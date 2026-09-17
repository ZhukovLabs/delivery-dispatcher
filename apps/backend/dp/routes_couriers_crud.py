"""Ручки курьеров: CRUD (создание/правка/удаление, перенос между точками)."""
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
