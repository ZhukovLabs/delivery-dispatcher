"""Выдача заказов курьеру, возврат заказа/курьера, синк маршрута в Telegram."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta

import requests
from fastapi import APIRouter

from .core import (CFG, MAX_POINTS, PALETTE, STATE, STATUSES, _approach_map,
                   _archive_order, _attach_geometry, _bump, _courier_plan,
                   _courier_speed, _db, _db_path, _db_lock, _deadline_rel_min,
                   _check_user_contact, _depot_view, _esc, _eta_pass, _ev,
                   _flip_return_route, _history_period, _courier_day_stats,
                   _home_point, _HOURLY_TRAFFIC, _invalidate_plan, _me,
                   _my_point, _now, _obj_point, _payload, _persist_couriers,
                   _persist_meta, _persist_orders, _plan_for, _plural,
                   _plans_lock, _tg_callback, _tg_handle_update, _tg_send,
                   _valid_latlng, build_time_matrix, haversine_km, log,
                   routing_geometry, solve_plan, _simplify_poly)
from .geocode import reverse_geocode
from .shims import _json, flaskish, jsonify, request, send_file, session
from .routes_plan import _patch_plan_after_assign, _tg_route_message

r = APIRouter()
_solving_lock = threading.Lock()

@r.post("/api/orders/assign")
@flaskish
def assign_orders():
    """Выдать заказы курьеру: статус ready -> out (в развозке)."""
    data = _json()
    oids = data.get("order_ids") or []
    cid = data.get("courier_id")
    if not isinstance(oids, list) or not oids:
        return jsonify({"error": "Не указаны заказы"}), 400
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if courier["status"] == "off":
        return jsonify({"error": f"{courier['name']} недоступен: включите его статусом"}), 400
    courier_pid = _home_point(courier)["id"]
    if courier_pid != _my_point():
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    bad = [o for o in STATE["orders"]
           if o["id"] in oids and (o.get("status") or "ready") == "ready"
           and _obj_point(o) != courier_pid]
    if bad:
        pt = next((p["name"] for p in STATE.get("points", [])
                   if p["id"] == _obj_point(bad[0])), "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — выдать может только "
                                 f"курьер этой точки"}), 400
    now = _now().isoformat(timespec="seconds")
    # снимаем адреса/координаты/ETA выданных стопов ДО патча плана —
    # для авто-сообщения курьеру (с кнопками маршрута в Яндекс Картах)
    me = _me()
    oid_set = set(oids)
    by_oid = {o["id"]: o for o in STATE["orders"]}

    def _stop_snapshot(s):
        o = by_oid.get(s.get("order_id") or "")
        return {"address": s.get("address") or (o or {}).get("address", ""),
                "eta_clock": s.get("eta_clock"),
                "lat": (o or {}).get("lat"), "lng": (o or {}).get("lng")}

    route = next((r for r in (_courier_plan(courier) or {}).get("routes", [])
                  if r["courier_id"] == cid), None)
    given_stops = ([_stop_snapshot(s)
                    for tr in (route or {}).get("trips", [])
                    for s in tr["stops"] if s["order_id"] in oid_set]) if route else []
    if not given_stops:  # выдача мимо плана — хотя бы адреса
        given_stops = [{"address": o["address"], "eta_clock": None,
                        "lat": o.get("lat"), "lng": o.get("lng")}
                       for o in STATE["orders"] if o["id"] in oid_set]
    given = 0
    # атомарно: две быстрые выдачи (например, двум курьерам подряд) иначе
    # перетирают друг другу правки одного и того же плана
    with _plans_lock:
        for o in STATE["orders"]:
            if o["id"] in oids and (o.get("status") or "ready") == "ready":
                o["status"] = "out"
                o["assigned"] = cid
                o["out_at"] = now
                o["pin"] = ""  # выдан — закрепление больше не нужно
                given += 1
        if not given:
            return jsonify({"error": "Заказы уже выданы или не найдены"}), 400
        _persist_orders()
        if not _patch_plan_after_assign(oids, cid=cid):
            _invalidate_plan(pid=courier_pid)
    log.info("assign: %d заказ(ов) -> %s", given, courier["name"])
    _ev("disp", f"выдал {given} заказ(ов) → {courier['name']}")

    # маршрут уходит курьеру в Telegram автоматически — раньше была
    # отдельная кнопка; шлём в фоне, выдача не ждёт сеть Telegram
    chat = (courier.get("tg_chat_id") or "").strip()
    if chat and CFG["tg_bot_token"] and given_stops:
        home = _home_point(courier)

        def _tg_assign():
            payload = _tg_route_message(
                courier["name"], given_stops, me,
                origin=f"{home['lat']},{home['lng']}"
                if home.get("lat") is not None else None,
                pos=STATE["tg_pos"].get(chat))
            payload["chat_id"] = chat
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
                    json=payload, timeout=10)
                data = resp.json()
                if not data.get("ok"):
                    log.warning("assign tg: не ушло курьеру %s: %s",
                                courier["name"], resp.text[:200])
                else:
                    # запоминаем сообщение: при возврате заказа отредактируем его
                    STATE.setdefault("tg_assign", {})[cid] = {
                        "chat": chat, "mid": data.get("result", {}).get("message_id")}
                    log.info("telegram sent (assign): %s", courier["name"])
            except (requests.RequestException, ValueError) as e:
                log.warning("assign tg: %s", e)
        threading.Thread(target=_tg_assign, daemon=True).start()
    return _payload()


def _tg_sync_route(cid, warn=None):
    """Живое TG-сообщение курьера догоняет реальность: пересобрать маршрут
    по оставшимся заказам (ссылки/кнопки обновятся) и предупредить курьера.

    Фоном — запрос диспетчера сеть Telegram не ждёт. warn=None — только
    правка сообщения (без отдельного предупреждения).
    """
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    chat = (courier or {}).get("tg_chat_id") or ""
    if not (courier and chat and CFG["tg_bot_token"]):
        return
    ref = STATE.get("tg_assign", {}).get(cid)
    if not warn and not ref:
        return
    me = _me()
    home = _home_point(courier)
    origin = (f"{home['lat']},{home['lng']}"
              if home.get("lat") is not None else None)
    pos = STATE["tg_pos"].get(chat)

    def _job():
        if warn:
            _tg_send(chat, warn)
        if not ref:
            return
        stops = [{"address": o["address"], "eta_clock": None,
                  "lat": o.get("lat"), "lng": o.get("lng")}
                 for o in STATE["orders"]
                 if o.get("assigned") == cid and (o.get("status") or "ready") == "out"]
        if stops:
            payload = _tg_route_message(courier["name"], stops, me,
                                        origin=origin, pos=pos)
        else:
            payload = {"text": f"📦 {_esc(courier['name'])}: все заказы"
                               " сняты с развозки", "parse_mode": "HTML"}
        payload.update({"chat_id": ref["chat"], "message_id": ref["mid"]})
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{CFG['tg_bot_token']}"
                "/editMessageText", json=payload, timeout=10)
            desc = resp.json().get("description", "")
            if "not modified" not in desc.lower() and not resp.json().get("ok"):
                log.warning("tg sync: не отредактировано (%s): %s",
                            courier["name"], desc[:200])
            if "message to edit not found" in desc.lower():
                STATE.get("tg_assign", {}).pop(cid, None)
        except (requests.RequestException, ValueError) as e:
            log.warning("tg sync: %s", e)

    threading.Thread(target=_job, daemon=True).start()


@r.post("/api/orders/{oid}/return")
@flaskish
def return_order(oid):
    """Вернуть заказ из развозки в очередь готовых."""
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if (order.get("status") or "ready") != "out":
        return jsonify({"error": "Заказ не в развозке"}), 400
    cid = order.get("assigned") or ""
    addr = order.get("address") or oid
    order["status"] = "ready"
    order["assigned"] = ""
    order["out_at"] = ""
    _persist_orders()
    _invalidate_plan(pid=_obj_point(order))
    _ev("disp", f"вернул «{addr}» в очередь")
    # предупредить курьера и пересобрать сообщение с маршрутом
    _tg_sync_route(cid, warn=(
        f"⚠️ Заказ <b>{_esc(addr)}</b> вернули в очередь — он уйдёт"
        " другому курьеру или новому расчёту.\n"
        "Маршрут обновлён — откройте новый маршрут по той же кнопке."))
    return _payload()


@r.post("/api/couriers/{cid}/returned")
@flaskish
def courier_returned(cid):
    """Курьер вернулся на базу: все его развозимые заказы доставлены (факт)."""
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if _home_point(courier)["id"] != _my_point() and not _me()["is_admin"]:
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    delivered = 0
    with _plans_lock:  # атомарно с параллельными выдачами того же депо
        for o in STATE["orders"]:
            if o.get("assigned") == cid and o.get("status") == "out":
                _archive_order(o, "delivered", courier["name"], courier_id=cid)
                delivered += 1
        STATE["orders"] = [o for o in STATE["orders"]
                           if not (o.get("assigned") == cid and o.get("status") == "out")]
        courier["status"] = "base"
        courier["back_min"] = 0
        courier.pop("out_geom", None)  # развозка завершена — трассу больше не рисуем
        courier.pop("ret_geom", None)
        _persist_orders()
        _persist_couriers()
        _invalidate_plan(courier_id=cid)
    log.info("courier returned: %s, доставлено %d", courier["name"], delivered)
    _ev("cour", f"{courier['name']} вернулся на базу" +
        (f" — доставлено {delivered}" if delivered else ""))
    return _payload()


