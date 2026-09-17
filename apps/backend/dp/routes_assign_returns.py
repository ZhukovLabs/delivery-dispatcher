"""Возврат заказа из развозки и возврат курьера на базу."""
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
from .routes_assign_helpers import _tg_sync_route

r = APIRouter()

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
