"""План развозки: патч после выдачи, закрепление, подмога, сообщение курьеру."""
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
from .routes_solve import _begin_solving
from .routes_plan_edit import _retime_route
from .routes_retiming import _retiming_matrix

r = APIRouter()
_solving_lock = threading.Lock()

def _patch_plan_after_assign(oids, cid=None):
    """Убрать выданные заказы из плана и пересчитать ETA оставшихся курьеров.

    План остаётся рабочим: диспетчер сразу выдаёт маршруты следующим курьерам,
    не дожидаясь полного пересчёта. Матрица берётся из кэша (набор точек тот же,
    что при расчёте), ETA пересчитываются от текущего момента.
    """
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    plan = _courier_plan(courier) if courier else _plan_for(_my_point())
    pid = (_home_point(courier)["id"] if courier and _home_point(courier)
           else _my_point())
    if not plan or not plan.get("routes"):
        return False
    oidset = set(oids)
    touched = set()
    # порядок выданных остановок — в сами заказы, а дорожную геометрию выданной
    # части трипа (до последней выданной остановки) сохраняем курьеру: по ней
    # карта рисует активную развозку по дорогам, когда маршрут ушёл из плана
    order_by_id = {o["id"]: o for o in STATE["orders"]}
    k = 0
    out_geom = []
    if courier:
        courier.pop("ret_geom", None)  # новая выдача отменяет возврат
        for r in plan["routes"]:
            if r["courier_id"] != courier["id"]:
                continue
            for tr in r.get("trips", []):
                geom = tr.get("geometry") or []
                last_idx = -1
                for s in tr["stops"]:
                    if s["order_id"] not in oidset:
                        continue
                    o = order_by_id.get(s["order_id"])
                    if o is not None:
                        o["out_no"] = k
                        k += 1
                    if geom:
                        gi = min(range(len(geom)),
                                 key=lambda i: (geom[i][0] - s["lat"]) ** 2
                                               + (geom[i][1] - s["lng"]) ** 2)
                        last_idx = max(last_idx, gi)
                if last_idx >= 0:
                    out_geom.extend(geom[:last_idx + 1])
    if out_geom:
        # стыки упрощённых сегментов дают дубли точек — ужимаем слитую трассу
        courier["out_geom"] = _simplify_poly(
            (courier.get("out_geom") or []) + out_geom)
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            before = len(tr["stops"])
            tr["stops"] = [s for s in tr["stops"] if s["order_id"] not in oidset]
            if len(tr["stops"]) != before:
                touched.add(r["courier_id"])
        r["trips"] = [tr for tr in r.get("trips", []) if tr["stops"]]
    if not touched:
        return False
    now = _now()
    try:
        # матрица и ретайминг — по ВСЕМ маршрутам плана: ETA нетронутых
        # курьеров тоже должны быть «от сейчас», а не от момента расчёта
        matrix, node, appr_home, home_of = _retiming_matrix(plan, pid,
                                                            plan["routes"])
        for r in plan["routes"]:
            h = home_of[r["courier_id"]]
            _retime_route(r, matrix, node, STATE["settings"], now, appr_home[h], h)
        # задержки плана перепривязаны к «сейчас» — фиксируем новый якорь,
        # иначе _refresh_plan_delays досдвигал бы их ещё раз от solved_at
        plan["anchored_at"] = now.isoformat(timespec="seconds")
    except Exception:
        log.warning("plan retime after assign failed, агрегаты пересобраны без матрицы")
        for r in plan["routes"]:
            r["stops"] = [s for tr in r.get("trips", []) for s in tr["stops"]]
            r["count"] = len(r["stops"])
    plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
    if not plan["routes"]:
        # всё выдано: мёртвый пустой план никому не нужен, а фоновый
        # пересчёт подхватит заказы, которые могли остаться вне маршрутов
        STATE["plans"].pop(pid, None)
        _persist_meta()
        _invalidate_plan(pid=pid)
        return True
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» больше не соответствуют плану
    plan.pop("moved", None)
    _persist_meta()
    _bump()  # выдача видна всем консолям сразу
    return True


@r.post("/api/plan/pin")
@flaskish
def plan_pin():
    """Закрепить готовый заказ за курьером и пересчитать план.

    Заказ остаётся ready («в развозку» уходит только по явной выдаче),
    но при расчёте его повезёт именно этот курьер.
    """
    data = _json()
    oid, cid = data.get("order_id"), data.get("courier_id")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if (order.get("status") or "ready") != "ready":
        return jsonify({"error": "Заказ уже в развозке"}), 400
    if courier["status"] == "off":
        return jsonify({"error": f"{courier['name']} недоступен: включите его статусом"}), 400
    opid = _obj_point(order)
    if opid != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if opid and opid != _home_point(courier)["id"]:
        pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == opid),
                  "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — закрепить можно только "
                                 f"за курьером этой точки"}), 400
    order["pin"] = cid
    _persist_orders()
    if not _begin_solving(opid):
        order["pin"] = ""
        _persist_orders()
        return jsonify({"error": "Расчёт развозки уже идёт — подождите окончания"}), 409
    _bump()  # остальные диспетчеры депо сразу видят «идёт расчёт»
    try:
        try:
            plan = solve_plan(point_id=opid)
        except (ValueError, RuntimeError) as e:
            order["pin"] = ""
            _persist_orders()
            return jsonify({"error": str(e)}), 400
        # закрепление должно попасть в план; если курьер не смог взять заказ
        # (лимиты заказов/заездов исчерпаны) — откатываем и говорим прямо,
        # раньше pin молча оставался, а заказ выпадал из маршрутов
        if not any(s["order_id"] == oid
                   for r in (plan or {}).get("routes", [])
                   for s in r.get("stops", [])):
            order["pin"] = ""
            _persist_orders()
            return jsonify({"error": f"«{courier['name']}» не может взять заказ "
                                     "(лимит заказов/заездов исчерпан) — закрепление "
                                     "отменено"}), 400
        log.info("pin: %s -> %s", oid, courier["name"])
        # флаг снят до сборки ответа: он несёт solving=false (см. /api/solve)
        STATE["solving"][opid] = False
        return _payload()
    finally:
        if STATE["solving"].get(opid):
            STATE["solving"][opid] = False
            _bump()


@r.post("/api/plan/help")
@flaskish
def plan_help():
    """Разовая помощь: курьер подъезжает к чужой точке и берёт один заказ.

    Точка и статус курьера НЕ меняются — только этот расчёт плана.
    """
    data = _json()
    cid, pid = data.get("courier_id"), data.get("point_id")
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    point = next((p for p in STATE.get("points") or [] if p["id"] == pid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if not point:
        return jsonify({"error": "Точка выдачи не найдена"}), 404
    first_pid = STATE["points"][0]["id"] if STATE.get("points") else ""
    if not any((o.get("status") or "ready") == "ready"
               and (o.get("point_id") or first_pid) == pid
               for o in STATE["orders"]):
        return jsonify({"error": "У этой точки нет готовых заказов — помогать не с чем"}), 400
    helpers = {cid: pid} if _home_point(courier)["id"] != pid else {}
    if not _begin_solving(pid):
        return jsonify({"error": "Расчёт развозки уже идёт — подождите окончания"}), 409
    _bump()  # остальные диспетчеры депо сразу видят «идёт расчёт»
    try:
        try:
            solve_plan(helpers=helpers, point_id=pid)
        except (ValueError, RuntimeError) as e:
            return jsonify({"error": str(e)}), 400
        log.info("plan help: %s -> точка %s", courier["name"], point["name"])
        # флаг снят до сборки ответа: он несёт solving=false (см. /api/solve)
        STATE["solving"][pid] = False
        return _payload()
    finally:
        if STATE["solving"].get(pid):
            STATE["solving"][pid] = False
            _bump()


_PAY_GEO_FRESH_S = 15 * 60  # живая геопозиция старше 15 минут — уже не «текущая»


def _tg_route_message(courier_name, stops, me, origin=None, pos=None):
    """Текст+клавиатура TG-сообщения о выдаче: адреса с ETA, у каждого —
    ссылки «Маршрут: Яндекс | Google» с новой строки, внизу — кнопки
    «Весь маршрут» друг под другом.

    Стартовая точка всех маршрутов — текущая геопозиция курьера (pos,
    живая локация от бота, не старше 15 минут); если её нет — точка
    выдачи origin. Явная точка надёжнее «моего местоположения» карты:
    браузер внутри Telegram геолокацию не отдаёт. Используется и при
    отправке (assign), и при редактировании (return)."""

    start = None
    if pos and pos.get("lat") is not None and \
            time.time() - pos.get("ts", 0) <= _PAY_GEO_FRESH_S:
        start = f"{pos['lat']},{pos['lng']}"
    if not start:
        start = origin or "~"

    def _ya_link(sp):
        if sp.get("lat") is None or sp.get("lng") is None:
            return None
        return (f"https://yandex.ru/maps/?rtext={start}~{sp['lat']},{sp['lng']}"
                "&rtt=auto")

    def _gg_link(sp):
        if sp.get("lat") is None or sp.get("lng") is None:
            return None
        return (f"https://www.google.com/maps/dir/?api=1"
                + (f"&origin={start}" if start != "~" else "")
                + f"&destination={sp['lat']},{sp['lng']}&travelmode=driving")
    z_word = _plural(len(stops), ("заказ", "заказа", "заказов"))
    lines = [f"🛵 <b>{_esc(courier_name)}, в развозку</b>: {len(stops)} {z_word}"]
    for i, s in enumerate(stops, start=1):
        head = f"{i}. {_esc(s['address'])}"
        if s.get("eta_clock"):
            head += f" · ≈{s['eta_clock']}"
        lines.append(head)
        ya, gg = _ya_link(s), _gg_link(s)
        if ya and gg:
            lines.append(f"    Маршрут: <a href=\"{ya}\">Яндекс</a>"
                         f" | <a href=\"{gg}\">Google</a>")
    lines.append("Время приблизительное, следите за сообщениями.")
    if (me or {}).get("name") and (me or {}).get("phone"):
        lines.append(f"\nЕсть вопросы? - {_esc(me['name'])}, {me['phone']}")
    payload = {"text": "\n".join(lines), "parse_mode": "HTML"}
    pts = [f"{s['lat']},{s['lng']}" for s in stops
           if s.get("lat") is not None and s.get("lng") is not None]
    if len(pts) >= 2:  # маршрут строим минимум по двум точкам
        gg = ("https://www.google.com/maps/dir/?api=1"
              + (f"&origin={start}" if start != "~" else "")
              + "&destination=" + pts[min(len(pts), 10) - 1]
              + "&waypoints=" + "%7C".join(pts[:min(len(pts), 10) - 1])
              + "&travelmode=driving")
        kb = [[{"text": "Яндекс | Весь маршрут",
                "url": "https://yandex.ru/maps/?rtext="
                       + "~".join([start] + pts[:10]) + "&rtt=auto"}],
              [{"text": "Google | Весь маршрут", "url": gg}]]
        payload["reply_markup"] = {"inline_keyboard": kb}
    return payload


