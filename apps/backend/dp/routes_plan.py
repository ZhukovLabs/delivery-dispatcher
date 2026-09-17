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
from .routes_plan_helpers import _patch_plan_after_assign
from .routes_plan_msg import _PAY_GEO_FRESH_S, _tg_route_message

r = APIRouter()
_solving_lock = threading.Lock()


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


