"""Ручки чтения состояния и справочника точек выдачи (depot/points)."""
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

@r.get("/api/state")
@flaskish
def get_state():
    return _payload()


@r.get("/api/route")
@flaskish
def get_route():
    """Геометрия маршрута по дорогам для отрисовки на карте (клик по курьеру).
    coords=lat,lng;lat,lng... — 2..50 точек. Каскад тот же, что у расчётов:
    локальный OSRM → FOSSGIS → демо → ORS."""
    coords = (request.args.get("coords") or "").strip()
    pts = []
    try:
        for pair in coords.split(";"):
            la, ln = pair.split(",")
            pts.append({"lat": float(la), "lng": float(ln)})
    except ValueError:
        return jsonify({"error": "coords=lat,lng;lat,lng..."}), 400
    if not 2 <= len(pts) <= 50 or not all(_valid_latlng(p["lat"], p["lng"]) for p in pts):
        return jsonify({"error": "нужно 2..50 корректных точек"}), 400
    geom = routing_geometry(pts)
    if not geom:
        return jsonify({"error": "роутер недоступен"}), 502
    return jsonify({"geometry": geom})


def _points_changed(persist_couriers=False):
    """После любой правки точек выдачи: обновить совместимый вид депо,
    сохранить мета (или курьеров) и сбросить рассчитанный план."""
    STATE["depot"] = _depot_view()
    if persist_couriers:
        _persist_couriers()
    else:
        _persist_meta()
    # план не выбрасываем: времена/геометрия устарели — помечаем «устарел»,
    # маршруты остаются на экране до пересчёта (раньше любая правка точки
    # гасила все карточки курьеров)
    _invalidate_plan()


@r.post("/api/depot")
@flaskish
def set_depot():
    """Совместимость со старым фронтом: двигает ПЕРВУЮ точку выдачи."""
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны координаты точки (lat, lng)"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    if not STATE.get("points"):
        return jsonify({"error": "Список точек пуст"}), 400
    address = (data.get("address") or "").strip() or reverse_geocode(lat, lng) or "Точка выдачи"
    STATE["points"][0].update({"address": address, "lat": lat, "lng": lng})
    _points_changed()
    return _payload()


@r.post("/api/points")
@flaskish
def add_point():
    """Добавить место выдачи заказов (точка, откуда курьеры забирают заказы)."""
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны координаты точки (lat, lng)"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    if len(STATE.get("points") or []) >= MAX_POINTS:
        return jsonify({"error": f"Максимум {MAX_POINTS} точек выдачи"}), 400
    address = (data.get("address") or "").strip() or reverse_geocode(lat, lng) or "Точка выдачи"
    name = (data.get("name") or "").strip() or f"Точка {len(STATE['points']) + 1}"
    STATE["points"].append({"id": uuid.uuid4().hex[:8], "name": name[:40],
                            "address": address, "lat": lat, "lng": lng})
    _points_changed()
    return _payload()


@r.post("/api/points/{pid}")
@flaskish
def upd_point(pid):
    """Переименовать ({name}) или передвинуть ({address,lat,lng}) точку выдачи."""
    data = _json()
    p = next((x for x in STATE.get("points") or [] if x["id"] == pid), None)
    if not p:
        return jsonify({"error": "Точка не найдена"}), 404
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Имя точки не может быть пустым"}), 400
        p["name"] = name[:40]
    if data.get("lat") is not None or data.get("lng") is not None:
        try:
            lat, lng = float(data["lat"]), float(data["lng"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Нужны обе координаты (lat, lng)"}), 400
        if not _valid_latlng(lat, lng):
            return jsonify({"error": "Координаты вне диапазона"}), 400
        address = (data.get("address") or "").strip() or p["address"] \
            or reverse_geocode(lat, lng) or "Точка выдачи"
        p.update({"address": address, "lat": lat, "lng": lng})
    _points_changed()
    return _payload()


@r.delete("/api/points/{pid}")
@flaskish
def del_point(pid):
    pts = STATE.get("points") or []
    p = next((x for x in pts if x["id"] == pid), None)
    if not p:
        return jsonify({"error": "Точка не найдена"}), 404
    if len(pts) == 1:
        return jsonify({"error": "Должна остаться хотя бы одна точка выдачи"}), 400
    attached = [c["name"] for c in STATE["couriers"] if c.get("point_id") == pid]
    if attached:
        return jsonify({"error": "К точке привязаны курьеры: "
                                 + ", ".join(attached)
                                 + ". Сначала перевесьте их на другую точку"}), 400
    pts.remove(p)
    _points_changed()
    return _payload()

