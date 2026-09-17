"""Ручки курьеров: симуляторы бота для демо без Telegram."""
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
