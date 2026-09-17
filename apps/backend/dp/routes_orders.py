"""Ручки заказов (CRUD) и пересчёт ETA-баз после правок."""
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
from .routes_plan import _patch_plan_after_assign
from .routes_assign import _tg_sync_route
from .routes_retiming import _retiming_bases, _retiming_matrix

r = APIRouter()
_solving_lock = threading.Lock()

@r.post("/api/orders")
@flaskish
def add_order():
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Укажите точку заказа на карте или через поиск"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    deadline = (data.get("deadline") or "").strip()
    if deadline and not _deadline_rel_min(deadline, 0) and deadline != "00:00":
        return jsonify({"error": "Дедлайн должен быть в формате ЧЧ:ММ"}), 400
    oid = uuid.uuid4().hex[:8]
    # заказ создаётся в депо диспетчера (селектор «Место работы»)
    pid = _my_point() or (data.get("point_id") or "").strip()
    pids = {p["id"] for p in STATE.get("points") or []}
    if pid not in pids:
        pid = (STATE["points"][0]["id"] if STATE.get("points") else "")
    STATE["orders"].append({
        "id": oid,
        "address": (data.get("address") or "").strip() or reverse_geocode(lat, lng) or f"Заказ {oid[:4]}",
        "lat": lat, "lng": lng,
        "created_at": _now().isoformat(timespec="seconds"),
        "prio": 1 if data.get("prio") else 0,
        "deadline": deadline if _deadline_rel_min(deadline, 0) is not None else "",
        "point_id": pid,
        "status": "ready",
        "assigned": "",
    })
    _persist_orders()
    _invalidate_plan()
    _ev("disp", "добавил заказ «" +
        ((data.get("address") or "").strip() or f"без адреса ({oid[:4]})") + "»")
    return _payload()


@r.patch("/api/orders/{oid}")
@flaskish
def patch_order(oid):
    """Переключение приоритета или дедлайна заказа."""
    data = _json()
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if "prio" in data:
        order["prio"] = 1 if data.get("prio") else 0
    if "deadline" in data:
        deadline = (data.get("deadline") or "").strip()
        if deadline and _deadline_rel_min(deadline, 0) is None:
            return jsonify({"error": "Дедлайн должен быть в формате ЧЧ:ММ"}), 400
        order["deadline"] = deadline
    _persist_orders()
    _invalidate_plan()
    return _payload()


@r.delete("/api/orders/{oid}")
@flaskish
def del_order(oid):
    """Отмена заказа (из очереди или из развозки) с записью в историю."""
    body = _json()
    outcome = body.get("outcome") if body.get("outcome") in ("delivered", "cancelled") \
        else "cancelled"
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if order:
        if _obj_point(order) != _my_point():
            return jsonify({"error": "Заказ другого депо"}), 403
        courier_name, courier_id = "", order.get("assigned") or ""
        if order.get("assigned"):
            c = next((c for c in STATE["couriers"] if c["id"] == order["assigned"]), None)
            courier_name = c["name"] if c else order["assigned"]
        elif outcome == "delivered":
            for r in (_plan_for(_obj_point(order)) or {}).get("routes", []):
                if any(s["order_id"] == oid for s in r["stops"]):
                    courier_name = r["courier_name"]
                    courier_id = courier_id or r.get("courier_id") or ""
                    break
        opid = _obj_point(order)
        _archive_order(order, outcome, courier_name, courier_id)
        _ev("disp", ("закрыл как доставленный «" if outcome == "delivered"
                     else "отменил «") + (order.get("address") or oid) + "»")
    else:
        opid = None
    # план не выбрасываем: убираем стоп из маршрутов хирургически, ПОКА заказ
    # ещё в STATE — matrix_ctx плана остаётся полным, матрица берётся из кэша
    # без похода в сеть; остальные курьеры остаются на экране с обновлёнными
    # ETA (раньше отмена одного заказа гасила ВСЕ карточки развозки)
    patched = _patch_plan_after_assign([oid])
    STATE["orders"] = [o for o in STATE["orders"] if o["id"] != oid]
    # доставлен последний заказ развозки — курьер едет домой по улицам
    if outcome == "delivered" and courier_id:
        _flip_return_route(courier_id)
    _persist_orders()
    if not patched:
        _invalidate_plan(pid=opid)
    # курьер нёс этот заказ — предупредить и пересобрать его TG-маршрут
    if order and courier_id and (order.get("status") or "ready") == "out":
        _tg_sync_route(courier_id, warn=(
            f"⚠️ Заказ <b>{_esc(order.get('address') or oid)}</b> "
            + ("отменил диспетчер." if outcome == "cancelled"
               else "отмечён доставленным диспетчером.")
            + "\nМаршрут обновлён — откройте новый маршрут по той же кнопке."))
    return _payload()
