"""Уведомления курьеру и админам, профиль."""
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

# ---------- Telegram-уведомление курьеру (заготовка) ----------

@r.post("/api/notify/courier/{cid}")
@flaskish
def notify_courier(cid):
    """Отправка маршрута курьеру в Telegram (нужны токен бота и chat_id)."""
    me = _me()
    if not CFG["tg_bot_token"]:
        return jsonify({"error": "tg_bot_token не задан в config.ini"}), 400
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    route = next((r for r in (_courier_plan(courier) or {}).get("routes", [])
                  if r["courier_id"] == cid), None)
    if not courier or not route:
        return jsonify({"error": "Курьер или маршрут не найден"}), 404
    chat = (courier.get("tg_chat_id") or "").strip()
    if not chat:
        return jsonify({"error": f"У курьера {courier['name']} не указан Telegram chat_id"}), 400
    z_word = _plural(route["count"], ("заказ", "заказа", "заказов"))
    lines = [f"🛵 <b>{_esc(route['courier_name'])}, маршрут на смену</b>: "
             f"{route['count']} {z_word}"]
    for ti, tr in enumerate(route["trips"], start=1):
        if len(route["trips"]) > 1:
            lines.append(f"Заезд {ti}: старт ≈{tr['start_clock']}")
        for i, s in enumerate(tr["stops"], start=1):
            lines.append(f"{i}. {_esc(s['address'])} · ≈{s['eta_clock']}")
    lines.append("Время приблизительное, следите за сообщениями.")
    if (me or {}).get("name") and (me or {}).get("phone"):
        lines.append(f"\nЕсть вопросы? - {_esc(me['name'])}, {me['phone']}")
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines), "parse_mode": "HTML"}, timeout=10)
        data = resp.json()
    except requests.RequestException as e:
        return jsonify({"error": f"Telegram недоступен: {e}"}), 502
    if not data.get("ok"):
        return jsonify({"error": f"Telegram: {data.get('description', 'ошибка')}"}), 400
    log.info("telegram sent: %s (%s)", courier["name"], cid)
    return jsonify({"ok": True})


@r.post("/api/profile")
@flaskish
def api_profile():
    """Имя и телефон текущего диспетчера (для подписи в сообщениях курьерам)."""
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    data = _json()
    try:
        name, phone = _check_user_contact(data.get("name"), data.get("phone"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    with _db_lock, _db() as c:
        c.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, me["id"]))
    log.info("profile updated: %s -> %s / %s", me["email"], name, phone)
    _bump()
    return _payload()


@r.post("/api/notify/tg")
@flaskish
def notify_tg():
    """Произвольное сообщение от имени бота привязанному пользователю (админ)."""
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    if not me["is_admin"]:
        return jsonify({"error": "Только администратор может отправлять сообщения"}), 403
    if not CFG["tg_bot_token"]:
        return jsonify({"error": "tg_bot_token не задан в config.ini"}), 400
    data = request.get_json(silent=True) or {}
    chat = str(data.get("chat_id") or "").strip()
    text = str(data.get("text") or "").strip()
    if not chat or not text:
        return jsonify({"error": "Нужны chat_id и текст"}), 400
    if len(text) > 3500:
        return jsonify({"error": "Сообщение слишком длинное (макс. 3500 символов)"}), 400
    if not me.get("name") or not me.get("phone"):
        return jsonify({"error": "Заполните имя и телефон в профиле: шестерёнка → Профиль"}), 400
    text = f"{text}\n\nЕсть вопросы? - {me['name']}, {me['phone']}"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
            json={"chat_id": chat, "text": text}, timeout=10)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        return jsonify({"error": f"Telegram недоступен: {e}"}), 502
    if not data.get("ok"):
        return jsonify({"error": f"Telegram: {data.get('description', 'ошибка')}"}), 400
    return jsonify({"ok": True})


# ---------- статистика недели ----------
