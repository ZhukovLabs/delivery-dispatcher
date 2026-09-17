"""Ручки курьеров: CRUD, привязка Telegram, симуляция бота."""
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

r = APIRouter()
_solving_lock = threading.Lock()

@r.post("/api/couriers/{cid}/point")
@flaskish
def set_courier_point(cid):
    """Перекинуть курьера на другое место выдачи."""
    data = _json()
    pid = (data.get("point_id") or "").strip()
    if not any(x["id"] == pid for x in STATE.get("points") or []):
        return jsonify({"error": "Точка выдачи не найдена"}), 404
    c = next((x for x in STATE["couriers"] if x["id"] == cid), None)
    if not c:
        return jsonify({"error": "Курьер не найден"}), 404
    if c.get("point_id") == pid:
        return _payload()
    c["point_id"] = pid
    STATE["depot"] = _depot_view()
    _persist_couriers()
    # его маршруты убираем из планов точечно (мог быть помощником в чужом
    # депо), планы остальных курьеров сохраняются с пометкой «устарел»
    _invalidate_plan(courier_id=cid)
    return _payload()


@r.post("/api/couriers")
@flaskish
def add_courier():
    name = (_json().get("name") or "").strip()
    if not name:
        return jsonify({"error": "Введите имя курьера"}), 400
    color = PALETTE[STATE["color_seq"] % len(PALETTE)]
    STATE["color_seq"] += 1
    STATE["couriers"].append({"id": uuid.uuid4().hex[:8], "name": name,
                              "status": "base", "color": color, "back_min": 15,
                              "tg_chat_id": "",
                              "point_id": (STATE.get("points") or [{}])[0].get("id", "")})
    _persist_couriers(), _persist_meta()
    _invalidate_plan()
    return _payload()


@r.patch("/api/couriers/{cid}")
@flaskish
def upd_courier(cid):
    data = _json()
    for c in STATE["couriers"]:
        if c["id"] == cid:
            if _home_point(c)["id"] != _my_point() and not _me()["is_admin"]:
                return jsonify({"error": "Курьер другого депо — управлять может "
                                         "только диспетчер его точки"}), 403
            if "name" in data and data["name"].strip():
                c["name"] = data["name"].strip()
            if data.get("status") in STATUSES:
                c["status"] = data["status"]
                if c["status"] == "off":
                    # выключенный курьер не участвует в расчётах: его
                    # закрепления остались бы вечными unassigned
                    for o in STATE["orders"]:
                        if o.get("pin") == cid:
                            o["pin"] = ""
                    _persist_orders()
            if "back_min" in data:
                try:
                    c["back_min"] = min(480, max(0, int(data["back_min"])))
                except (TypeError, ValueError):
                    pass
            if "tg_chat_id" in data:
                new_tg = str(data["tg_chat_id"]).strip()[:64]
                if new_tg and not new_tg.isdigit():
                    return jsonify({"error": "ID Telegram должен быть числом"}), 400
                c["tg_chat_id"] = new_tg
            _persist_couriers()
            # курьер может быть помощником в чужом плане — но его маршрут
            # убираем точечно, чужие маршруты остаются с пометкой «устарел»
            _invalidate_plan(courier_id=cid)
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@r.post("/api/couriers/{cid}/bind")
@flaskish
def bind_courier(cid):
    """Привязка курьера к Telegram-пользователю (по chat_id из бота)."""
    data = _json()
    chat_id = str(data.get("chat_id") or "").strip()
    if not chat_id.isdigit():
        return jsonify({"error": "ID Telegram должен быть числом"}), 400
    for c in STATE["couriers"]:
        if c["id"] == cid:
            if _home_point(c)["id"] != _my_point() and not _me()["is_admin"]:
                return jsonify({"error": "Курьер другого депо — управлять может "
                                         "только диспетчер его точки"}), 403
            # гео не должна утекать к двум курьерам сразу
            for other in STATE["couriers"]:
                if other is not c and other.get("tg_chat_id") == chat_id:
                    other["tg_chat_id"] = ""
                    other["tg_login"] = ""
            c["tg_chat_id"] = chat_id
            c["tg_login"] = (data.get("login")
                             or STATE["tg_seen"].get(chat_id, {}).get("login")
                             or "").strip()[:64]
            _persist_couriers()
            # приветствие — в фоне: сеть Telegram не должна держать запрос
            # диспетчера (таймаут 5 с × каскад — ощутимо на живом боте)
            msg = (f"Готово! Вы привязаны: курьер «{_esc(c['name'])}».\n\n"
                   "Включите <b>живую геолокацию</b>:\n"
                   "скрепка → «Геолокация» → «Поделиться моей геолокацией» → "
                   "время <b>«Пока не отключу»</b>.\n\n"
                   "Диспетчер увидит вас на карте.")
            threading.Thread(target=_tg_send, args=(chat_id, msg),
                             daemon=True).start()
            _bump()
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@r.post("/api/couriers/{cid}/unbind")
@flaskish
def unbind_courier(cid):
    for c in STATE["couriers"]:
        if c["id"] == cid:
            STATE["tg_pos"].pop(c.get("tg_chat_id") or "", None)
            c["tg_chat_id"] = ""
            c["tg_login"] = ""
            _persist_couriers()
            _bump()
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@r.post("/api/sim/geo")
@flaskish
def sim_geo():
    """Симулятор live-гео: подсунуть поллеру апдейт Telegram от курьера.

    Только администратор. Синтетический апдейт проходит тот же конвейер,
    что и настоящая геолокация (_tg_handle_update): сглаживание медианой,
    замер скорости, трекеры выдачи/доставки/авто-статусов, пинок картам.
    """
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор"}), 403
    data = _json()
    try:
        chat_id = str(int(str(data.get("chat_id"))))
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны chat_id (числом), lat, lng"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    u = {"edited_message": {
        "chat": {"id": int(chat_id)},
        "from": {"username": str(data.get("login") or f"sim_{chat_id[-4:]}")},
        "location": {"latitude": lat, "longitude": lng,
                     "live_period": 31536000,
                     "horizontal_accuracy": 12},
        "date": int(time.time())}}
    _tg_handle_update(u)
    return jsonify({"ok": True})


@r.post("/api/sim/tgcb")
@flaskish
def sim_tgcb():
    """Симулятор кнопок бота: «курьер нажал» инлайн-кнопку (для демо без TG).

    Только администратор. Пример: {"chat_id": 9100000, "data": "dlv:<oid>:y"}.
    Проходит через тот же _tg_callback, что и настоящие нажатия.
    """
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор"}), 403
    data = _json()
    try:
        chat_id = str(int(str(data.get("chat_id"))))
        cb_data = str(data["data"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны chat_id (числом) и data"}), 400
    _tg_callback({"id": f"sim{int(time.time() * 1000) % 10 ** 9}",
                  "from": {"username": str(data.get("login") or "sim")},
                  "message": {"chat": {"id": int(chat_id)}, "message_id": 0},
                  "data": cb_data})
    return jsonify({"ok": True})


@r.post("/api/sim/tgtext")
@flaskish
def sim_tgtext():
    """Симулятор текста боту: «курьер написал» сообщение (для демо без TG).

    Только администратор. Пример: {"chat_id": 9100000, "text": "24.50"}.
    Проходит через тот же _tg_handle_update, что и настоящие сообщения
    (в т.ч. флоу «сумма оплаты» после подтверждения доставки).
    """
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор"}), 403
    data = _json()
    try:
        chat_id = str(int(str(data.get("chat_id"))))
        text = str(data["text"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны chat_id (числом) и text"}), 400
    _tg_handle_update({"message": {
        "chat": {"id": int(chat_id)},
        "from": {"username": str(data.get("login") or "sim")},
        "text": text, "date": int(time.time())}})
    return jsonify({"ok": True})


@r.delete("/api/couriers/{cid}")
@flaskish
def del_courier(cid):
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if courier and _home_point(courier)["id"] != _my_point() \
            and not _me()["is_admin"]:
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    # его развозимые заказы возвращаются в очередь, чтобы не зависли;
    # закреплённые за ним — тоже: курьера больше нет, pin остался бы
    # в решателе пустым allowed и вечным unassigned
    for o in STATE["orders"]:
        if o.get("assigned") == cid and o.get("status") == "out":
            o["status"] = "ready"
            o["assigned"] = ""
            o["out_at"] = ""
        if o.get("pin") == cid:
            o["pin"] = ""
    STATE["couriers"] = [c for c in STATE["couriers"] if c["id"] != cid]
    _persist_orders()
    _persist_couriers()
    _invalidate_plan(courier_id=cid)
    return _payload()


