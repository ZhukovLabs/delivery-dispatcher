"""Ручки курьеров: привязка/отвязка Telegram."""
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
