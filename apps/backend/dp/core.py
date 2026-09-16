# -*- coding: utf-8 -*-
"""Ð¯Ð´Ñ€Ð¾ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€ÑÐºÐ¾Ð¹: ÐºÐ¾Ð½Ñ„Ð¸Ð³, ÑÐ¾ÑÑ‚Ð¾ÑÐ½Ð¸Ðµ, Ð‘Ð”, ÑÐºÐ¾Ñ€Ð¾ÑÑ‚Ð¸, ORS/OSRM, TG-Ð±Ð¾Ñ‚, payload.

ÐŸÐµÑ€ÐµÐ½ÐµÑÐµÐ½Ð¾ Ñ Flask-Ð¼Ð¾Ð½Ð¾Ð»Ð¸Ñ‚Ð° Ð±ÐµÐ· Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ñ Ð»Ð¾Ð³Ð¸ÐºÐ¸; Flask-Ð·Ð°Ð²Ð¸ÑÐ¸Ð¼Ð¾ÑÑ‚Ð¸
Ð·Ð°Ð¼ÐµÑ‰ÐµÐ½Ñ‹ ÑˆÐ¸Ð¼Ð°Ð¼Ð¸ dp.shims.
"""
import configparser
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

import requests
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .shims import g, jsonify, request, send_file, session

# ÐœÐ¸Ð½ÑÐº: UTC+3, Ð±ÐµÐ· Ð¿ÐµÑ€ÐµÑ…Ð¾Ð´Ð° Ð½Ð° Ð»ÐµÑ‚Ð½ÐµÐµ Ð²Ñ€ÐµÐ¼Ñ. Ð’ÑÐµ Â«Ð½Ð°ÑÑ‚ÐµÐ½Ð½Ñ‹ÐµÂ» Ð²Ñ€ÐµÐ¼ÐµÐ½Ð°
# (Ñ‡Ð°ÑÑ‹ Ð¿Ð»Ð°Ð½Ð°, Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ, Ð´ÐµÐ´Ð»Ð°Ð¹Ð½Ñ‹, ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ) Ð¿Ð¸ÑˆÐµÐ¼ Ð¿Ð¾ ÐœÐ¸Ð½ÑÐºÑƒ Ð½ÐµÐ·Ð°Ð²Ð¸ÑÐ¸Ð¼Ð¾
# Ð¾Ñ‚ Ñ‡Ð°ÑÐ¾Ð²Ð¾Ð³Ð¾ Ð¿Ð¾ÑÑÐ° ÑÐµÑ€Ð²ÐµÑ€Ð°. Ð—Ð½Ð°Ñ‡ÐµÐ½Ð¸Ñ Ð¾ÑÑ‚Ð°ÑŽÑ‚ÑÑ Ð½Ð°Ð¸Ð²Ð½Ñ‹Ð¼Ð¸ (Ð±ÐµÐ· Ð¾Ñ„Ñ„ÑÐµÑ‚Ð°),
# Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð½Ðµ Ð»Ð¾Ð¼Ð°Ñ‚ÑŒ ÑÑ€Ð°Ð²Ð½ÐµÐ½Ð¸Ñ Ñ ÑƒÐ¶Ðµ Ð·Ð°Ð¿Ð¸ÑÐ°Ð½Ð½Ñ‹Ð¼Ð¸ Ð´Ð°Ð½Ð½Ñ‹Ð¼Ð¸.
_MN = timezone(timedelta(hours=3))


def _now() -> datetime:
    return datetime.now(_MN).replace(tzinfo=None)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # apps/backend

from itsdangerous import URLSafeTimedSerializer

# BASE_DIR Ð·Ð°Ð´Ð°Ñ‘Ñ‚ÑÑ Ð² ÑˆÐ°Ð¿ÐºÐµ Ð¼Ð¾Ð´ÑƒÐ»Ñ (Ñ€Ð¾Ð´Ð¸Ñ‚ÐµÐ»ÑŒ dp/, Ñ‡Ñ‚Ð¾Ð±Ñ‹ config.ini/db Ð»ÐµÐ¶Ð°Ð»Ð¸ ÐºÐ°Ðº Ñ€Ð°Ð½ÑŒÑˆÐµ)


def _pick_data_dir():
    """ÐŸÐµÑ€Ð²Ñ‹Ð¹ ÐºÐ°Ñ‚Ð°Ð»Ð¾Ð³, Ð´Ð¾ÑÑ‚ÑƒÐ¿Ð½Ñ‹Ð¹ Ð½Ð° Ð·Ð°Ð¿Ð¸ÑÑŒ: Ñ€ÑÐ´Ð¾Ð¼ Ñ ÐºÐ¾Ð´Ð¾Ð¼ -> DISPATCHER_DATA -> temp.
    ÐÐ° PaaS Ñ read-only FS (Belmo) ÐºÐ¾Ð´ Ð»ÐµÐ¶Ð¸Ñ‚ Ð² /app Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð´Ð»Ñ Ñ‡Ñ‚ÐµÐ½Ð¸Ñ."""
    import tempfile
    cands = [BASE_DIR,
             os.environ.get("DISPATCHER_DATA", ""),
             os.path.join(tempfile.gettempdir(), "dispatcher")]
    for d in cands:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("1")
            os.remove(probe)
            return d
        except OSError:
            continue
    return BASE_DIR


DATA_DIR = _pick_data_dir()

# ---------- ÐºÐ¾Ð½Ñ„Ð¸Ð³ÑƒÑ€Ð°Ñ†Ð¸Ñ (config.ini Ñ€ÑÐ´Ð¾Ð¼ Ñ app.py) ----------

CFG = {"ors_key": "", "host": "127.0.0.1", "port": 5050,
        "admin_email": "admin@local", "admin_password": "admin",
        "tg_bot_token": "", "tg_poll": 1, "tz": "Europe/Minsk",
        "db_path": os.path.join(DATA_DIR, "dispatcher.db")}

_cp = configparser.ConfigParser()
if _cp.read(os.path.join(BASE_DIR, "config.ini"), encoding="utf-8"):
    _s = _cp["app"] if _cp.has_section("app") else {}
    CFG["ors_key"] = _s.get("ors_key", "").strip()
    CFG["host"] = _s.get("host", "127.0.0.1").strip()
    try:
        CFG["port"] = _s.getint("port", 5050)
    except ValueError:
        pass
    CFG["admin_email"] = (_s.get("admin_email", "") or "admin@local").strip().lower()
    CFG["admin_password"] = _s.get("admin_password", "") or "admin"
    CFG["tg_bot_token"] = _s.get("tg_bot_token", "").strip()
    CFG["tz"] = _s.get("tz", "Europe/Minsk").strip() or "Europe/Minsk"
    try:
        CFG["tg_poll"] = _s.getint("tg_poll", 1)
    except ValueError:
        pass
    _db = _s.get("db_path", "").strip()
    if _db:
        CFG["db_path"] = _db if os.path.isabs(_db) else os.path.join(BASE_DIR, _db)

# ÐžÐºÑ€ÑƒÐ¶ÐµÐ½Ð¸Ðµ Ð¿ÐµÑ€ÐµÐºÑ€Ñ‹Ð²Ð°ÐµÑ‚ config.ini (Ð´ÐµÐ¿Ð»Ð¾Ð¹ Ð² Ð¾Ð±Ð»Ð°ÐºÐ¾: Render Ð¸ Ñ‚.Ð¿.)
CFG["ors_key"] = os.environ.get("ORS_KEY", "").strip() or CFG["ors_key"]
CFG["tg_bot_token"] = os.environ.get("TG_BOT_TOKEN", "").strip() or CFG["tg_bot_token"]
# tg_poll=0 â€” ÑÑ‚Ð¾Ñ‚ Ð¸Ð½ÑÑ‚Ð°Ð½Ñ ÐÐ• ÑÐ»ÑƒÑˆÐ°ÐµÑ‚ Ð±Ð¾Ñ‚Ð° (ÐºÐ¾Ð³Ð´Ð° Ð±Ð¾Ñ‚ Ð·Ð°Ð½ÑÑ‚ Ð´Ñ€ÑƒÐ³Ð¸Ð¼ ÑÐµÑ€Ð²ÐµÑ€Ð¾Ð¼,
# Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€ Ð»Ð¾ÐºÐ°Ð»ÑŒÐ½Ñ‹Ð¹ + Ð¾Ð±Ð»Ð°Ñ‡Ð½Ñ‹Ð¹ Ð¾Ð´Ð½Ð¾Ð²Ñ€ÐµÐ¼ÐµÐ½Ð½Ð¾; Telegram Ð¾Ñ‚Ð´Ð°Ñ‘Ñ‚ getUpdates Ð¾Ð´Ð½Ð¾Ð¼Ñƒ)
if os.environ.get("TG_POLL", "").strip():
    try:
        CFG["tg_poll"] = 1 if os.environ["TG_POLL"].strip() not in ("0", "false", "no") else 0
    except ValueError:
        pass
CFG["admin_email"] = (os.environ.get("ADMIN_EMAIL", "").strip() or CFG["admin_email"]).lower()
CFG["admin_password"] = os.environ.get("ADMIN_PASSWORD", "").strip() or CFG["admin_password"]
for _pn in ("PORT", "SERVER_PORT", "P_SERVER_PORT"):  # PaaS/Pterodactyl Ð¾Ñ‚Ð´Ð°ÑŽÑ‚ Ð¿Ð¾Ñ€Ñ‚ Ð¿Ð¾-Ñ€Ð°Ð·Ð½Ð¾Ð¼Ñƒ
    _pv = os.environ.get(_pn, "").strip()
    if _pv:
        try:
            CFG["port"] = int(_pv)
            CFG["host"] = "0.0.0.0"
            break
        except ValueError:
            pass
if CFG.get("tz"):
    os.environ["TZ"] = CFG["tz"]
    if hasattr(time, "tzset"):
        time.tzset()


# ---------- Ð»Ð¾Ð³Ð¸Ñ€Ð¾Ð²Ð°Ð½Ð¸Ðµ ----------

_handlers = [logging.StreamHandler()]  # Ð²ÑÐµÐ³Ð´Ð°: stderr (Ð´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½ Ð½Ð° Ð»ÑŽÐ±Ð¾Ð¼ PaaS)
try:
    _handlers.insert(0, RotatingFileHandler(os.path.join(DATA_DIR, "dispatcher.log"),
                                            maxBytes=1_000_000, backupCount=3,
                                            encoding="utf-8"))
except OSError:
    pass  # read-only FS: Ð¶Ð¸Ð²Ñ‘Ð¼ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð² stderr
logging.basicConfig(
    handlers=_handlers,
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("waitress").setLevel(logging.WARNING)
log = logging.getLogger("dispatcher")

DEFAULT_DEPOT = {"address": "ÑƒÐ». ÐŸÐ¾Ð´Ð³Ð¾Ñ€Ð½Ð°Ñ 12/1, Ð“Ð¾Ð¼ÐµÐ»ÑŒ", "lat": 52.44146, "lng": 31.01476}

STATE = {
    "rev": 0,            # ÑÑ‡ÐµÑ‚Ñ‡Ð¸Ðº Ð¸Ð·Ð¼ÐµÐ½ÐµÐ½Ð¸Ð¹ Ð´Ð»Ñ long-poll /api/rev
    "depot": dict(DEFAULT_DEPOT),  # ÑÐ¾Ð²Ð¼ÐµÑÑ‚Ð¸Ð¼Ñ‹Ð¹ Ð²Ð¸Ð´ Ð¿ÐµÑ€Ð²Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸ {address, lat, lng}
    "points": [],        # Ð¼ÐµÑÑ‚Ð° Ð²Ñ‹Ð´Ð°Ñ‡Ð¸: {"id", "name", "address", "lat", "lng"}
    "couriers": [],      # {"id", "name", "status": base|away|off, "color", "back_min", "point_id"}
    "orders": [],        # {"id", "address", "lat", "lng"}
    "settings": {"speed_kmh": 60, "handover_min": 5, "max_orders": 5, "traffic": 1.25,
                 "lights_sec_per_km": 15, "auto_prio_min": 0, "reload_min": 10,
                 "hour_traffic": 1, "approach_center_min": 4, "approach_far_min": 2},
    "plans": {},         # pid -> Ð¿Ð»Ð°Ð½ Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸ (Ñƒ ÐºÐ°Ð¶Ð´Ð¾Ð³Ð¾ Ð´ÐµÐ¿Ð¾ ÑÐ²Ð¾Ð¹)
    "advice_modes": {},  # pid -> Ñ€ÑƒÑ‡Ð½Ð¾Ð¹ Ð²Ñ‹Ð±Ð¾Ñ€ Â«Ð¶Ð´Ð°Ñ‚ÑŒ/Ð½Ðµ Ð¶Ð´Ð°Ñ‚ÑŒÂ» (now|split)
    "solving": {},       # pid -> True: Ð² Ð´ÐµÐ¿Ð¾ Ð¸Ð´Ñ‘Ñ‚ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚ Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸ (ÐºÐ»Ð¸ÐµÐ½Ñ‚Ñ‹
                         # Ð±Ð»Ð¾ÐºÐ¸Ñ€ÑƒÑŽÑ‚ UI, Ð¿Ð¾Ð²Ñ‚Ð¾Ñ€Ð½Ñ‹Ð¹ Ð·Ð°Ð¿ÑƒÑÐº Ð¾Ñ‚ÐºÐ»Ð¾Ð½ÑÐµÑ‚ÑÑ)
    "color_seq": 0,      # Ð¼Ð¾Ð½Ð¾Ñ‚Ð¾Ð½Ð½Ñ‹Ð¹ ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸Ðº: Ñ†Ð²ÐµÑ‚Ð° Ð½Ðµ Ð¿ÐµÑ€ÐµÐ¼ÐµÑˆÐ¸Ð²Ð°ÑŽÑ‚ÑÑ Ð¿Ñ€Ð¸ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸ÑÑ…
    # Telegram: ÐºÑ‚Ð¾ Ð¿Ð¸ÑÐ°Ð» Ð±Ð¾Ñ‚Ñƒ (Ð´Ð»Ñ Ð¿Ñ€Ð¸Ð²ÑÐ·ÐºÐ¸), Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ Ð»Ð¾ÐºÐ°Ñ†Ð¸Ð¸ ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð², ÐºÑƒÑ€ÑÐ¾Ñ€ getUpdates
    "tg_seen": {},       # chat_id -> {"chat_id", "login", "ts"}
    "tg_pos": {},        # chat_id -> {"lat", "lng", "ts", "live"}
    "tg_nagged": {},     # chat_id -> ts Ð¿Ð¾ÑÐ»ÐµÐ´Ð½ÐµÐ³Ð¾ Â«Ð½Ðµ Ð¿Ñ€Ð¸Ð²ÑÐ·Ð°Ð½Â» (Ð°Ð½Ñ‚Ð¸ÑÐ¿Ð°Ð¼ live-Ð¿Ñ€Ð°Ð²Ð¾Ðº)
    "tg_load": {},       # chat_id -> {"since", "loaded_at"} â€” Ñ‚Ñ€ÐµÐºÐµÑ€ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ð·Ð°ÐºÐ°Ð·Ð¾Ð²
    "tg_deliv": {},      # chat_id -> {order_id: {"since", "at"}} â€” Ð²Ñ‹Ð²Ð¾Ð´ Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½Â»
                         # Ð¢ÐžÐ›Ð¬ÐšÐž Ð´Ð»Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð° Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚Ð°; ÑÑ‚Ð°Ñ‚ÑƒÑ Ð·Ð°ÐºÐ°Ð·Ð° Ð½Ðµ Ð¼ÐµÐ½ÑÐµÑ‚
    "tg_away": {},       # chat_id -> {"since"} â€” Ð°Ð²Ñ‚Ð¾-Â«Ð² Ð¿ÑƒÑ‚Ð¸Â» Ð¿Ñ€Ð¸ Ð¾Ñ‚ÑŠÐµÐ·Ð´Ðµ Ð¾Ñ‚ Ñ‚Ð¾Ñ‡ÐºÐ¸
    "tg_ask": {},        # chat_id -> {order_id: {"msg", "stage"}} â€” Ð±Ð¾Ñ‚ Ð¶Ð´Ñ‘Ñ‚ Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð¸Ð»?Â»
    "tg_offset": 0,
    "tg_bot": "",        # @username Ð±Ð¾Ñ‚Ð° (Ð´Ð»Ñ Ð¿Ð¾Ð´ÑÐºÐ°Ð·Ð¾Ðº Ð² Ð¸Ð½Ñ‚ÐµÑ€Ñ„ÐµÐ¹ÑÐµ)
    "events": [],        # Ð»ÐµÐ½Ñ‚Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸: {"t", "actor": bot|disp|cour|sys, "text"}
}

ROAD_FACTOR = 1.4  # Ð·Ð°Ð¿Ð°ÑÐ½Ð¾Ð¹ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚ (ÐµÑÐ»Ð¸ OSRM Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½): Ð¿Ñ€ÑÐ¼Ð°Ñ -> Ð´Ð¾Ñ€Ð¾Ð³Ð°
_PRIO_WEIGHT = 60  # Ð²ÐµÑ Ð¼Ð¸Ð½ÑƒÑ‚Ñ‹ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ¸ Ð¿Ñ€Ð¸Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚Ð½Ð¾Ð³Ð¾ Ð·Ð°ÐºÐ°Ð·Ð° (Ð¿Ñ€Ð¾Ñ‚Ð¸Ð² 1 Ñƒ Ð¾Ð±Ñ‹Ñ‡Ð½Ð¾Ð³Ð¾)
MAX_POINTS = 10    # Ð¼Ð°ÐºÑÐ¸Ð¼ÑƒÐ¼ Ð¼ÐµÑÑ‚ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸


def _depot_view():
    """Ð¡Ð¾Ð²Ð¼ÐµÑÑ‚Ð¸Ð¼Ñ‹Ð¹ ÑÐ¾ ÑÑ‚Ð°Ñ€Ñ‹Ð¼ API Ð²Ð¸Ð´ Ð¿ÐµÑ€Ð²Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ ({"address","lat","lng"})."""
    p = (STATE.get("points") or [None])[0]
    if not p:
        return dict(DEFAULT_DEPOT)
    return {"address": p["address"], "lat": p["lat"], "lng": p["lng"]}


def _home_point(courier):
    """Ð¢Ð¾Ñ‡ÐºÐ° Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ ÐºÑƒÑ€ÑŒÐµÑ€Ð° (Ð¸Ð»Ð¸ Ð¿ÐµÑ€Ð²Ð°Ñ, ÐµÑÐ»Ð¸ Ð¿Ñ€Ð¸Ð²ÑÐ·ÐºÐ° Ð½Ðµ Ð·Ð°Ð´Ð°Ð½Ð°/Ð±Ð¸Ñ‚Ð°Ñ)."""
    pid = (courier.get("point_id") or "").strip()
    for p in STATE.get("points") or []:
        if p["id"] == pid:
            return p
    return (STATE.get("points") or [None])[0]


def _obj_point(x):
    """Ð¢Ð¾Ñ‡ÐºÐ° Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ð·Ð°ÐºÐ°Ð·Ð°/ÐºÑƒÑ€ÑŒÐµÑ€Ð°: Ð¿ÑƒÑÑ‚Ð°Ñ Ð¿Ñ€Ð¸Ð²ÑÐ·ÐºÐ° = Ð¿ÐµÑ€Ð²Ð°Ñ Ñ‚Ð¾Ñ‡ÐºÐ°."""
    pid = (x.get("point_id") or "").strip()
    if pid and any(p["id"] == pid for p in STATE.get("points") or []):
        return pid
    return (STATE.get("points") or [{}])[0].get("id") or ""

OSRM_URLS = [
    "https://routing.openstreetmap.de/routed-car",  # ÑÐµÑ€Ð²ÐµÑ€Ñ‹ ÑÐ¾Ð¾Ð±Ñ‰ÐµÑÑ‚Ð²Ð° OSM (FOSSGIS) â€” Ð½Ð°Ð´Ñ‘Ð¶Ð½ÐµÐµ
    "http://router.project-osrm.org",               # Ð¾Ñ„Ð¸Ñ†Ð¸Ð°Ð»ÑŒÐ½Ñ‹Ð¹ Ð´ÐµÐ¼Ð¾-ÑÐµÑ€Ð²ÐµÑ€ â€” Ð·Ð°Ð¿Ð°ÑÐ½Ð¾Ð¹
]
_osrm_base = None  # Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ð¹ Ñ€Ð°Ð±Ð¾Ñ‡Ð¸Ð¹ ÑÐµÑ€Ð²ÐµÑ€ (Ð¿Ñ€Ð¾Ð²ÐµÑ€ÑÐµÑ‚ÑÑ Ð¿ÐµÑ€Ð²Ñ‹Ð¼)

# OpenRouteService: Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾Ð¹ Ð¸ÑÑ‚Ð¾Ñ‡Ð½Ð¸Ðº Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†/Ð³ÐµÐ¾Ð¼ÐµÑ‚Ñ€Ð¸Ð¸ (Ð¿Ð¾ ÐºÐ»ÑŽÑ‡Ñƒ, Ð±ÐµÑÐ¿Ð»Ð°Ñ‚Ð½Ñ‹Ð¹ Ñ‚Ð°Ñ€Ð¸Ñ„).
# ÐšÐ²Ð¾Ñ‚Ð° ÑÑƒÑ‚Ð¾Ðº Ð¾Ð³Ñ€Ð°Ð½Ð¸Ñ‡ÐµÐ½Ð°, Ð¿Ð¾ÑÑ‚Ð¾Ð¼Ñƒ: ÑÑ‡Ð¸Ñ‚Ð°ÐµÐ¼ Ð·Ð°Ð¿Ñ€Ð¾ÑÑ‹ ÑÐ°Ð¼Ð¸, Ð¿Ñ€Ð¸ Ð¿Ñ€Ð¸Ð±Ð»Ð¸Ð¶ÐµÐ½Ð¸Ð¸
# Ðº Ð»Ð¸Ð¼Ð¸Ñ‚Ñƒ Ð·Ð°Ñ€Ð°Ð½ÐµÐµ ÑƒÑ…Ð¾Ð´Ð¸Ð¼ Ð½Ð° OSRM, Ð° Ð¿Ñ€Ð¸ 429/403 Ð¾Ñ‚ÐºÐ»ÑŽÑ‡Ð°ÐµÐ¼ ORS Ð´Ð¾
# Ð²Ð¾ÑÑÑ‚Ð°Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ñ (ÑÑƒÑ‚ÐºÐ¸ â€” Ð´Ð¾ Ð¿Ð¾Ð»ÑƒÐ½Ð¾Ñ‡Ð¸ UTC, Ð¼Ð¸Ð½ÑƒÑ‚Ð½Ñ‹Ð¹ Ð»Ð¸Ð¼Ð¸Ñ‚ â€” Ð½Ð° 5 Ð¼Ð¸Ð½ÑƒÑ‚).
ORS_KEY = CFG["ors_key"]
ORS_BASE = "https://api.openrouteservice.org"
ORS_SOFT_LIMIT = 1800   # Ð·Ð°Ð¿Ð°Ñ Ð´Ð¾ Ð¿Ð°ÑÐ¿Ð¾Ñ€Ñ‚Ð½Ñ‹Ñ… 2000/ÑÑƒÑ‚ÐºÐ¸: Ð´Ð°Ð»ÑŒÑˆÐµ Ð½Ðµ Ñ‚Ñ€Ð°Ñ‚Ð¸Ð¼ ÐºÐ²Ð¾Ñ‚Ñƒ
ORS_MAX_POINTS = 50     # Ð»Ð¸Ð¼Ð¸Ñ‚ Ð±ÐµÑÐ¿Ð»Ð°Ñ‚Ð½Ð¾Ð³Ð¾ Ñ‚Ð°Ñ€Ð¸Ñ„Ð° Ð½Ð° Ñ€Ð°Ð·Ð¼ÐµÑ€ Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†Ñ‹
ORS_STATE = {"day": None, "used": 0, "disabled_until": None, "last_error": None}

PALETTE = ["#e8482b", "#2563eb", "#059669", "#9333ea",
           "#d97706", "#0891b2", "#be185d", "#4d7c0f"]
STATUSES = {"base", "away", "off"}

# ---------- Ð¿ÐµÑ€ÑÐ¸ÑÑ‚ÐµÐ½Ñ‚Ð½Ð¾ÑÑ‚ÑŒ (SQLite) ----------

_db_lock = threading.Lock()
_db_path = CFG["db_path"]

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS points(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
    lat REAL NOT NULL, lng REAL NOT NULL, pos INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS couriers(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, color TEXT);
CREATE TABLE IF NOT EXISTS orders(
    id TEXT PRIMARY KEY, address TEXT, lat REAL NOT NULL, lng REAL NOT NULL,
    created_at TEXT NOT NULL, prio INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS history(
    id TEXT PRIMARY KEY, address TEXT, lat REAL, lng REAL,
    created_at TEXT, closed_at TEXT NOT NULL, outcome TEXT NOT NULL, courier TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS users(
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, pwd_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS speed_day(
    courier_id TEXT NOT NULL, day TEXT NOT NULL,
    geo_m REAL NOT NULL DEFAULT 0, geo_s REAL NOT NULL DEFAULT 0,
    del_n INTEGER NOT NULL DEFAULT 0, del_min REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(courier_id, day));
"""

_DB_MIGRATIONS = [
    # (Ñ‚Ð°Ð±Ð»Ð¸Ñ†Ð°, ÐºÐ¾Ð»Ð¾Ð½ÐºÐ°, DDL) â€” Ð²Ñ‹Ð¿Ð¾Ð»Ð½ÑÐµÑ‚ÑÑ, ÐµÑÐ»Ð¸ ÐºÐ¾Ð»Ð¾Ð½ÐºÐ¸ ÐµÑ‰Ñ‘ Ð½ÐµÑ‚
    ("couriers", "back_min", "ALTER TABLE couriers ADD COLUMN back_min INTEGER DEFAULT 15"),
    ("history", "courier", "ALTER TABLE history ADD COLUMN courier TEXT DEFAULT ''"),
    ("orders", "prio", "ALTER TABLE orders ADD COLUMN prio INTEGER DEFAULT 0"),
    ("orders", "deadline", "ALTER TABLE orders ADD COLUMN deadline TEXT DEFAULT ''"),
    ("history", "deadline", "ALTER TABLE history ADD COLUMN deadline TEXT DEFAULT ''"),
    ("couriers", "tg_chat_id", "ALTER TABLE couriers ADD COLUMN tg_chat_id TEXT DEFAULT ''"),
    ("couriers", "tg_login", "ALTER TABLE couriers ADD COLUMN tg_login TEXT DEFAULT ''"),
    ("orders", "status", "ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'"),
    ("orders", "assigned", "ALTER TABLE orders ADD COLUMN assigned TEXT NOT NULL DEFAULT ''"),
    ("orders", "out_at", "ALTER TABLE orders ADD COLUMN out_at TEXT NOT NULL DEFAULT ''"),
    ("couriers", "point_id", "ALTER TABLE couriers ADD COLUMN point_id TEXT NOT NULL DEFAULT ''"),
    ("orders", "point_id", "ALTER TABLE orders ADD COLUMN point_id TEXT NOT NULL DEFAULT ''"),
    ("orders", "pin", "ALTER TABLE orders ADD COLUMN pin TEXT NOT NULL DEFAULT ''"),
    ("users", "name", "ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''"),
    ("users", "phone", "ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''"),
    ("history", "point_id", "ALTER TABLE history ADD COLUMN point_id TEXT NOT NULL DEFAULT ''"),
    ("history", "reason", "ALTER TABLE history ADD COLUMN reason TEXT NOT NULL DEFAULT ''"),
]


@contextmanager
def _db():
    conn = sqlite3.connect(_db_path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(_DB_SCHEMA)  # Ð¸Ð´ÐµÐ¼Ð¿Ð¾Ñ‚ÐµÐ½Ñ‚Ð½Ð¾; Ð¿ÐµÑ€ÐµÐ¶Ð¸Ð²Ð°ÐµÑ‚ ÑƒÐ´Ð°Ð»ÐµÐ½Ð¸Ðµ Ñ„Ð°Ð¹Ð»Ð° Ð½Ð° Ñ…Ð¾Ð´Ñƒ
        for table, column, ddl in _DB_MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(ddl)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _persist_meta():
    with _db_lock, _db() as c:
        c.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", [
            ("depot", json.dumps(STATE["depot"], ensure_ascii=False)),
            ("settings", json.dumps(STATE["settings"], ensure_ascii=False)),
            ("color_seq", str(STATE["color_seq"])),
            ("plans", json.dumps(STATE["plans"], ensure_ascii=False)
             if STATE.get("plans") else ""),
            ("tg_ask", json.dumps(STATE["tg_ask"], ensure_ascii=False)
             if STATE.get("tg_ask") else ""),
            ("tg_deliv", json.dumps(STATE["tg_deliv"], ensure_ascii=False)
             if STATE.get("tg_deliv") else "")])
        c.execute("DELETE FROM points")
        c.executemany(
            "INSERT INTO points(id, name, address, lat, lng, pos) VALUES(?, ?, ?, ?, ?, ?)",
            [(p["id"], p["name"], p["address"], float(p["lat"]), float(p["lng"]), i)
             for i, p in enumerate(STATE.get("points") or [])])


def _persist_couriers():
    with _db_lock, _db() as c:
        c.execute("DELETE FROM couriers")
        c.executemany(
            "INSERT INTO couriers(id, name, status, color, back_min, tg_chat_id, tg_login, point_id) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            [(x["id"], x["name"], x["status"], x.get("color") or "",
              int(x.get("back_min", 15)), x.get("tg_chat_id") or "",
              x.get("tg_login") or "", x.get("point_id") or "")
             for x in STATE["couriers"]])


def _persist_orders():
    with _db_lock, _db() as c:
        c.execute("DELETE FROM orders")
        c.executemany(
            "INSERT INTO orders(id, address, lat, lng, created_at, prio, deadline, "
            "status, assigned, out_at, point_id, pin) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(x["id"], x["address"], x["lat"], x["lng"],
              x.get("created_at") or _now().isoformat(timespec="seconds"),
              int(x.get("prio") or 0), x.get("deadline") or "",
              x.get("status") or "ready", x.get("assigned") or "",
              x.get("out_at") or "", x.get("point_id") or "", x.get("pin") or "")
             for x in STATE["orders"]])


def _archive_order(order, outcome, courier="", courier_id="", reason=""):
    closed = _now().isoformat(timespec="seconds")
    if outcome == "delivered" and courier_id and order.get("out_at"):
        try:  # Ñ‚ÐµÐ¼Ð¿ Ð´Ð¾ÑÑ‚Ð°Ð²Ð¾Ðº -> Ñ„Ð¾Ð»Ð»Ð±ÐµÐº-Ð·Ð°Ð¼ÐµÑ€ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚Ð¸
            cycle_min = (_now() - datetime.fromisoformat(order["out_at"])
                         ).total_seconds() / 60.0
            if 1 <= cycle_min <= 180:
                _speed_add(courier_id, del_n=1, del_min=cycle_min)
        except ValueError:
            pass
    with _db_lock, _db() as c:
        c.execute("INSERT OR REPLACE INTO history "
                  "(id, address, lat, lng, created_at, closed_at, outcome,"
                  " courier, deadline, point_id, reason) "
                  "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (order["id"], order["address"], order["lat"], order["lng"],
                   order.get("created_at"),
                   closed, outcome, courier,
                   order.get("deadline") or "", _obj_point(order), reason))


def _history_period(days=1, point_id=None):
    """Ð¡Ñ‚Ñ€Ð¾ÐºÐ¸ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ð¸ Ð·Ð° Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ðµ `days` Ð´Ð½ÐµÐ¹ + ÑÐ²Ð¾Ð´ÐºÐ° (Ð½Ð¾Ð²Ñ‹Ðµ â€” Ð¿ÐµÑ€Ð²Ñ‹Ð¼Ð¸).

    point_id â€” Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð·Ð°ÐºÐ°Ð·Ñ‹ ÑÑ‚Ð¾Ð³Ð¾ Ð´ÐµÐ¿Ð¾ (None = Ð²ÑÐµ; Ð¿ÑƒÑÑ‚Ð°Ñ point_id Ñƒ ÑÑ‚Ð°Ñ€Ñ‹Ñ…
    ÑÑ‚Ñ€Ð¾Ðº Ñ‚Ñ€Ð°ÐºÑ‚ÑƒÐµÑ‚ÑÑ ÐºÐ°Ðº Ð¿ÐµÑ€Ð²Ð°Ñ Ñ‚Ð¾Ñ‡ÐºÐ°).
    """
    since = (_now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    where, args = "substr(closed_at, 1, 10) >= ?", [since]
    if point_id:
        first = (STATE.get("points") or [{}])[0].get("id") or ""
        where += " AND (point_id = ? OR (point_id = '' AND ? = ?))"
        args += [point_id, first, point_id]
    try:
        with _db_lock, _db() as c:
            rows = c.execute(
                f"SELECT * FROM history WHERE {where} "
                "ORDER BY closed_at DESC LIMIT 500", args).fetchall()
    except sqlite3.Error:
        return {"rows": [], "summary": {}}
    out, cycles = [], []
    for r in rows:
        cycle_min = None
        try:
            if r["created_at"] and r["outcome"] == "delivered":
                delta = (datetime.fromisoformat(r["closed_at"])
                         - datetime.fromisoformat(r["created_at"]))
                if 0 < delta.total_seconds() < 24 * 3600:
                    cycle_min = int(delta.total_seconds() // 60)
                    cycles.append(cycle_min)
        except ValueError:
            pass
        out.append({"closed_at": r["closed_at"], "address": r["address"],
                    "outcome": r["outcome"], "courier": r["courier"] or "",
                    "cycle_min": cycle_min, "deadline": r["deadline"] or "",
                    "reason": r["reason"] or ""})
    summary = {"delivered": sum(1 for r in out if r["outcome"] == "delivered"),
               "cancelled": sum(1 for r in out if r["outcome"] == "cancelled"),
               "avg_cycle_min": round(sum(cycles) / len(cycles)) if cycles else None}
    return {"rows": out, "summary": summary}


def _history_today(point_id=None):
    return _history_period(1, point_id=point_id)["summary"]


# ---------- Ð¸Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»ÑŒÐ½Ð°Ñ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚ÑŒ ÐºÑƒÑ€ÑŒÐµÑ€Ð° ----------
# Ð—Ð°Ð¼ÐµÑ€ Ð¿Ð¾ Ð³ÐµÐ¾: Ð¿Ð°Ñ€Ñ‹ Ð¡Ð“Ð›ÐÐ–Ð•ÐÐÐ«Ð¥ (Ð¼ÐµÐ´Ð¸Ð°Ð½Ð°) Ñ‚Ð¾Ñ‡ÐµÐº live-Ð»Ð¾ÐºÐ°Ñ†Ð¸Ð¸ Ñ dt >= 15 c,
# Ð¾Ñ‚Ñ€ÐµÐ·ÐºÐ¾Ð¼ >= 40 Ð¼ Ð¸ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚ÑŒÑŽ 3..80 ÐºÐ¼/Ñ‡ Ð´Ð¾Ð±Ð°Ð²Ð»ÑÑŽÑ‚ Ð¼ÐµÑ‚Ñ€Ñ‹/ÑÐµÐºÑƒÐ½Ð´Ñ‹ Ð² speed_day
# Ð·Ð° ÑÐµÐ³Ð¾Ð´Ð½Ñ. ÐžÐ´Ð¸Ð½Ð¾Ñ‡Ð½Ñ‹Ð¹ GPS-Ð¿Ñ€Ñ‹Ð¶Ð¾Ðº Ð³Ð°ÑÐ¸Ñ‚ÑÑ Ð¼ÐµÐ´Ð¸Ð°Ð½Ð¾Ð¹ (Ð½Ðµ Ð¿Ð¾Ð¿Ð°Ð´Ð°ÐµÑ‚ Ð² Ñ‚Ñ€ÐµÐº),
# Ð¼ÐµÐ»ÐºÐ°Ñ Ð´Ñ€Ð¾Ð¶ÑŒ Ð½Ð° Ð¼ÐµÑÑ‚Ðµ â€” Ð¿Ð¾Ñ€Ð¾Ð³Ð¾Ð¼ Ð´Ð¸ÑÑ‚Ð°Ð½Ñ†Ð¸Ð¸, Ð²Ñ‹Ð±Ñ€Ð¾Ñ Â«1000 ÐºÐ¼/Ñ‡Â» â€” Ð¿Ð¾Ñ‚Ð¾Ð»ÐºÐ¾Ð¼,
# Ð° Ð´Ð½ÐµÐ²Ð½Ð°Ñ ÑÑƒÐ¼Ð¼Ð° ÑƒÑÑ€ÐµÐ´Ð½ÑÐµÑ‚ Ð¾ÑÑ‚Ð°Ñ‚Ð¾Ñ‡Ð½Ñ‹Ð¹ ÑˆÑƒÐ¼.
# Ð¤Ð¾Ð»Ð»Ð±ÐµÐº Ð¿Ð¾ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ°Ð¼: ÑÑ€ÐµÐ´Ð½Ð¸Ð¹ Ñ†Ð¸ÐºÐ» ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð¿Ñ€Ð¾Ñ‚Ð¸Ð² ÑÑ€ÐµÐ´Ð½ÐµÐ³Ð¾ Ð¿Ð¾ Ñ„Ð»Ð¾Ñ‚Ñƒ
# Ð·Ð° Ñ‚Ð¾Ñ‚ Ð¶Ðµ Ð´ÐµÐ½ÑŒ â€” Ð¾Ñ‚Ð½Ð¾ÑˆÐµÐ½Ð¸Ðµ Ð¼Ð°ÑÑˆÑ‚Ð°Ð±Ð¸Ñ€ÑƒÐµÑ‚ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚ÑŒ Ð¿Ð¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ.
_SPEED_MIN_GEO_S = 180.0   # Ð½ÑƒÐ¶Ð½Ð¾ >= 3 Ð¼Ð¸Ð½ÑƒÑ‚ Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ñ, Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð´Ð¾Ð²ÐµÑ€ÑÑ‚ÑŒ Ð³ÐµÐ¾
_SPEED_MIN_DEL_N = 2       # Ð½ÑƒÐ¶Ð½Ð¾ >= 2 Ð´Ð¾ÑÑ‚Ð°Ð²Ð¾Ðº, Ñ‡Ñ‚Ð¾Ð±Ñ‹ ÑÑ€Ð°Ð²Ð½Ð¸Ð²Ð°Ñ‚ÑŒ Ñ‚ÐµÐ¼Ð¿
_SPEED_KMH_BOUNDS = (5.0, 80.0)
_SPEED_RATIO_BOUNDS = (0.6, 1.7)  # Ñ„Ð¾Ð»Ð»Ð±ÐµÐº Ð½Ðµ Ð¼Ð¾Ð¶ÐµÑ‚ ÑƒÐ²Ð¾Ð´Ð¸Ñ‚ÑŒ Ð´Ð°Ð»ÐµÐºÐ¾ Ð¾Ñ‚ Ð½Ð¾Ñ€Ð¼Ñ‹
_SPEED_SEG_MIN_M = 40.0    # ÐºÐ¾Ñ€Ð¾Ñ‡Ðµ 40 Ð¼ â€” Ð´Ñ€Ð¾Ð¶ÑŒ ÑÑ‚Ð¾ÑÐ½Ð¸Ñ, Ð½Ðµ Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ðµ
_SPEED_MAX_ACC_M = 100.0   # Ñ‚Ð¾Ñ‡Ð½Ð¾ÑÑ‚ÑŒ Ñ…ÑƒÐ¶Ðµ 100 Ð¼ â€” Ñ‚Ð¾Ñ‡ÐºÐ° Ð¼ÑƒÑÐ¾Ñ€Ð½Ð°Ñ


def _speed_add(courier_id, day=None, geo_m=0.0, geo_s=0.0, del_n=0, del_min=0.0):
    day = day or _now().strftime("%Y-%m-%d")
    with _db_lock, _db() as c:
        c.execute(
            "INSERT INTO speed_day(courier_id, day, geo_m, geo_s, del_n, del_min) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(courier_id, day) DO UPDATE SET "
            "geo_m = geo_m + excluded.geo_m, geo_s = geo_s + excluded.geo_s, "
            "del_n = del_n + excluded.del_n, del_min = del_min + excluded.del_min",
            (courier_id, day, geo_m, geo_s, del_n, del_min))


def _speed_geo_sample(courier_id, prev, cur):
    """Ð¡ÐºÐ»Ð°Ð´Ñ‹Ð²Ð°ÐµÑ‚ Ð¾Ñ‚Ñ€ÐµÐ·Ð¾Ðº Ð¼ÐµÐ¶Ð´Ñƒ Ð´Ð²ÑƒÐ¼Ñ Ð³ÐµÐ¾-Ñ‚Ð¾Ñ‡ÐºÐ°Ð¼Ð¸ Ð² Ð´Ð½ÐµÐ²Ð½Ð¾Ð¹ Ð·Ð°Ð¼ÐµÑ€ (Ð¸Ð»Ð¸ Ð¸Ð³Ð½Ð¾Ñ€)."""
    dt = cur["ts"] - prev["ts"]
    if not (15 <= dt <= 600):
        return
    if max(prev.get("acc") or 0, cur.get("acc") or 0) > _SPEED_MAX_ACC_M:
        return  # Ñ‚Ð¾Ñ‡Ð½Ð¾ÑÑ‚ÑŒ Ñ…ÑƒÐ¶Ðµ 100 Ð¼ â€” Ð²ÐµÑ€Ð¸Ñ‚ÑŒ Ð¾Ñ‚Ñ€ÐµÐ·ÐºÑƒ Ð½ÐµÐ»ÑŒÐ·Ñ
    m = haversine_km(prev, cur) * ROAD_FACTOR * 1000.0
    if m < _SPEED_SEG_MIN_M:
        return  # Ð´Ñ€Ð¾Ð¶ÑŒ Ð½Ð° Ð¼ÐµÑÑ‚Ðµ / ÑˆÐ°Ð³ Ð²Ð½ÑƒÑ‚Ñ€Ð¸ Ð¿Ð¾Ð³Ñ€ÐµÑˆÐ½Ð¾ÑÑ‚Ð¸ GPS
    kmh = m / 1000.0 / (dt / 3600.0)
    if 3.0 <= kmh <= 80.0:
        _speed_add(courier_id, geo_m=m, geo_s=dt)


_SPEED_CUR_WINDOW = 240.0  # Ð¾ÐºÐ½Ð¾ Â«Ñ‚ÐµÐºÑƒÑ‰ÐµÐ¹Â» ÑÐºÐ¾Ñ€Ð¾ÑÑ‚Ð¸, ÑÐµÐºÑƒÐ½Ð´Ñ‹
_SPEED_CUR_MAX_AGE = 300.0  # Ð³ÐµÐ¾ ÑÑ‚Ð°Ñ€ÑˆÐµ 5 Ð¼Ð¸Ð½ÑƒÑ‚ â€” Ñ‚ÐµÐºÑƒÑ‰ÐµÐ¹ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚Ð¸ Ð½ÐµÑ‚


def _speed_current_kmh(pos, now):
    """Ð¡ÐºÐ¾Ñ€Ð¾ÑÑ‚ÑŒ Â«Ð¿Ñ€ÑÐ¼Ð¾ ÑÐµÐ¹Ñ‡Ð°ÑÂ» Ð¿Ð¾ ÑÐ²ÐµÐ¶ÐµÐ¼Ñƒ Ð³ÐµÐ¾-Ñ‚Ñ€ÐµÐºÑƒ. None â€” Ð³ÐµÐ¾ Ð½ÐµÑ‚/ÑƒÑÑ‚Ð°Ñ€ÐµÐ»Ð¾,
    0.0 â€” ÑÑ‚Ð¾Ð¸Ñ‚ Ð½Ð° Ð¼ÐµÑÑ‚Ðµ (Ñ‚Ð¾Ñ‡ÐºÐ¸ ÐµÑÑ‚ÑŒ, Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ñ Ð½ÐµÑ‚)."""
    if not pos or now - pos["ts"] > _SPEED_CUR_MAX_AGE:
        return None
    hist = [h for h in pos.get("hist", []) if now - h["ts"] <= _SPEED_CUR_WINDOW]
    if len(hist) < 2:
        return None
    m_sum = t_sum = 0.0
    for a, b in zip(hist, hist[1:]):
        dt = b["ts"] - a["ts"]
        if dt < 5:
            continue
        if max(a.get("acc") or 0, b.get("acc") or 0) > _SPEED_MAX_ACC_M:
            continue
        m = haversine_km(a, b) * ROAD_FACTOR * 1000.0
        kmh = m / 1000.0 / (dt / 3600.0)
        if kmh > 90.0:
            continue  # GPS-Ð¿Ñ€Ñ‹Ð¶Ð¾Ðº
        if m < 15.0 and kmh < 5.0:
            t_sum += dt  # ÑÑ‚Ð¾Ð¸Ñ‚ Ð½Ð° Ð¼ÐµÑÑ‚Ðµ: Ð²Ñ€ÐµÐ¼Ñ Ð¸Ð´Ñ‘Ñ‚, Ð¼ÐµÑ‚Ñ€Ñ‹ â€” Ð½ÐµÑ‚
            continue
        m_sum += m
        t_sum += dt
    return round(m_sum / 1000.0 / (t_sum / 3600.0), 1) if t_sum else 0.0


def _speed_rows(courier_id, limit=30):
    with _db_lock, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM speed_day WHERE courier_id = ? "
            "ORDER BY day DESC LIMIT ?", (courier_id, limit))]


def _speed_fleet_cycle_avg(day):
    """Ð¡Ñ€ÐµÐ´Ð½Ð¸Ð¹ Ñ†Ð¸ÐºÐ» Ð´Ð¾ÑÑ‚Ð°Ð²Ð¾Ðº Ð¿Ð¾ Ð²ÑÐµÐ¼ ÐºÑƒÑ€ÑŒÐµÑ€Ð°Ð¼ Ð·Ð° Ð´ÐµÐ½ÑŒ (Ð¸Ð»Ð¸ None)."""
    with _db_lock, _db() as c:
        r = c.execute("SELECT SUM(del_min) AS s, SUM(del_n) AS n FROM speed_day "
                      "WHERE day = ? AND del_n > 0", (day,)).fetchone()
    return (r["s"] / r["n"]) if r and r["n"] else None


def _courier_del_avg_min(courier):
    """Ð¡Ñ€ÐµÐ´Ð½Ð¸Ðµ Ð¼Ð¸Ð½ÑƒÑ‚Ñ‹ Ð½Ð° Ð¾Ð´Ð¸Ð½ Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½Ð½Ñ‹Ð¹ Ð·Ð°ÐºÐ°Ð·: ÑÐ²Ð¾Ð¹ Ñ‚ÐµÐ¼Ð¿, Ð¸Ð½Ð°Ñ‡Ðµ Ñ„Ð»Ð¾Ñ‚, Ð¸Ð½Ð°Ñ‡Ðµ 15."""
    rows = _speed_rows(courier.get("id") or "", limit=7)
    for r in rows:
        if (r["del_n"] or 0) >= 1 and (r["del_min"] or 0) > 0:
            return min(90.0, r["del_min"] / r["del_n"])
    for r in rows:
        if r["day"]:
            fleet = _speed_fleet_cycle_avg(r["day"])
            if fleet:
                return min(90.0, fleet)
            break
    return 15.0


def _speed_from_row(row, default_kmh):
    """Ð¡ÐºÐ¾Ñ€Ð¾ÑÑ‚ÑŒ Ð¸Ð· ÑÑ‚Ñ€Ð¾ÐºÐ¸ Ð´Ð½Ñ: ÑÐ½Ð°Ñ‡Ð°Ð»Ð° Ð³ÐµÐ¾, Ð¸Ð½Ð°Ñ‡Ðµ Ñ‚ÐµÐ¼Ð¿ Ð´Ð¾ÑÑ‚Ð°Ð²Ð¾Ðº. None â€” Ð½ÐµÑ‚ Ð´Ð°Ð½Ð½Ñ‹Ñ…."""
    if row["geo_s"] >= _SPEED_MIN_GEO_S:
        kmh = row["geo_m"] / row["geo_s"] * 3.6
        if kmh > 0.5:
            return min(_SPEED_KMH_BOUNDS[1], max(_SPEED_KMH_BOUNDS[0], kmh)), "geo"
    if row["del_n"] >= _SPEED_MIN_DEL_N:
        mine = row["del_min"] / row["del_n"]
        fleet = _speed_fleet_cycle_avg(row["day"])
        if fleet and mine > 0:
            ratio = fleet / mine  # Ñ†Ð¸ÐºÐ» Ð´Ð»Ð¸Ð½Ð½ÐµÐµ ÑÑ€ÐµÐ´Ð½ÐµÐ³Ð¾ -> Ð¼ÐµÐ´Ð»ÐµÐ½Ð½ÐµÐµ
            ratio = min(_SPEED_RATIO_BOUNDS[1], max(_SPEED_RATIO_BOUNDS[0], ratio))
            return min(_SPEED_KMH_BOUNDS[1],
                       max(_SPEED_KMH_BOUNDS[0], default_kmh * ratio)), "delivery"
    return None


def _courier_speed(courier, settings=None):
    """(ÐºÐ¼/Ñ‡, Ð¸ÑÑ‚Ð¾Ñ‡Ð½Ð¸Ðº) Ð¸Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»ÑŒÐ½Ð¾Ð¹ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚Ð¸ ÐºÑƒÑ€ÑŒÐµÑ€Ð°.

    Ð›ÐµÑÑ‚Ð½Ð¸Ñ†Ð°: ÑÐµÐ³Ð¾Ð´Ð½Ñ (Ð³ÐµÐ¾ -> Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ¸) -> Ð²Ñ‡ÐµÑ€Ð° -> ÑÐ°Ð¼Ñ‹Ð¹ ÑÐ²ÐµÐ¶Ð¸Ð¹ Ð´ÐµÐ½ÑŒ Ñ Ð·Ð°Ð¼ÐµÑ€Ð¾Ð¼
    -> Ð½Ð°ÑÑ‚Ñ€Ð¾Ð¹ÐºÐ° speed_kmh (Ð¸ÑÑ‚Ð¾Ñ‡Ð½Ð¸Ðº "default").
    """
    default_kmh = max(5.0, float((settings or STATE["settings"])
                                 .get("speed_kmh", 60)))
    if not courier or not courier.get("id"):
        return default_kmh, "default"
    for row in _speed_rows(courier["id"]):
        got = _speed_from_row(row, default_kmh)
        if got:
            return got
    return default_kmh, "default"


def load_state():
    """Ð—Ð°Ð³Ñ€ÑƒÐ·ÐºÐ° ÑÐ¾Ñ…Ñ€Ð°Ð½Ñ‘Ð½Ð½Ð¾Ð³Ð¾ ÑÐ¾ÑÑ‚Ð¾ÑÐ½Ð¸Ñ Ð¿Ñ€Ð¸ ÑÑ‚Ð°Ñ€Ñ‚Ðµ (Ð´Ð°Ð½Ð½Ñ‹Ðµ Ð¿ÐµÑ€ÐµÐ¶Ð¸Ð²Ð°ÑŽÑ‚ Ñ€ÐµÑÑ‚Ð°Ñ€Ñ‚)."""
    with _db_lock, _db() as c:
        c.executescript(_DB_SCHEMA)
        meta = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")}
        couriers = [dict(r) for r in c.execute(
            "SELECT id, name, status, color, back_min, tg_chat_id, tg_login, point_id "
            "FROM couriers ORDER BY rowid")]
        orders = [dict(r) for r in c.execute(
            "SELECT id, address, lat, lng, created_at, prio, deadline, "
            "status, assigned, out_at, point_id, pin FROM orders ORDER BY rowid")]
        pts = [dict(r) for r in c.execute(
            "SELECT id, name, address, lat, lng FROM points ORDER BY pos, rowid")]
    if pts:
        STATE["points"] = [{"id": p["id"], "name": p["name"], "address": p["address"],
                            "lat": float(p["lat"]), "lng": float(p["lng"])} for p in pts]
    elif meta.get("points"):
        try:
            saved_pts = json.loads(meta["points"])
            if isinstance(saved_pts, list) and saved_pts:
                STATE["points"] = saved_pts
        except ValueError:
            pass
    if not STATE.get("points"):
        # ÑÐ¸Ð´: Ð´Ð²Ðµ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Barak (Ð“Ð¾Ð¼ÐµÐ»ÑŒ)
        STATE["points"] = [
            {"id": uuid.uuid4().hex[:8], "name": "ÐŸÐ¾Ð´Ð³Ð¾Ñ€Ð½Ð°Ñ",
             "address": "ÑƒÐ». ÐŸÐ¾Ð´Ð³Ð¾Ñ€Ð½Ð°Ñ 12/1, Ð“Ð¾Ð¼ÐµÐ»ÑŒ",
             "lat": 52.44175316939852, "lng": 31.01452592124391},
            {"id": uuid.uuid4().hex[:8], "name": "Ð‘Ð°Ñ€Ñ‹ÐºÐ¸Ð½Ð°",
             "address": "ÑƒÐ». Ð‘Ð°Ñ€Ñ‹ÐºÐ¸Ð½Ð° 230Ð‘, Ð“Ð¾Ð¼ÐµÐ»ÑŒ",
             "lat": 52.42326517981715, "lng": 30.934015684685928},
        ]
        _persist_meta()
    STATE["depot"] = _depot_view()
    _pt_ids = {p["id"] for p in STATE["points"]}
    _first = STATE["points"][0]["id"]
    for r_c in couriers:
        if r_c.get("point_id") not in _pt_ids:
            r_c["point_id"] = _first
    if meta.get("settings"):
        try:
            saved = json.loads(meta["settings"])
            STATE["settings"].update({k: v for k, v in saved.items()
                                      if k in STATE["settings"]})
        except ValueError:
            pass
    try:
        STATE["color_seq"] = int(meta.get("color_seq") or 0)
    except ValueError:
        pass
    for key in ("tg_ask", "tg_deliv"):
        # Ð´Ð¸Ð°Ð»Ð¾Ð³Ð¸ Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½?Â» Ð¸ Ñ‚Ñ€ÐµÐºÐµÑ€Ñ‹ Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ñ Ð¿ÐµÑ€ÐµÐ¶Ð¸Ð²Ð°ÑŽÑ‚ Ñ€ÐµÑÑ‚Ð°Ñ€Ñ‚:
        # Ð±ÐµÐ· ÑÑ‚Ð¾Ð³Ð¾ Ð¿Ð¾ÑÐ»Ðµ ÐºÐ°Ð¶Ð´Ð¾Ð³Ð¾ Ð´ÐµÐ¿Ð»Ð¾Ñ Ð±Ð¾Ñ‚ Ð¿ÐµÑ€ÐµÑÐ¿Ñ€Ð°ÑˆÐ¸Ð²Ð°Ð», Ð° Ð½Ð°Ð¶Ð°Ñ‚Ð¸Ñ
        # ÐºÐ½Ð¾Ð¿Ð¾Ðº Ð½Ð° ÑÑ‚Ð°Ñ€Ñ‹Ñ… ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÑÑ… Ð¿Ð¾Ð¿Ð°Ð´Ð°Ð»Ð¸ Ð² Â«ÑƒÐ¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÑŒÐ½Ð¾Â»
        if meta.get(key):
            try:
                saved = json.loads(meta[key])
                if isinstance(saved, dict):
                    STATE[key].update(saved)
            except ValueError:
                pass
    # ÑÐ²ÐµÑ€ÐºÐ°: ÐµÑÐ»Ð¸ Ð±Ð¾Ñ‚ ÑƒÐ¶Ðµ ÑÐ¿Ñ€Ð¾ÑÐ¸Ð» (asked), Ð° Ð´Ð¸Ð°Ð»Ð¾Ð³ Ð½Ðµ Ð²Ð¾ÑÑÑ‚Ð°Ð½Ð¾Ð²Ð¸Ð»ÑÑ â€”
    # ÑÐ±Ñ€Ð°ÑÑ‹Ð²Ð°ÐµÐ¼ asked, Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ð¿ÐµÑ€ÐµÑÐ¿Ñ€Ð¾ÑÐ¸Ð» Ð·Ð°Ð½Ð¾Ð²Ð¾ ÑÐ²ÐµÐ¶Ð¸Ð¼ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸ÐµÐ¼
    for _chat, _recs in (STATE["tg_deliv"] or {}).items():
        for _oid, _rec in list((_recs or {}).items()):
            if _rec.get("asked") and _oid not in (STATE["tg_ask"].get(_chat) or {}):
                _rec.pop("asked", None)
    STATE["couriers"] = [{"id": r["id"], "name": r["name"], "status": r["status"],
                          "color": r["color"] or None,
                          "back_min": int(r["back_min"] if r["back_min"] is not None else 15),
                          "tg_chat_id": r["tg_chat_id"] or "",
                          "tg_login": r.get("tg_login") or "",
                          "point_id": r.get("point_id") or _first}
                         for r in couriers]
    STATE["orders"] = [{**r, "prio": int(r.get("prio") or 0),
                        "deadline": r.get("deadline") or "",
                        "status": r.get("status") or "ready",
                        "assigned": r.get("assigned") or "",
                        "out_at": r.get("out_at") or ""} for r in orders]
    if meta.get("plans"):
        try:
            ps = json.loads(meta["plans"])
            if isinstance(ps, dict):
                STATE["plans"] = {k: v for k, v in ps.items()
                                  if isinstance(v, dict) and v.get("routes")}
        except ValueError:
            pass
    elif meta.get("plan"):  # ÑÑ‚Ð°Ñ€Ñ‹Ð¹ Ñ„Ð¾Ñ€Ð¼Ð°Ñ‚: ÐµÐ´Ð¸Ð½Ñ‹Ð¹ Ð¿Ð»Ð°Ð½ -> Ð¿Ð»Ð°Ð½ Ð¿ÐµÑ€Ð²Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸
        try:
            p = json.loads(meta["plan"])
            first = (STATE.get("points") or [{}])[0].get("id")
            if isinstance(p, dict) and p.get("routes") and first:
                STATE["plans"][first] = p
        except ValueError:
            pass
    log.info("state loaded: %d couriers, %d orders", len(couriers), len(orders))


# ÑÐµÐºÑ€ÐµÑ‚ ÑÐµÑÑÐ¸Ð¹: ÑÑ‚Ð°Ð±Ð¸Ð»ÐµÐ½ Ð¼ÐµÐ¶Ð´Ñƒ Ñ€ÐµÑÑ‚Ð°Ñ€Ñ‚Ð°Ð¼Ð¸, Ð°Ð²Ñ‚Ð¾Ð³ÐµÐ½ÐµÑ€Ð°Ñ†Ð¸Ñ Ð¿Ñ€Ð¸ Ð¿ÐµÑ€Ð²Ð¾Ð¼ ÑÑ‚Ð°Ñ€Ñ‚Ðµ
def _session_secret():
    with _db_lock, _db() as c:
        row = c.execute("SELECT value FROM meta WHERE key = 'session_secret'").fetchone()
        if row:
            return row["value"]
        secret = uuid.uuid4().hex + uuid.uuid4().hex
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('session_secret', ?)",
                  (secret,))
        return secret


SESSION_SECRET = _session_secret()  # Ð¿Ð¾Ð´Ð¿Ð¸ÑÑŒ cookie-ÑÐµÑÑÐ¸Ð¹ (ÑˆÐ¸Ð¼Ñ‹ Ð±ÐµÑ€ÑƒÑ‚ Ð¿Ñ€Ð¸ ÑÑ‚Ð°Ñ€Ñ‚Ðµ)
_PBKDF_ROUNDS = 200_000


def haversine_km(a, b):
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def osrm_get(path, params):
    global _osrm_base
    bases = ([_osrm_base] if _osrm_base else []) + [u for u in OSRM_URLS if u != _osrm_base]
    for base in bases:
        try:
            resp = requests.get(f"{base}{path}", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == "Ok":
                _osrm_base = base
                return data
            log.warning("OSRM %s: code=%s", base, data.get("code"))
        except Exception as e:  # noqa: BLE001
            log.warning("OSRM %s Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½: %s", base, e)
    return None


def osrm_table(points):
    """ÐœÐ°Ñ‚Ñ€Ð¸Ñ†Ñ‹ Ð²Ñ€ÐµÐ¼ÐµÐ½Ð¸ (ÑÐµÐº) Ð¸ Ñ€Ð°ÑÑÑ‚Ð¾ÑÐ½Ð¸Ñ (Ð¼) Ð¿Ð¾ Ð´Ð¾Ñ€Ð¾Ð³Ð°Ð¼. (None, None), ÐµÑÐ»Ð¸ OSRM Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½."""
    coords = ";".join(f"{p['lng']:.6f},{p['lat']:.6f}" for p in points)
    data = osrm_get(f"/table/v1/driving/{coords}", {"annotations": "duration,distance"})
    durations = data and data.get("durations")
    if not durations or any(d is None for row in durations for d in row):
        return None, None
    distances = data.get("distances")
    if distances and any(d is None for row in distances for d in row):
        distances = None
    return durations, distances


def osrm_geometry(points):
    """Ð“ÐµÐ¾Ð¼ÐµÑ‚Ñ€Ð¸Ñ Ð¼Ð°Ñ€ÑˆÑ€ÑƒÑ‚Ð° Ð¿Ð¾ Ð´Ð¾Ñ€Ð¾Ð³Ð°Ð¼: ÑÐ¿Ð¸ÑÐ¾Ðº [lat, lng]. None Ð¿Ñ€Ð¸ ÑÐ±Ð¾Ðµ."""
    coords = ";".join(f"{p['lng']:.6f},{p['lat']:.6f}" for p in points)
    data = osrm_get(f"/route/v1/driving/{coords}",
                    {"overview": "full", "geometries": "geojson"})
    try:
        return [[c[1], c[0]] for c in data["routes"][0]["geometry"]["coordinates"]]
    except (KeyError, IndexError, TypeError):
        return None


# ---------- OpenRouteService (Ñ Ð·Ð°Ñ‰Ð¸Ñ‚Ð¾Ð¹ Ð¾Ñ‚ Ð¸ÑÑ‡ÐµÑ€Ð¿Ð°Ð½Ð¸Ñ ÐºÐ²Ð¾Ñ‚Ñ‹) ----------

def _seconds_to_utc_midnight():
    now = datetime.now(timezone.utc)
    return 24 * 3600 - (now.hour * 3600 + now.minute * 60 + now.second)


def _ors_available():
    """Ð¡Ð¼ÐµÐ½Ð° ÑÑƒÑ‚Ð¾Ðº Ð¾Ð±Ð½ÑƒÐ»ÑÐµÑ‚ ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸Ðº Ð¸ ÑÐ½Ð¸Ð¼Ð°ÐµÑ‚ Ð¾Ñ‚ÐºÐ»ÑŽÑ‡ÐµÐ½Ð¸Ðµ."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if ORS_STATE["day"] != day:
        ORS_STATE.update(day=day, used=0, disabled_until=None, last_error=None)
    if ORS_STATE["disabled_until"] and time.time() < ORS_STATE["disabled_until"]:
        return False
    return ORS_STATE["used"] < ORS_SOFT_LIMIT


def ors_post(path, body):
    """POST Ðº ORS. None => ORS Ð½ÐµÐ´Ð¾ÑÑ‚ÑƒÐ¿ÐµÐ½ Ð¸Ð»Ð¸ ÐºÐ²Ð¾Ñ‚Ð° Ð¸ÑÑ‡ÐµÑ€Ð¿Ð°Ð½Ð° (ÑƒÑ…Ð¾Ð´Ð¸Ð¼ Ð½Ð° OSRM)."""
    if not _ors_available():
        return None
    try:
        resp = requests.post(f"{ORS_BASE}{path}", json=body,
                             headers={"Authorization": ORS_KEY}, timeout=15)
        if resp.status_code in (401, 402, 403, 429):
            try:
                msg = str(resp.json().get("error", ""))[:160]
            except Exception:  # noqa: BLE001
                msg = resp.text[:160]
            daily = any(w in msg.lower() for w in ("daily", "quota", "exceeded"))
            ORS_STATE["disabled_until"] = (time.time() + _seconds_to_utc_midnight()
                                           if daily else time.time() + 300)
            ORS_STATE["last_error"] = f"HTTP {resp.status_code}: {msg}"
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # ÑÐµÑ‚ÑŒ/Ñ‚Ð°Ð¹Ð¼Ð°ÑƒÑ‚ â€” ÐºÐ¾Ñ€Ð¾Ñ‚ÐºÐ°Ñ Ð¿Ð°ÑƒÐ·Ð° Ð¸ Ñ„Ð¾Ð»Ð±ÑÐº
        ORS_STATE["disabled_until"] = time.time() + 60
        ORS_STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:160]
        return None
    ORS_STATE["used"] += 1
    return data


def ors_matrix(points):
    body = {"locations": [[p["lng"], p["lat"]] for p in points],
            "metrics": ["duration", "distance"]}
    data = ors_post("/v2/matrix/driving-car", body)
    try:
        durations = data["durations"]
        if any(v is None for row in durations for v in row):
            return None, None
        return durations, data.get("distances")
    except (AttributeError, KeyError, TypeError):
        return None, None


def ors_geometry(points):
    data = ors_post("/v2/directions/driving-car/geojson",
                    {"coordinates": [[p["lng"], p["lat"]] for p in points]})
    try:
        return [[c[1], c[0]] for c in data["features"][0]["geometry"]["coordinates"]]
    except (AttributeError, KeyError, IndexError, TypeError):
        return None


def ors_status():
    _ors_available()  # Ð¾Ð±Ð½Ð¾Ð²Ð»ÑÐµÑ‚ ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸ÐºÐ¸ Ð¿Ñ€Ð¸ ÑÐ¼ÐµÐ½Ðµ ÑÑƒÑ‚Ð¾Ðº
    paused = bool(ORS_STATE["disabled_until"] and time.time() < ORS_STATE["disabled_until"])
    return {"used": ORS_STATE["used"], "soft_limit": ORS_SOFT_LIMIT,
            "paused": paused, "last_error": ORS_STATE["last_error"]}


_MATRIX_CACHE = {}          # ÐºÐ»ÑŽÑ‡(Ñ‚Ð¾Ñ‡ÐºÐ¸) -> (ts, durations, distances, provider)
_MATRIX_TTL = 1800          # 30 Ð¼Ð¸Ð½ÑƒÑ‚: Ð´Ð¾Ñ€Ð¾Ð¶Ð½Ð°Ñ ÑÐµÑ‚ÑŒ Ð½Ðµ Ð¼ÐµÐ½ÑÐµÑ‚ÑÑ Ñ‚Ð°Ðº Ð±Ñ‹ÑÑ‚Ñ€Ð¾
_MATRIX_CACHE_MAX = 40


def _matrix_key(points):
    # Ð´ÐµÐ¿Ð¾ â€” Ð½Ð° ÑÐ²Ð¾Ñ‘Ð¼ Ð¼ÐµÑÑ‚Ðµ: Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†Ð° Ð¸Ð½Ð´ÐµÐºÑÐ½Ð°, Ð¾Ð´Ð¸Ð½Ð°ÐºÐ¾Ð²Ñ‹Ð¹ Ð½Ð°Ð±Ð¾Ñ€ Ñ‚Ð¾Ñ‡ÐµÐº
    # Ñ Ð´Ñ€ÑƒÐ³Ð¸Ð¼ Ð´ÐµÐ¿Ð¾ (Ð´ÐµÐ¿Ð¾ = Ñ‡ÐµÐ¹-Ñ‚Ð¾ Ð°Ð´Ñ€ÐµÑ) Ð½Ðµ Ð´Ð¾Ð»Ð¶ÐµÐ½ Ð¿Ð¾Ð¿Ð°Ð´Ð°Ñ‚ÑŒ Ð½Ð° Ñ‡ÑƒÐ¶Ð¾Ð¹ ÐºÑÑˆ
    return (tuple((round(points[0]["lat"], 5), round(points[0]["lng"], 5))),
            tuple(sorted((round(p["lat"], 5), round(p["lng"], 5)) for p in points[1:])))


def _cache_matrix(key, value):
    if len(_MATRIX_CACHE) >= _MATRIX_CACHE_MAX:  # Ð¿Ñ€Ð¾ÑÑ‚Ð°Ñ Ð²Ñ‹Ñ‚ÐµÑÐ½ÑÑŽÑ‰Ð°Ñ Ñ‡Ð¸ÑÑ‚ÐºÐ°
        oldest = min(_MATRIX_CACHE, key=lambda k: _MATRIX_CACHE[k][0])
        _MATRIX_CACHE.pop(oldest, None)
    _MATRIX_CACHE[key] = (time.time(), *value)


def routing_table(points):
    """ÐœÐ°Ñ‚Ñ€Ð¸Ñ†Ð° Ð²Ñ€ÐµÐ¼ÐµÐ½Ð¸/Ñ€Ð°ÑÑÑ‚Ð¾ÑÐ½Ð¸Ñ: ORS -> OSRM (FOSSGIS -> Ð´ÐµÐ¼Ð¾) -> offline.

    Ð ÐµÐ·ÑƒÐ»ÑŒÑ‚Ð°Ñ‚ ÐºÑÑˆÐ¸Ñ€ÑƒÐµÑ‚ÑÑ Ð¿Ð¾ Ð½Ð°Ð±Ð¾Ñ€Ñƒ Ñ‚Ð¾Ñ‡ÐµÐº (30 Ð¼Ð¸Ð½): Ð¿Ð¾Ð²Ñ‚Ð¾Ñ€Ð½Ñ‹Ð¹ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚ Ñ‚Ð¾Ð³Ð¾ Ð¶Ðµ
    Ð½Ð°Ð±Ð¾Ñ€Ð° Ð½Ðµ Ñ‚Ñ€Ð°Ñ‚Ð¸Ñ‚ ÐºÐ²Ð¾Ñ‚Ñƒ Ð²Ð½ÐµÑˆÐ½Ð¸Ñ… ÑÐµÑ€Ð²Ð¸ÑÐ¾Ð² Ð¸ Ð·Ð°Ð½Ð¸Ð¼Ð°ÐµÑ‚ Ð¼Ð¸Ð»Ð»Ð¸ÑÐµÐºÑƒÐ½Ð´Ñ‹.
    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ (durations|None, distances|None, provider).
    """
    key = _matrix_key(points)
    hit = _MATRIX_CACHE.get(key)
    if hit and time.time() - hit[0] < _MATRIX_TTL:
        ts, durations, distances, provider = hit
        return durations, distances, provider
    durations = distances = None
    provider = "offline"
    if len(points) <= ORS_MAX_POINTS:
        durations, distances = ors_matrix(points)
        if durations is not None:
            provider = "ORS"
    if durations is None:
        durations, distances = osrm_table(points)
        if durations is not None:
            provider = "OSRM"
    _cache_matrix(key, (durations, distances, provider))
    return durations, distances, provider


def routing_geometry(points):
    """Ð“ÐµÐ¾Ð¼ÐµÑ‚Ñ€Ð¸Ñ Ð¼Ð°Ñ€ÑˆÑ€ÑƒÑ‚Ð°: ORS -> OSRM. None Ð¿Ñ€Ð¸ Ð¿Ð¾Ð»Ð½Ð¾Ð¼ ÑÐ±Ð¾Ðµ."""
    if len(points) <= ORS_MAX_POINTS:
        geom = ors_geometry(points)
        if geom:
            return geom
    return osrm_geometry(points)


def build_time_matrix(points, settings):
    """ÐœÐ°Ñ‚Ñ€Ð¸Ñ†Ð° Ð²Ñ€ÐµÐ¼ÐµÐ½Ð¸ Ð² Ð¼Ð¸Ð½ÑƒÑ‚Ð°Ñ….

    Ð’Ñ€ÐµÐ¼Ñ Ð´ÑƒÐ³Ð¸ = (OSRM-Ð²Ñ€ÐµÐ¼Ñ Ã— ÐºÐ¾ÑÑ„Ñ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚ Ð¿Ñ€Ð¾Ð±Ð¾Ðº)
               + (Ñ€Ð°ÑÑÑ‚Ð¾ÑÐ½Ð¸Ðµ Ã— Ð·Ð°Ð´ÐµÑ€Ð¶ÐºÐ° Ð½Ð° ÑÐ²ÐµÑ‚Ð¾Ñ„Ð¾Ñ€Ð°Ñ…, Ñ/ÐºÐ¼)
               + Ð²Ñ€ÑƒÑ‡ÐµÐ½Ð¸Ðµ (Ð½Ð° Ð´ÑƒÐ³Ðµ Ð¿Ñ€Ð¸Ð±Ñ‹Ñ‚Ð¸Ñ Ð² Ð·Ð°ÐºÐ°Ð·).
    OSRM Ð¾Ñ‚Ð´Ð°Ñ‘Ñ‚ Ð²Ñ€ÐµÐ¼Ñ ÑÐ²Ð¾Ð±Ð¾Ð´Ð½Ð¾Ð³Ð¾ Ð¿Ð¾Ñ‚Ð¾ÐºÐ°: Ð±ÐµÐ· Ð¿Ñ€Ð¾Ð±Ð¾Ðº Ð¸ Ð±ÐµÐ· Ð¾ÑÑ‚Ð°Ð½Ð¾Ð²Ð¾Ðº Ð½Ð°
    Ñ€ÐµÐ³ÑƒÐ»Ð¸Ñ€ÑƒÐµÐ¼Ñ‹Ñ… Ð¿ÐµÑ€ÐµÐºÑ€Ñ‘ÑÑ‚ÐºÐ°Ñ…, Ð¿Ð¾ÑÑ‚Ð¾Ð¼Ñƒ ÑÐ²ÐµÑ‚Ð¾Ñ„Ð¾Ñ€Ñ‹ Ð¼Ð¾Ð´ÐµÐ»Ð¸Ñ€ÑƒÑŽÑ‚ÑÑ Ð¾Ñ‚Ð´ÐµÐ»ÑŒÐ½Ð¾Ð¹
    Ð½Ð°Ð´Ð±Ð°Ð²ÐºÐ¾Ð¹ Ð·Ð° ÐºÐ¸Ð»Ð¾Ð¼ÐµÑ‚Ñ€ Ð¿ÑƒÑ‚Ð¸ (Ð¿Ð¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ 15 Ñ/ÐºÐ¼ â‰ˆ ÑÐ²ÐµÑ‚Ð¾Ñ„Ð¾Ñ€ ÐºÐ°Ð¶Ð´Ñ‹Ðµ
    ~1.2 ÐºÐ¼ Ð¸ ~18 Ñ Ð¾Ð¶Ð¸Ð´Ð°Ð½Ð¸Ñ). Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‚ Ð² Ð´ÐµÐ¿Ð¾ ÑƒÑ‡Ð¸Ñ‚Ñ‹Ð²Ð°ÐµÑ‚ÑÑ.
    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ (Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†Ð°, Ð´Ð¾Ñ€Ð¾Ð³Ð¸_Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ð½Ñ‹, Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†Ð°_Ñ€Ð°ÑÑÑ‚Ð¾ÑÐ½Ð¸Ð¹_Ð¼|None, Ð¿Ñ€Ð¾Ð²Ð°Ð¹Ð´ÐµÑ€).
    """
    handover = max(0, int(settings["handover_min"]))
    traffic = max(1.0, float(settings.get("traffic", 1.3)))
    lights = max(0.0, float(settings.get("lights_sec_per_km", 24))) / 60.0  # Ð¼Ð¸Ð½/ÐºÐ¼
    speed = max(5.0, float(settings["speed_kmh"]))
    n = len(points)
    durations, distances, provider = (routing_table(points) if n >= 2
                                      else (None, None, "offline"))
    m = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if durations is not None:
                minutes = durations[i][j] / 60.0 * traffic
                km = (distances[i][j] / 1000.0 if distances
                      else haversine_km(points[i], points[j]) * ROAD_FACTOR)
            else:  # Ð·Ð°Ð¿Ð°ÑÐ½Ð¾Ð¹ Ð²Ð°Ñ€Ð¸Ð°Ð½Ñ‚: Ð¾Ñ†ÐµÐ½ÐºÐ° Ð¿Ð¾ Ð¿Ñ€ÑÐ¼Ð¾Ð¹
                km = haversine_km(points[i], points[j]) * ROAD_FACTOR
                minutes = km / speed * 60
            minutes += km * lights
            t = max(1, int(round(minutes)))
            if j != 0:
                t += handover
            m[i][j] = t
    return m, durations is not None, distances, provider


# ÐŸÐ¾Ñ‡Ð°ÑÐ¾Ð²Ñ‹Ðµ ÐºÐ¾ÑÑ„Ñ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ñ‹ Ð´Ð¾Ñ€Ð¾Ð¶Ð½Ð¾Ð¹ Ð½Ð°Ð³Ñ€ÑƒÐ·ÐºÐ¸ (Ð“Ð¾Ð¼ÐµÐ»ÑŒ). Ð£Ñ‚Ñ€ÐµÐ½Ð½Ð¸Ð¹ Ð¿Ð¸Ðº Ñ€ÐµÐ·ÐºÐ¸Ð¹:
# Ñ ~7:05 (Ð¿Ñ€Ð¸Ð³Ð¾Ñ€Ð¾Ð´Ð½Ñ‹Ðµ Ð¿Ð¾Ñ‚Ð¾ÐºÐ¸ ÐÐ¾Ð²Ð¾Ð±ÐµÐ»Ð¸Ñ†Ñ‹/Ð Ð¾Ð¼Ð°Ð½Ð¾Ð²Ð¸Ñ‡ÐµÐ¹, ÑˆÐºÐ¾Ð»Ñ‹), ÑÐ°Ð¼Ñ‹Ð¹ Ð¿Ð»Ð¾Ñ‚Ð½Ñ‹Ð¹
# 7:15-8:40; Ð²ÐµÑ‡ÐµÑ€Ð½Ð¸Ð¹ 17:00-19:00 (Ñ†ÐµÐ½Ñ‚Ñ€, Ð¼Ð¾ÑÑ‚, Ð²Ð¾ÐºÐ·Ð°Ð»); Ð¾Ð±ÐµÐ´ÐµÐ½Ð½Ñ‹Ð¹ Ð¼Ð¸Ð½Ð¸-Ð¿Ð¸Ðº.
# Ð˜ÑÑ‚Ð¾Ñ‡Ð½Ð¸ÐºÐ¸: Ð¼ÐµÑÑ‚Ð½Ñ‹Ðµ Ð¡ÐœÐ˜ (BGmedia, ÑÐµÐ½Ñ‚ÑÐ±Ñ€ÑŒ 2026), Ñ€Ð°Ð·Ð±Ð¾Ñ€Ñ‹ Ð¿Ñ€Ð¾ÑÐ¿ÐµÐºÑ‚Ð° Ð›ÐµÐ½Ð¸Ð½Ð°.
# Ð¨ÐºÐ°Ð»Ð° ÐºÐ¾Ð½ÑÐµÑ€Ð²Ð°Ñ‚Ð¸Ð²Ð½Ð°Ñ: Ð¿Ð¸Ðº +35%, Ð¼ÐµÐ¶Ð¿Ð¸Ðº -5..-10%.
_HOURLY_TRAFFIC = {0: 0.90, 1: 0.90, 2: 0.90, 3: 0.90, 4: 0.90, 5: 0.90,
                   6: 1.00, 7: 1.20, 8: 1.35, 9: 1.15, 10: 1.00, 11: 1.00,
                   12: 1.10, 13: 1.05, 14: 1.00, 15: 1.00, 16: 1.05,
                   17: 1.20, 18: 1.35, 19: 1.15, 20: 1.00, 21: 0.95,
                   22: 0.95, 23: 0.90}
_LATE_WEIGHT = 25    # ÑˆÑ‚Ñ€Ð°Ñ„ Ð·Ð° Ð¼Ð¸Ð½ÑƒÑ‚Ñƒ Ð¾Ð¿Ð¾Ð·Ð´Ð°Ð½Ð¸Ñ Ðº Ð´ÐµÐ´Ð»Ð°Ð¹Ð½Ñƒ
_MAX_ROUNDS = 3      # Ð¼Ð°ÐºÑÐ¸Ð¼Ð°Ð»ÑŒÐ½Ð¾Ðµ Ñ‡Ð¸ÑÐ»Ð¾ Â«Ð·Ð°ÐµÐ·Ð´Ð¾Ð²Â» Ð½Ð° ÐºÑƒÑ€ÑŒÐµÑ€Ð°


def _deadline_rel_min(hhmm, now_hm):
    """Ð”ÐµÐ´Ð»Ð°Ð¹Ð½ Â«Ð¾Ð±ÐµÑ‰Ð°Ð»Ð¸ Ðº HH:MMÂ» Ð² Ð¼Ð¸Ð½ÑƒÑ‚Ð°Ñ… Ð¾Ñ‚ Ñ‚ÐµÐºÑƒÑ‰ÐµÐ³Ð¾ Ð¼Ð¾Ð¼ÐµÐ½Ñ‚Ð°. None, ÐµÑÐ»Ð¸ Ð½Ðµ Ð·Ð°Ð´Ð°Ð½."""
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", (hhmm or "").strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2))) - now_hm


_APPROACH_RADIUS_KM = 2.5  # Ð±Ð»Ð¸Ð¶Ðµ Ðº Ñ†ÐµÐ½Ñ‚Ñ€Ñƒ â€” Ð¿Ð»Ð¾Ñ‚Ð½Ð°Ñ Ð·Ð°ÑÑ‚Ñ€Ð¾Ð¹ÐºÐ°, Ð¿Ð°Ñ€ÐºÐ¾Ð²ÐºÐ° Ð´Ð¾Ð»ÑŒÑˆÐµ


def _approach_map(points, home, k_orders, settings):
    """Ð”Ð¾Ð±Ð°Ð²ÐºÐ° Ð½Ð° Ð¿Ð°Ñ€ÐºÐ¾Ð²ÐºÑƒ/Ð¿Ð¾Ð´ÑŠÐµÐ·Ð´ Ð´Ð»Ñ Ð·Ð°ÐºÐ°Ð·Ð¾Ð² (ÑƒÐ·Ð»Ñ‹ k_orders..) Ð¾Ñ‚ Ñ‚Ð¾Ñ‡ÐºÐ¸ home.

    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ {ÑƒÐ·ÐµÐ»_Ð·Ð°ÐºÐ°Ð·Ð°: Ð¼Ð¸Ð½ÑƒÑ‚Ñ‹}; Ñ†ÐµÐ½Ñ‚Ñ€Ñƒ Ð±Ð»Ð¸Ð¶Ðµ _APPROACH_RADIUS_KM - Â«Ñ†ÐµÐ½Ñ‚Ñ€Â».
    """
    near = max(0, int(settings.get("approach_center_min", 4)))
    far = max(0, int(settings.get("approach_far_min", 2)))
    out = {}
    for j in range(k_orders, len(points)):
        km = haversine_km(home, points[j])
        out[j] = near if km <= _APPROACH_RADIUS_KM else far
    return out


def _eta_pass(stop_nodes, delay, matrix, settings, solved_dt, appr=None, home=0,
              spd_factor=1.0):
    """ETA Ð¾ÑÑ‚Ð°Ð½Ð¾Ð²Ð¾Ðº Ð¿Ð¾ÐµÐ·Ð´ÐºÐ¸ (Ð¼Ð¸Ð½ÑƒÑ‚Ñ‹ Ð¾Ñ‚ solved_dt) Ñ Ð¿Ð¾Ñ‡Ð°ÑÐ¾Ð²Ñ‹Ð¼Ð¸ ÐºÐ¾ÑÑ„Ñ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð°Ð¼Ð¸.

    ÐœÐ°Ñ‚Ñ€Ð¸Ñ†Ð° Ð¿Ð¾ÑÑ‚Ñ€Ð¾ÐµÐ½Ð° Ñ Ð±Ð°Ð·Ð¾Ð²Ñ‹Ð¼ ÐºÐ¾ÑÑ„Ñ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚Ð¾Ð¼ traffic: Ð´ÑƒÐ³Ð° Ð¾Ñ‡Ð¸Ñ‰Ð°ÐµÑ‚ÑÑ Ð¾Ñ‚ Ð½ÐµÐ³Ð¾
    Ð¸ Ð´Ð¾Ð¼Ð½Ð¾Ð¶Ð°ÐµÑ‚ÑÑ Ð½Ð° ÐºÐ¾ÑÑ„Ñ„Ð¸Ñ†Ð¸ÐµÐ½Ñ‚ Ñ‡Ð°ÑÐ° Ñ„Ð°ÐºÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾Ð³Ð¾ Ð²Ñ‹ÐµÐ·Ð´Ð° Ð½Ð° Ð´ÑƒÐ³Ñƒ.
    spd_factor â€” Ð¸Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»ÑŒÐ½Ñ‹Ð¹ Ð¼Ð½Ð¾Ð¶Ð¸Ñ‚ÐµÐ»ÑŒ ÐºÑƒÑ€ÑŒÐµÑ€Ð° (Ð·Ð°Ð¼ÐµÐ´Ð»ÐµÐ½Ð½Ð°Ñ/Ð±Ñ‹ÑÑ‚Ñ€Ð°Ñ ÐµÐ·Ð´Ð°).
    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ (ÑÐ¿Ð¸ÑÐ¾Ðº ETA Ð¾ÑÑ‚Ð°Ð½Ð¾Ð²Ð¾Ðº, Ð¿Ð¾Ð»Ð½Ð°Ñ Ð´Ð»Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð¾ÑÑ‚ÑŒ Ð¿Ð¾ÐµÐ·Ð´ÐºÐ¸).
    """
    hourly = int(settings.get("hour_traffic", 1))
    base_traffic = max(1.0, float(settings.get("traffic", 1.3)))
    handover = max(0, int(settings["handover_min"]))
    t = float(delay)
    node = home
    etas = []
    for g in stop_nodes:
        hour = (solved_dt + timedelta(minutes=t)).hour
        factor = _HOURLY_TRAFFIC.get(hour, 1.0) if hourly else 1.0
        travel = (matrix[node][g] - handover) / base_traffic * factor * spd_factor
        t += travel + handover + (appr.get(g, 0) if appr else 0)
        etas.append(int(round(t)))
        node = g
    hour = (solved_dt + timedelta(minutes=t)).hour
    factor = _HOURLY_TRAFFIC.get(hour, 1.0) if hourly else 1.0
    t += matrix[node][home] / base_traffic * factor * spd_factor
    return etas, int(round(t))


def solve_plan(include_away=True, with_geometry=True, helpers=None, force=None,
               point_id=None):
    """Ð Ð°Ð·Ð²Ð¾Ð·ÐºÐ° ÐžÐ”ÐÐžÐ“Ðž Ð´ÐµÐ¿Ð¾ (point_id; None = Ñ‚Ð¾Ñ‡ÐºÐ° Ð²Ñ‹Ð·Ñ‹Ð²Ð°ÑŽÑ‰ÐµÐ³Ð¾).

    include_away=False â€” ÑÑ†ÐµÐ½Ð°Ñ€Ð¸Ð¹ Â«Ð½Ðµ Ð¶Ð´Ð°Ñ‚ÑŒÂ»: Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐºÑƒÑ€ÑŒÐµÑ€Ñ‹ Ð½Ð° Ð±Ð°Ð·Ðµ.

    Ð—Ð°ÐºÐ°Ð·Ð¾Ð² Ð±Ð¾Ð»ÑŒÑˆÐµ, Ñ‡ÐµÐ¼ Ð²Ð»ÐµÐ·Ð°ÐµÑ‚ Ð² Ð¾Ð´Ð¸Ð½ Ð·Ð°ÐµÐ·Ð´ (Ð²Ð¼ÐµÑÑ‚Ð¸Ð¼Ð¾ÑÑ‚ÑŒ x ÐºÑƒÑ€ÑŒÐµÑ€Ñ‹), Ñ€ÐµÑˆÐ°ÐµÑ‚ÑÑ
    Ð½ÐµÑÐºÐ¾Ð»ÑŒÐºÐ¸Ð¼Ð¸ Ñ€Ð°ÑƒÐ½Ð´Ð°Ð¼Ð¸: ÐºÑƒÑ€ÑŒÐµÑ€ Ð²ÐµÑ€Ð½Ñ‘Ñ‚ÑÑ Ð½Ð° Ð±Ð°Ð·Ñƒ Ð¸ Ð¿Ð¾ÐµÐ´ÐµÑ‚ Ð²Ñ‚Ð¾Ñ€Ñ‹Ð¼ Ð·Ð°ÐµÐ·Ð´Ð¾Ð¼
    (Ð·Ð°Ð´ÐµÑ€Ð¶ÐºÐ° ÑÑ‚Ð°Ñ€Ñ‚Ð° = Ð´Ð»Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð¾ÑÑ‚ÑŒ Ð¿ÐµÑ€Ð²Ð¾Ð³Ð¾ Ð·Ð°ÐµÐ·Ð´Ð° + Ð¿ÐµÑ€ÐµÐ·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ°).

    helpers: {courier_id: point_id} â€” Ñ€Ð°Ð·Ð¾Ð²Ð°Ñ Â«Ð¿Ð¾Ð¼Ð¾Ñ‰ÑŒÂ»: ÐºÑƒÑ€ÑŒÐµÑ€ Ð² ÑÑ‚Ð¾Ð¼ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ðµ
    ÑÑ‚Ð°Ñ€Ñ‚ÑƒÐµÑ‚ Ñ Ñ‡ÑƒÐ¶Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ð¸ Ð±ÐµÑ€Ñ‘Ñ‚ Ð¼Ð°ÐºÑÐ¸Ð¼ÑƒÐ¼ Ð¾Ð´Ð¸Ð½ Ð·Ð°ÐºÐ°Ð·. Ð•Ð³Ð¾ ÑÐ¾Ð±ÑÑ‚Ð²ÐµÐ½Ð½Ð°Ñ
    Ñ‚Ð¾Ñ‡ÐºÐ° Ð¸ ÑÑ‚Ð°Ñ‚ÑƒÑ Ð½Ðµ Ð¼ÐµÐ½ÑÑŽÑ‚ÑÑ.
    """
    helpers = helpers or {}
    point_id = point_id or _my_point()
    force_ids = {cid for cid in (force or [])
                 if any(c["id"] == cid for c in STATE["couriers"])}
    settings = STATE["settings"]
    orders = [o for o in STATE["orders"]
              if (o.get("status") or "ready") == "ready" and _obj_point(o) == point_id]
    mine = [c for c in STATE["couriers"] if _obj_point(c) == point_id]
    active = [c for c in mine
              if c["status"] == "base" or (include_away and c["status"] == "away")]
    helper_ids = {cid for cid in helpers
                  if any(c["id"] == cid for c in STATE["couriers"])}
    couriers = active + [c for c in STATE["couriers"]
                         if c["id"] in helper_ids and c not in active]
    if not STATE.get("points"):
        raise ValueError("Ð¡Ð½Ð°Ñ‡Ð°Ð»Ð° Ð·Ð°Ð´Ð°Ð¹Ñ‚Ðµ Ð¼ÐµÑÑ‚Ð¾ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ð·Ð°ÐºÐ°Ð·Ð¾Ð² (Ñ‚Ð¾Ñ‡ÐºÑƒ Ð½Ð° ÐºÐ°Ñ€Ñ‚Ðµ)")
    if not orders:
        raise ValueError("ÐÐµÑ‚ Ð³Ð¾Ñ‚Ð¾Ð²Ñ‹Ñ… Ð·Ð°ÐºÐ°Ð·Ð¾Ð², Ð´Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ Ñ…Ð¾Ñ‚Ñ Ð±Ñ‹ Ð¾Ð´Ð¸Ð½")
    if not couriers:
        raise ValueError("ÐÐµÑ‚ Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ñ… ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð², Ð´Ð¾Ð±Ð°Ð²ÑŒÑ‚Ðµ ÐºÑƒÑ€ÑŒÐµÑ€Ð°")

    def _eff_home(c):
        """Ð¢Ð¾Ñ‡ÐºÐ° ÑÑ‚Ð°Ñ€Ñ‚Ð° ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð² ÑÑ‚Ð¾Ð¼ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ðµ (Ð¿Ð¾Ð¼Ð¾Ñ‰Ð½Ð¸Ðº ÐµÐ´ÐµÑ‚ Ñ Ñ‡ÑƒÐ¶Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸)."""
        hp = helpers.get(c["id"])
        if hp:
            p = next((p for p in STATE["points"] if p["id"] == hp), None)
            if p:
                return p
        return _home_point(c)

    # Ð£Ð·Ð»Ñ‹ Ð¼Ð°Ñ‚Ñ€Ð¸Ñ†Ñ‹: 0..K-1 - ÑƒÐ½Ð¸ÐºÐ°Ð»ÑŒÐ½Ñ‹Ðµ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ñ… ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð², Ð´Ð°Ð»ÑŒÑˆÐµ Ð·Ð°ÐºÐ°Ð·Ñ‹.
    # ÐšÐ°Ð¶Ð´Ñ‹Ð¹ ÐºÑƒÑ€ÑŒÐµÑ€ ÑÑ‚Ð°Ñ€Ñ‚ÑƒÐµÑ‚ Ð¸ Ñ„Ð¸Ð½Ð¸ÑˆÐ¸Ñ€ÑƒÐµÑ‚ Ð² Ð¡Ð’ÐžÐ•Ð™ Ñ‚Ð¾Ñ‡ÐºÐµ (RoutingIndexManager starts).
    homes, home_idx = [], {}
    for c in couriers:
        hp = _eff_home(c)
        if hp["id"] not in home_idx:
            home_idx[hp["id"]] = len(homes)
            homes.append(hp)
    K = len(homes)
    points = homes + orders
    matrix, by_roads, distances, provider = build_time_matrix(points, settings)
    appr_home = [_approach_map(points, h, K, settings) for h in homes]
    solved_dt = _now()
    now_hm = solved_dt.hour * 60 + solved_dt.minute
    handover = max(0, int(settings["handover_min"]))
    auto_prio = int(settings.get("auto_prio_min", 0) or 0)
    max_orders = int(settings["max_orders"])
    reload_min = max(0, int(settings.get("reload_min", 10)))

    # Ð˜Ð½Ð´Ð¸Ð²Ð¸Ð´ÑƒÐ°Ð»ÑŒÐ½Ð°Ñ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚ÑŒ: Ð´Ð¾Ñ€Ð¾Ð¶Ð½Ð¾Ðµ Ð²Ñ€ÐµÐ¼Ñ Ð¼Ð°ÑÑˆÑ‚Ð°Ð±Ð¸Ñ€ÑƒÐµÑ‚ÑÑ Ð½Ð° default/Ð·Ð°Ð¼ÐµÑ€.
    default_kmh = max(5.0, float(settings.get("speed_kmh", 60)))
    speeds = {c["id"]: _courier_speed(c, settings) for c in couriers}
    spd_factor = {cid: max(0.25, min(4.0, default_kmh / kmh))
                  for cid, (kmh, _src) in speeds.items()}

    deadline_rel, eff_prio, auto_flag = {}, {}, {}
    for i, o in enumerate(orders):
        g = K + i
        deadline_rel[g] = _deadline_rel_min(o.get("deadline"), now_hm)
        age_min = 0
        try:
            age_min = int((solved_dt - datetime.fromisoformat(o["created_at"])
                           ).total_seconds() // 60)
        except (KeyError, ValueError, TypeError):
            pass
        auto_flag[g] = bool(auto_prio > 0 and age_min >= auto_prio)
        eff_prio[g] = bool(o.get("prio") or auto_flag[g])

    def _start_delay(c):
        """ÐšÐ¾Ð³Ð´Ð° ÐºÑƒÑ€ÑŒÐµÑ€ ÑÐ¼Ð¾Ð¶ÐµÑ‚ Ð²Ñ‹ÐµÑ…Ð°Ñ‚ÑŒ ÑÐ¾ ÑÐ²Ð¾ÐµÐ¹ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ñ Ð½Ð¾Ð²Ð¾Ð¹ Ð¿Ð°Ñ€Ñ‚Ð¸ÐµÐ¹."""
        if c["status"] != "away":
            return 0
        g = _courier_geo(c, _home_point(c))
        if g:  # Ð¶Ð¸Ð²Ð°Ñ Ð³ÐµÐ¾ Ñ‚Ð¾Ñ‡Ð½ÐµÐµ Ñ€ÑƒÑ‡Ð½Ð¾Ð¹ Ð¾Ñ†ÐµÐ½ÐºÐ¸
            if g.get("to_point_min") is not None:
                # Ð·Ð°ÐºÐ°Ð·Ñ‹ Ð¿Ñ€ÐµÐ¶Ð½ÐµÐ¹ Ð¿Ð°Ñ€Ñ‚Ð¸Ð¸ ÐµÑ‰Ñ‘ Ð½Ðµ Ð·Ð°Ð±Ñ€Ð°Ð½Ñ‹: Ð´Ð¾ÐµÑ…Ð°Ñ‚ÑŒ + Ð¿Ð¾Ð³Ñ€ÑƒÐ·Ð¸Ñ‚ÑŒÑÑ
                return min(480, g["to_point_min"] + reload_min)
            return g["back_min"]
        return max(0, int(c.get("back_min", 15)))

    avail = {c["id"]: _start_delay(c) for c in couriers}
    home_of = {c["id"]: home_idx[_eff_home(c)["id"]] for c in couriers}
    # Ð¢Ð¾Ñ‡ÐºÐ° Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ ÐºÐ°Ð¶Ð´Ð¾Ð³Ð¾ Ð·Ð°ÐºÐ°Ð·Ð°: Ð²ÐµÐ·Ñ‚Ð¸ ÐµÐ³Ð¾ Ð¼Ð¾Ð³ÑƒÑ‚ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐºÑƒÑ€ÑŒÐµÑ€Ñ‹ ÑÑ‚Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸.
    first_pid = STATE["points"][0]["id"]
    order_pid = {K + i: (o.get("point_id") or first_pid) for i, o in enumerate(orders)}
    home_pid = {c["id"]: _eff_home(c)["id"] for c in couriers}
    trips_by_cid = {}
    remaining = list(range(K, len(points)))

    for round_no in range(_MAX_ROUNDS):
        if not remaining:
            break
        pool = [c for c in couriers if c["id"] not in helper_ids or round_no == 0]
        n_veh = min(len(pool), len(remaining))
        round_couriers = pool[:n_veh]
        round_homes = []
        for c in round_couriers:
            if home_of[c["id"]] not in round_homes:
                round_homes.append(home_of[c["id"]])
        sub = round_homes + remaining        # ÑƒÐ·Ð»Ñ‹ Ñ€Ð°ÑƒÐ½Ð´Ð°: Ð´Ð¾Ð¼Ð°, Ð¿Ð¾Ñ‚Ð¾Ð¼ Ð·Ð°ÐºÐ°Ð·Ñ‹
        pos_of = {a: i for i, a in enumerate(sub)}
        starts = [pos_of[home_of[c["id"]]] for c in round_couriers]
        manager = pywrapcp.RoutingIndexManager(len(sub), n_veh, starts, starts)
        routing = pywrapcp.RoutingModel(manager)

        def make_cb(delay, hpos, appr, allowed, factor):
            def cb(from_index, to_index):
                i, j = sub[manager.IndexToNode(from_index)], sub[manager.IndexToNode(to_index)]
                arc = matrix[i][j]
                if j != 0 and arc > handover:
                    # Ð´ÑƒÐ³Ð° Ð¿Ñ€Ð¸Ð±Ñ‹Ñ‚Ð¸Ñ Ð² Ð·Ð°ÐºÐ°Ð· ÑÐ¾Ð´ÐµÑ€Ð¶Ð¸Ñ‚ Ð²Ñ€ÑƒÑ‡ÐµÐ½Ð¸Ðµ â€” ÐµÐ³Ð¾ Ð½Ðµ Ð¼Ð°ÑÑˆÑ‚Ð°Ð±Ð¸Ñ€ÑƒÐµÐ¼
                    # (arc == 0 â€” Ð¿ÐµÑ‚Ð»Ñ Ð½ÐµÐ¿Ð¾ÑÐµÑ‰Ñ‘Ð½Ð½Ð¾Ð³Ð¾ ÑƒÐ·Ð»Ð°, ÐµÑ‘ Ð½Ðµ Ñ‚Ñ€Ð¾Ð³Ð°ÐµÐ¼)
                    arc = int(round((arc - handover) * factor)) + handover
                cost = arc + (delay if i == sub[hpos] else 0)
                if j >= K:
                    cost += appr.get(j, 0)
                    if j not in allowed:      # Ñ‡ÑƒÐ¶Ð°Ñ Ñ‚Ð¾Ñ‡ÐºÐ° Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ â€” Ð²ÐµÐ·Ñ‚Ð¸ Ð½ÐµÐ»ÑŒÐ·Ñ
                        cost += 1_000_000_000
                return cost
            return cb

        cb_idxs = [routing.RegisterTransitCallback(
                       make_cb(avail[c["id"]], pos_of[home_of[c["id"]]],
                               appr_home[home_of[c["id"]]],
                               {K + i for i, o in enumerate(orders)
                                if order_pid[K + i] == home_pid[c["id"]]
                                and (not o.get("pin") or o["pin"] == c["id"])},
                               spd_factor.get(c["id"], 1.0)))
                   for c in round_couriers]
        for v, cb_idx in enumerate(cb_idxs):
            routing.SetArcCostEvaluatorOfVehicle(cb_idx, v)
        routing.AddConstantDimension(1, max_orders + 1, True, "Orders")
        orders_dim = routing.GetDimensionOrDie("Orders")
        for v, c in enumerate(round_couriers):
            if c["id"] in helper_ids:
                # Ñ€Ð°Ð·Ð¼ÐµÑ€Ð½Ð¾ÑÑ‚ÑŒ ÑÑ‡Ð¸Ñ‚Ð°ÐµÑ‚ Ð´ÑƒÐ³Ð¸: Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ð¹ = 1, Ñ€Ð¾Ð²Ð½Ð¾ Ð¾Ð´Ð¸Ð½ Ð·Ð°ÐºÐ°Ð· = 2
                orders_dim.CumulVar(routing.End(v)).SetRange(2, 2)
            elif round_no == 0 and c["id"] in force_ids:
                # Ð¿ÐµÑ€ÐµÑ‚Ð°Ñ‰ÐµÐ½ Ð² Ð¿Ð»Ð°Ð½ Ð²Ñ€ÑƒÑ‡Ð½ÑƒÑŽ: Ð¾Ð±ÑÐ·Ð°Ð½ Ð²Ð·ÑÑ‚ÑŒ Ñ…Ð¾Ñ‚Ñ Ð±Ñ‹ Ð¾Ð´Ð¸Ð½ Ð·Ð°ÐºÐ°Ð·
                orders_dim.CumulVar(routing.End(v)).SetMin(2)
        routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 60, True, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.SetGlobalSpanCostCoefficient(200)

        # Ð¨Ñ‚Ñ€Ð°Ñ„ Ð·Ð° Ð¾Ð¶Ð¸Ð´Ð°Ð½Ð¸Ðµ Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ¸: Ð¾Ð±Ñ‹Ñ‡Ð½Ñ‹Ð¹ Ð·Ð°ÐºÐ°Ð· 1 Ð¼Ð¸Ð½, Ð¿Ñ€Ð¸Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚Ð½Ñ‹Ð¹ 60,
        # Ð¿Ñ€Ð¾ÑÑ€Ð¾Ñ‡ÐºÐ° Ð´ÐµÐ´Ð»Ð°Ð¹Ð½Ð° 25 (Ð´ÐµÐ´Ð»Ð°Ð¹Ð½ ÑÐ¸Ð»ÑŒÐ½ÐµÐµ Ð¿Ñ€Ð¸Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚Ð°).
        for ln in range(len(sub)):
            g = sub[ln]
            if g < K:
                continue  # Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ - Ð½Ðµ Ð¾ÑÑ‚Ð°Ð½Ð¾Ð²ÐºÐ¸
            idx = manager.NodeToIndex(ln)
            if deadline_rel[g] is not None:
                time_dim.SetCumulVarSoftUpperBound(idx, max(0, deadline_rel[g]),
                                                   _LATE_WEIGHT)
            elif eff_prio[g]:
                time_dim.SetCumulVarSoftUpperBound(idx, 0, _PRIO_WEIGHT)
            else:
                time_dim.SetCumulVarSoftUpperBound(idx, 0, 1)

        # Ð Ð°Ð·Ñ€ÐµÑˆÐ°ÐµÐ¼ Ð¾ÑÑ‚Ð°Ð²Ð¸Ñ‚ÑŒ Ð·Ð°ÐºÐ°Ð· Ð½Ð° ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ð¹ Ð·Ð°ÐµÐ·Ð´: Ð´Ñ€Ð¾Ð¿-Ð²Ð¸Ð·Ð¸Ñ‚ Ñ Ð¿Ð¾Ð´Ð°Ð²Ð»ÑÑŽÑ‰Ð¸Ð¼
        # ÑˆÑ‚Ñ€Ð°Ñ„Ð¾Ð¼. Ð‘ÐµÐ· ÑÑ‚Ð¾Ð³Ð¾ Ñ€Ð°ÑƒÐ½Ð´ Ð±ÐµÐ· Ð¿Ð¾Ð»Ð½Ð¾Ð¹ Ð²Ð¼ÐµÑÑ‚Ð¸Ð¼Ð¾ÑÑ‚Ð¸ Ð±Ñ‹Ð» Ð±Ñ‹ Ð½ÐµÐ¾ÑÑƒÑ‰ÐµÑÑ‚Ð²Ð¸Ð¼.
        # (ÐŸÑ€Ð¸Ð²ÑÐ·ÐºÐ° Ð·Ð°ÐºÐ°Ð·Ð° Ðº Ñ‚Ð¾Ñ‡ÐºÐµ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ ÑƒÐ¶Ðµ Ð² ÐºÐ¾Ð»Ð±ÑÐºÐµ ÑÑ‚Ð¾Ð¸Ð¼Ð¾ÑÑ‚Ð¸: Ñ‡ÑƒÐ¶Ð¾Ð¹ Ð·Ð°ÐºÐ°Ð·
        # ÑÑ‚Ð¾Ð¸Ñ‚ Ð¼Ð¸Ð»Ð»Ð¸Ð°Ñ€Ð´ Ð¸ Ð½Ð¸ÐºÐ¾Ð³Ð´Ð° Ð½Ðµ Ð¿Ð¾Ð¿Ð°Ð´Ñ‘Ñ‚ Ðº ÐºÑƒÑ€ÑŒÐµÑ€Ñƒ Ð´Ñ€ÑƒÐ³Ð¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐ¸.)
        for ln in range(len(sub)):
            if sub[ln] >= K:
                routing.AddDisjunction([manager.NodeToIndex(ln)], 1_000_000)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
        params.time_limit.FromSeconds(4 if round_no == 0 else 2)
        solution = routing.SolveWithParameters(params)
        if solution is None:
            log.warning("solve round %d: no solution (status=%s, veh=%d, nodes=%d)",
                        round_no, routing.status(), n_veh, len(sub))
            if round_no == 0:
                raise RuntimeError("OR-Tools Ð½Ðµ Ð½Ð°ÑˆÑ‘Ð» Ñ€ÐµÑˆÐµÐ½Ð¸Ðµ, Ð¿Ð¾Ð¿Ñ€Ð¾Ð±ÑƒÐ¹Ñ‚Ðµ ÐµÑ‰Ñ‘ Ñ€Ð°Ð·")
            break
        round_stops = set()
        for v in range(n_veh):
            idx, stops = routing.Start(v), []
            while not routing.IsEnd(idx):
                p = manager.IndexToNode(idx)
                if sub[p] >= K:  # Ð¿Ñ€Ð¾Ð¿ÑƒÑÐºÐ°ÐµÐ¼ ÑÐ²Ð¾ÑŽ Ñ‚Ð¾Ñ‡ÐºÑƒ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ (ÑÑ‚Ð°Ñ€Ñ‚)
                    stops.append(sub[p])
                idx = solution.Value(routing.NextVar(idx))
            if not stops:
                continue
            c = round_couriers[v]
            trips_by_cid.setdefault(c["id"], []).append(
                {"courier": c, "stops": stops, "delay": avail[c["id"]]})
            avail[c["id"]] = solution.Value(time_dim.CumulVar(routing.End(v))) + reload_min
            round_stops.update(stops)
        if not round_stops:
            break
        remaining = [g for g in remaining if g not in round_stops]

    routes = []
    for c in couriers:
        trips_raw = trips_by_cid.get(c["id"])
        if not trips_raw:
            continue
        h = home_of[c["id"]]
        home_view = {k: homes[h][k] for k in ("id", "name", "address", "lat", "lng")}
        trips, flat = [], []
        for tr in trips_raw:
            etas, total = _eta_pass(tr["stops"], tr["delay"], matrix, settings,
                                    solved_dt, appr_home[h], home=h,
                                    spd_factor=spd_factor.get(c["id"], 1.0))
            stops = []
            for g, eta in zip(tr["stops"], etas):
                o = orders[g - K]
                late = max(0, eta - deadline_rel[g]) if deadline_rel[g] is not None else 0
                stops.append({
                    "order_id": o["id"], "address": o["address"],
                    "prio": eff_prio[g], "auto": auto_flag[g],
                    "deadline": o.get("deadline") or "", "late_min": late,
                    "lat": o["lat"], "lng": o["lng"], "eta_min": eta,
                    "eta_clock": (solved_dt + timedelta(minutes=eta)).strftime("%H:%M")})
            dist_m = None
            if distances:
                dist_m = 0
                seq = [h] + tr["stops"] + [h]
                for a, b in zip(seq, seq[1:]):
                    dist_m += distances[a][b] or 0
            trips.append({
                "stops": stops, "total_min": total,
                "start_delay_min": tr["delay"],
                "start_clock": (solved_dt + timedelta(minutes=tr["delay"])).strftime("%H:%M"),
                "end_clock": (solved_dt + timedelta(minutes=total)).strftime("%H:%M"),
                "distance_km": round(dist_m / 1000.0, 1) if dist_m is not None else None})
            flat.extend(stops)
        routes.append({
            "courier_id": c["id"], "courier_name": c["name"], "status": c["status"],
            "color": c.get("color") or PALETTE[len(routes) % len(PALETTE)],
            "count": len(flat), "trips": trips, "stops": flat,
            "total_min": max(t["total_min"] for t in trips),
            "start_delay_min": trips[0]["start_delay_min"],
            "distance_km": (round(sum(t["distance_km"] for t in trips), 1)
                            if all(t["distance_km"] is not None for t in trips) else None),
            "tg_chat_id": c.get("tg_chat_id") or "",
            "home_point": home_view,
            "speed_kmh": round(speeds[c["id"]][0], 1),
            "speed_src": speeds[c["id"]][1]})

    # Ð¡Ð½Ð°Ñ‡Ð°Ð»Ð° Â«Ð¾Ñ‚Ð´Ð°Ñ‚ÑŒ ÑÐµÐ¹Ñ‡Ð°ÑÂ» (Ð½Ð° Ð±Ð°Ð·Ðµ), Ð¿Ð¾Ñ‚Ð¾Ð¼ Â«ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ð¼Â»
    routes.sort(key=lambda r: 0 if r["status"] == "base" else 1)
    all_etas = [s["eta_min"] for r in routes for s in r["stops"]]
    plan = {
        "solved_at": solved_dt.isoformat(timespec="seconds"),
        "routes": routes,
        "routing": "roads" if by_roads else "straight",
        "provider": provider,
        "last_delivery_min": max(all_etas, default=0),
        "last_delivery_clock": None,
        "avg_delivery_min": round(sum(all_etas) / len(all_etas)) if all_etas else 0,
        "unassigned": len(remaining),
    }
    if all_etas:
        plan["last_delivery_clock"] = (solved_dt + timedelta(
            minutes=plan["last_delivery_min"])).strftime("%H:%M")
    STATE["plans"][point_id] = plan
    if with_geometry:
        _attach_geometry(plan)
    return plan


def _attach_geometry(plan):
    """Ð“ÐµÐ¾Ð¼ÐµÑ‚Ñ€Ð¸Ñ Ð¼Ð°Ñ€ÑˆÑ€ÑƒÑ‚Ð¾Ð² (Ð´Ð»Ñ Ð»Ð¸Ð½Ð¸Ð¹ Ð½Ð° ÐºÐ°Ñ€Ñ‚Ðµ), Ð¿Ð¾ Ñ‚Ð¾Ñ‡ÐºÐµ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ ÐºÑƒÑ€ÑŒÐµÑ€Ð°."""
    for r in plan["routes"]:
        home = r.get("home_point") or STATE["depot"]
        for t in r.get("trips", []):
            seq = [home] + [{"lat": s["lat"], "lng": s["lng"]}
                            for s in t["stops"]] + [home]
            t["geometry"] = routing_geometry(seq) if plan["routing"] == "roads" else None


# ---------- Ð°ÑƒÑ‚ÐµÐ½Ñ‚Ð¸Ñ„Ð¸ÐºÐ°Ñ†Ð¸Ñ Ð¿Ð¾ email (Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»Ð¸ Ð² Ð‘Ð”) ----------

def _hash_pwd(pwd, salt=None):
    salt = salt or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt, _PBKDF_ROUNDS)
    return f"{salt.hex()}${h.hex()}"


def _verify_pwd(pwd, stored):
    try:
        salt_hex, h_hex = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"),
                                   bytes.fromhex(salt_hex), _PBKDF_ROUNDS)
        return hmac.compare_digest(calc.hex(), h_hex)
    except (ValueError, AttributeError):
        return False


def _check_user_email(email):
    """ÐÐ¾Ñ€Ð¼Ð°Ð»Ð¸Ð·ÑƒÐµÑ‚ Ð¸ Ð²Ð°Ð»Ð¸Ð´Ð¸Ñ€ÑƒÐµÑ‚ email; Ð±Ñ€Ð¾ÑÐ°ÐµÑ‚ ValueError Ñ Ñ‚ÐµÐºÑÑ‚Ð¾Ð¼ Ð´Ð»Ñ 400."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]{1,64}@[^@\s]{1,190}$", email):
        raise ValueError("ÐÐµÐºÐ¾Ñ€Ñ€ÐµÐºÑ‚Ð½Ñ‹Ð¹ email")
    return email


def _check_user_contact(name, phone):
    """ÐÐ¾Ñ€Ð¼Ð°Ð»Ð¸Ð·ÑƒÐµÑ‚ Ð¸ Ð²Ð°Ð»Ð¸Ð´Ð¸Ñ€ÑƒÐµÑ‚ Ð¸Ð¼Ñ/Ñ‚ÐµÐ»ÐµÑ„Ð¾Ð½ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð°; Ð±Ñ€Ð¾ÑÐ°ÐµÑ‚ ValueError."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if len(name) < 2:
        raise ValueError("Ð£ÐºÐ°Ð¶Ð¸Ñ‚Ðµ Ð¸Ð¼Ñ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð° (Ð¼Ð¸Ð½Ð¸Ð¼ÑƒÐ¼ 2 ÑÐ¸Ð¼Ð²Ð¾Ð»Ð°)")
    if not re.match(r"^\+?[\d\s()-]{7,20}$", phone):
        raise ValueError("Ð£ÐºÐ°Ð¶Ð¸Ñ‚Ðµ Ñ‚ÐµÐ»ÐµÑ„Ð¾Ð½ Ð´Ð»Ñ ÑÐ²ÑÐ·Ð¸ (Ð½Ð°Ð¿Ñ€Ð¸Ð¼ÐµÑ€, +375291234567)")
    return name, phone


def _create_user(email, password, is_admin=0, name="", phone=""):
    email = _check_user_email(email)
    if len(password or "") < 4:
        raise ValueError("ÐŸÐ°Ñ€Ð¾Ð»ÑŒ: Ð¼Ð¸Ð½Ð¸Ð¼ÑƒÐ¼ 4 ÑÐ¸Ð¼Ð²Ð¾Ð»Ð¾Ð²")
    name, phone = _check_user_contact(name, phone)
    uid = uuid.uuid4().hex[:8]
    with _db_lock, _db() as c:
        try:
            c.execute("INSERT INTO users(id, email, pwd_hash, is_admin, created_at, name, phone) "
                      "VALUES(?, ?, ?, ?, ?, ?, ?)",
                      (uid, email, _hash_pwd(password), int(bool(is_admin)),
                       _now().isoformat(timespec="seconds"), name, phone))
        except sqlite3.IntegrityError:
            raise ValueError(f"ÐŸÐ¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒ {email} ÑƒÐ¶Ðµ ÑÑƒÑ‰ÐµÑÑ‚Ð²ÑƒÐµÑ‚") from None
    return uid


def ensure_default_admin():
    """ÐŸÐµÑ€Ð²Ñ‹Ð¹ Ð·Ð°Ð¿ÑƒÑÐº: ÑÐ¾Ð·Ð´Ð°Ñ‘Ð¼ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑ‚Ñ€Ð°Ñ‚Ð¾Ñ€Ð° Ð¸Ð· config.ini (Ð¿Ð¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ admin@local/admin)."""
    with _db_lock, _db() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if not n:
        _create_user(CFG["admin_email"], CFG["admin_password"], is_admin=1,
                     name="ÐÐ´Ð¼Ð¸Ð½Ð¸ÑÑ‚Ñ€Ð°Ñ‚Ð¾Ñ€", phone="+375000000000")
        log.info("created default admin %s â€” ÑÐ¼ÐµÐ½Ð¸Ñ‚Ðµ Ð¿Ð°Ñ€Ð¾Ð»ÑŒ Ð¿Ð¾ÑÐ»Ðµ Ð²Ñ…Ð¾Ð´Ð°", CFG["admin_email"])


def _me():
    uid = session.get("uid")
    if not uid:
        return None
    with _db_lock, _db() as c:
        r = c.execute("SELECT id, email, is_admin, name, phone FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(r) if r else None


def _admin_users():
    with _db_lock, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, email, is_admin, created_at, name, phone FROM users ORDER BY created_at")]


_LOGIN_FAILS = {}  # ip -> [Ñ‡Ð¸ÑÐ»Ð¾ Ð¾ÑˆÐ¸Ð±Ð¾Ðº, Ð·Ð°Ð»Ð¾Ñ‡ÐµÐ½Ð¾_Ð´Ð¾_epoch]
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SEC = 60

# ---------- ÐºÑ‚Ð¾ Ð¸Ð· Ð°Ð´Ð¼Ð¸Ð½Ð¾Ð² Ð¾Ð½Ð»Ð°Ð¹Ð½ Ð¸ Ð½Ð° ÐºÐ°ÐºÐ¾Ð¹ Ñ‚Ð¾Ñ‡ÐºÐµ Ñ€Ð°Ð±Ð¾Ñ‚Ð°ÐµÑ‚ ----------
# sid (Ð¸Ð· cookie-ÑÐµÑÑÐ¸Ð¸) -> {"uid", "email", "point_id", "last"}.
# Â«ÐžÐ½Ð»Ð°Ð¹Ð½Â» = Ð±Ñ‹Ð» Ð»ÑŽÐ±Ð¾Ð¹ Ð·Ð°Ð¿Ñ€Ð¾Ñ Ð·Ð° ONLINE_WINDOW ÑÐµÐºÑƒÐ½Ð´ (long-poll /api/rev
# Ð²Ð¸ÑÐ¸Ñ‚ Ð´Ð¾ 25 Ñ, Ð¿Ð¾ÑÑ‚Ð¾Ð¼Ñƒ Ð¾ÐºÐ½Ð¾ Ñ Ð·Ð°Ð¿Ð°ÑÐ¾Ð¼).
ONLINE: dict = {}
_ONLINE_LOCK = threading.Lock()
ONLINE_WINDOW = 90


def _my_point():
    """Ð Ð°Ð±Ð¾Ñ‡Ð°Ñ Ñ‚Ð¾Ñ‡ÐºÐ° Ñ‚ÐµÐºÑƒÑ‰ÐµÐ¹ ÑÐµÑÑÐ¸Ð¸ (ÑÐµÐ»ÐµÐºÑ‚Ð¾Ñ€ Â«ÐœÐµÑÑ‚Ð¾ Ñ€Ð°Ð±Ð¾Ñ‚Ñ‹Â» Ð² ÑˆÐ°Ð¿ÐºÐµ).

    ÐÐ²Ñ‚Ð¾Ñ€Ð¸Ñ‚ÐµÑ‚Ð½Ð¾Ðµ Ð·Ð½Ð°Ñ‡ÐµÐ½Ð¸Ðµ â€” session["point"]: Ð¿ÐµÑ€ÐµÐ¶Ð¸Ð²Ð°ÐµÑ‚ Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ð¹,
    ONLINE-Ð·Ð°Ð¿Ð¸ÑÑŒ Ð´ÐµÑ€Ð¶Ð¸Ñ‚ Ð»Ð¸ÑˆÑŒ Ð¶Ð¸Ð²Ð¾Ðµ Ð·ÐµÑ€ÐºÐ°Ð»Ð¾ Ð´Ð»Ñ ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸ÐºÐ¾Ð² Â«Ð¾Ð½Ð»Ð°Ð¹Ð½ Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸Â».
    """
    pt = session.get("point") or ""
    if pt and any(p["id"] == pt for p in STATE.get("points") or []):
        sid = session.get("sid")
        if sid:
            with _ONLINE_LOCK:
                rec = ONLINE.get(sid)
                if rec is not None and rec.get("point_id") != pt:
                    rec["point_id"] = pt  # Ð·ÐµÑ€ÐºÐ°Ð»Ð¾ Ð´Ð¾Ð³Ð¾Ð½ÑÐµÑ‚ ÑÐµÑÑÐ¸ÑŽ
        return pt
    return (STATE.get("points") or [{}])[0].get("id") or ""


def _plan_for(pid):
    """ÐŸÐ»Ð°Ð½ Ð´ÐµÐ¿Ð¾ Ð¿Ð¾ id Ñ‚Ð¾Ñ‡ÐºÐ¸."""
    return STATE["plans"].get(pid)


def _courier_plan(c):
    """ÐŸÐ»Ð°Ð½ Ð´ÐµÐ¿Ð¾, Ðº ÐºÐ¾Ñ‚Ð¾Ñ€Ð¾Ð¼Ñƒ Ð¿Ñ€Ð¸Ð¿Ð¸ÑÐ°Ð½ ÐºÑƒÑ€ÑŒÐµÑ€."""
    hp = _home_point(c)
    return STATE["plans"].get(hp["id"]) if hp else None


def _touch_online(pt=None):
    """ÐžÑ‚Ð¼ÐµÑ‚Ð¸Ñ‚ÑŒ Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚ÑŒ Ñ‚ÐµÐºÑƒÑ‰ÐµÐ¹ ÑÐµÑÑÐ¸Ð¸; pt Ð·Ð°Ð´Ð°Ñ‘Ñ‚ ÐµÑ‘ Ñ€Ð°Ð±Ð¾Ñ‡ÑƒÑŽ Ñ‚Ð¾Ñ‡ÐºÑƒ.

    Ð•ÑÐ»Ð¸ Ð·Ð°Ð¿Ð¸ÑÑŒ ÑÑ‚Ñ‘Ñ€Ð»Ð°ÑÑŒ (Ð´Ð¾Ð»Ð³Ð¾ Ð²Ð¸ÑÐµÐ²ÑˆÐ¸Ð¹ long-poll / Ñ„Ð¾Ð½Ð¾Ð²Ð°Ñ Ð²ÐºÐ»Ð°Ð´ÐºÐ°),
    Ð²Ð¾ÑÑÑ‚Ð°Ð½Ð°Ð²Ð»Ð¸Ð²Ð°ÐµÐ¼ ÐµÑ‘ Ð¸Ð· ÑÐµÑÑÐ¸Ð¸ â€” Ð¸Ð½Ð°Ñ‡Ðµ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€ Â«Ð¿Ñ€Ð¾Ð¿Ð°Ð´Ð°Ð»Â» Ð¸Ð· Ð¾Ð½Ð»Ð°Ð¹Ð½-Ð¿Ñ€Ð¾Ð±Ð¾Ðº.
    """
    sid = session.get("sid")
    if not sid:
        return
    rec = ONLINE.get(sid)
    if rec is None:
        uid = session.get("uid")
        if not uid:
            return
        with _db_lock, _db() as c:  # Ñ‡Ñ‚ÐµÐ½Ð¸Ðµ â€” Ð²Ð½Ðµ ONLINE-Ð±Ð»Ð¾ÐºÐ¸Ñ€Ð¾Ð²ÐºÐ¸
            r = c.execute("SELECT email FROM users WHERE id = ?", (uid,)).fetchone()
        if not r:
            return
        with _ONLINE_LOCK:
            ONLINE[sid] = {"uid": uid, "email": r["email"],
                 "point_id": pt or session.get("point")
                 or (STATE.get("points") or [{}])[0].get("id") or "",
                 "last": time.time()}
        return
    rec["last"] = time.time()
    if pt is not None:
        rec["point_id"] = pt


def _drop_online():
    sid = session.get("sid")
    if sid:
        with _ONLINE_LOCK:
            ONLINE.pop(sid, None)




# --- Telegram: Ð±Ð¾Ñ‚ Ð¿Ñ€Ð¸Ð½Ð¸Ð¼Ð°ÐµÑ‚ Ð³ÐµÐ¾Ð»Ð¾ÐºÐ°Ñ†Ð¸Ð¸ ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð² ---------------------------------
TG_POS_TTL = 30 * 60  # Ð»Ð¾ÐºÐ°Ñ†Ð¸Ñ ÑÑ‚Ð°Ñ€ÑˆÐµ 30 Ð¼Ð¸Ð½ÑƒÑ‚ ÑÑ‡Ð¸Ñ‚Ð°ÐµÑ‚ÑÑ ÑƒÑÑ‚Ð°Ñ€ÐµÐ²ÑˆÐµÐ¹


def _tg_api(method):
    return f"https://api.telegram.org/bot{CFG['tg_bot_token']}/{method}"


# Ð¢ÐµÑÑ‚-Ñ€ÐµÐ¶Ð¸Ð¼: Ð²ÑÐµ Ð´Ð¸Ð°Ð»Ð¾Ð³Ð¸ Ð±Ð¾Ñ‚Ð° (Ð³ÐµÐ¾-Ð·Ð°Ð¿Ñ€Ð¾ÑÑ‹, Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½?Â», Ð¿Ñ€Ð¸Ð²ÑÐ·ÐºÐ¸) ÑƒÑ…Ð¾Ð´ÑÑ‚
# Ð¾Ð´Ð½Ð¾Ð¼Ñƒ Ð¶Ð¸Ð²Ð¾Ð¼Ñƒ Ñ‡ÐµÐ»Ð¾Ð²ÐµÐºÑƒ Ð²Ð¼ÐµÑÑ‚Ð¾ Ñ€ÐµÐ°Ð»ÑŒÐ½Ñ‹Ñ… ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð². Ð“ÐµÐ¾-ÐºÐ¾Ð½Ð²ÐµÐ¹ÐµÑ€ Ð¿Ñ€Ð¸ ÑÑ‚Ð¾Ð¼
# Ð¾ÑÑ‚Ð°Ñ‘Ñ‚ÑÑ Ñ‡ÐµÑÑ‚Ð½Ñ‹Ð¼: ÐºÐ°Ð¶Ð´Ñ‹Ð¹ Ð±Ð¾Ñ‚-ÐºÑƒÑ€ÑŒÐµÑ€ Ð¿Ñ€Ð¸Ð²ÑÐ·Ð°Ð½ Ðº ÑÐ²Ð¾ÐµÐ¼Ñƒ ÑÐ¸Ð½Ñ‚ÐµÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾Ð¼Ñƒ chat_id,
# Ñ€ÐµÐ´Ð¸Ñ€ÐµÐºÑ‚ Ð¿Ñ€Ð¾Ð¸ÑÑ…Ð¾Ð´Ð¸Ñ‚ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð² Ð¼Ð¾Ð¼ÐµÐ½Ñ‚ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²ÐºÐ¸ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ð¹.
TG_TEST_REDIRECT = os.environ.get("TG_TEST_REDIRECT", "").strip()


def _tg_out_chat(chat_id):
    """(Ð°Ð´Ñ€ÐµÑÐ°Ñ‚, Ð¿Ñ€ÐµÑ„Ð¸ÐºÑ) Ð´Ð»Ñ Ð¸ÑÑ…Ð¾Ð´ÑÑ‰ÐµÐ³Ð¾ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ: Ð² Ñ‚ÐµÑÑ‚-Ñ€ÐµÐ¶Ð¸Ð¼Ðµ Ð²ÑÑ‘ Ð¾Ð´Ð½Ð¾Ð¼Ñƒ
    Ñ‡ÐµÐ»Ð¾Ð²ÐµÐºÑƒ, Ñ Ð¿Ð¾Ð¼ÐµÑ‚ÐºÐ¾Ð¹, Ð¾Ñ‚ ÐºÐ°ÐºÐ¾Ð³Ð¾ ÐºÑƒÑ€ÑŒÐµÑ€Ð° ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ."""
    cid = str(chat_id)
    if TG_TEST_REDIRECT and cid != TG_TEST_REDIRECT:
        c = next((x for x in STATE["couriers"]
                  if str(x.get("tg_chat_id") or "") == cid), None)
        pref = f"[{_esc(c['name'])}] " if c else "[Ñ‚ÐµÑÑ‚] "
        return TG_TEST_REDIRECT, pref
    return cid, ""
    return f"https://api.telegram.org/bot{CFG['tg_bot_token']}/{method}"


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plural(n, forms):
    """Ð ÑƒÑÑÐºÐ¾Ðµ ÑÐºÐ»Ð¾Ð½ÐµÐ½Ð¸Ðµ: _plural(3, ("Ð·Ð°ÐºÐ°Ð·", "Ð·Ð°ÐºÐ°Ð·Ð°", "Ð·Ð°ÐºÐ°Ð·Ð¾Ð²")) -> "Ð·Ð°ÐºÐ°Ð·Ð°"."""
    n = abs(n)
    if n % 100 in (11, 12, 13, 14):
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if n % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]


def _tg_send(chat_id, text):
    """Ð˜ÑÑ…Ð¾Ð´ÑÑ‰ÐµÐµ ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ ÐºÑƒÑ€ÑŒÐµÑ€Ñƒ (Ð¾ÑˆÐ¸Ð±ÐºÐ¸ Ð½Ðµ ÐºÑ€Ð¸Ñ‚Ð¸Ñ‡Ð½Ñ‹ â€” Ð¼Ð¾Ð»Ñ‡Ð° Ð² Ð»Ð¾Ð³)."""
    chat_id, pref = _tg_out_chat(chat_id)
    try:
        requests.post(_tg_api("sendMessage"),
                      json={"chat_id": chat_id, "text": pref + text, "parse_mode": "HTML"}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg sendMessage: %s", e)


def _tg_send_kb(chat_id, text, buttons):
    """Ð¡Ð¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ðµ Ñ Ð¸Ð½Ð»Ð°Ð¹Ð½-ÐºÐ½Ð¾Ð¿ÐºÐ°Ð¼Ð¸. buttons = [[{text, callback_data}, ...], ...].
    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ message_id Ð¸Ð»Ð¸ None (Ð½Ðµ Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð¸Ð»Ð¾ÑÑŒ)."""
    chat_id, pref = _tg_out_chat(chat_id)
    try:
        r = requests.post(_tg_api("sendMessage"),
                          json={"chat_id": chat_id, "text": pref + text, "parse_mode": "HTML",
                                "reply_markup": {"inline_keyboard": buttons}}, timeout=5)
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        log.warning("tg sendMessage kb: %s", data.get("description"))
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("tg sendMessage kb: %s", e)
    return None


def _tg_edit_msg(chat_id, message_id, text, buttons=None):
    """ÐŸÑ€Ð°Ð²ÐºÐ° ÑÐ¾Ð¾Ð±Ñ‰ÐµÐ½Ð¸Ñ Ð±Ð¾Ñ‚Ð° (ÑÐ¼ÐµÐ½Ð° Ñ‚ÐµÐºÑÑ‚Ð°/ÐºÐ½Ð¾Ð¿Ð¾Ðº). ÐžÑˆÐ¸Ð±ÐºÐ¸ Ð¼Ð¾Ð»Ñ‡Ð° Ð² Ð»Ð¾Ð³."""
    chat_id, pref = _tg_out_chat(chat_id)
    payload = {"chat_id": chat_id, "message_id": message_id,
               "text": pref + text, "parse_mode": "HTML"}
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(_tg_api("editMessageText"), json=payload, timeout=5)
        data = r.json()
        if not data.get("ok") and data.get("description") != "message is not modified":
            log.warning("tg editMessageText: %s", data.get("description"))
    except (requests.RequestException, ValueError) as e:
        log.warning("tg editMessageText: %s", e)


def _tg_answer_cb(callback_id, text=""):
    """ÐžÑ‚Ð²ÐµÑ‚ Ð½Ð° Ð½Ð°Ð¶Ð°Ñ‚Ð¸Ðµ ÐºÐ½Ð¾Ð¿ÐºÐ¸ (Ð·Ð°ÐºÑ€Ñ‹Ð²Ð°ÐµÑ‚ Â«Ñ‡Ð°ÑÐ¸ÐºÐ¸Â» Ñƒ ÐºÑƒÑ€ÑŒÐµÑ€Ð°)."""
    try:
        requests.post(_tg_api("answerCallbackQuery"),
                      json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg answerCallbackQuery: %s", e)


_BOT_ASK_AFTER_S = 30   # ÑÑ‚Ð¾Ð»ÑŒÐºÐ¾ ÑÐµÐºÑƒÐ½Ð´ ÐºÑƒÑ€ÑŒÐµÑ€ ÑÑ‚Ð¾Ð¸Ñ‚ Ñƒ Ð°Ð´Ñ€ÐµÑÐ°, Ð¿Ñ€ÐµÐ¶Ð´Ðµ Ñ‡ÐµÐ¼ Ð±Ð¾Ñ‚ ÑÐ¿Ñ€Ð¾ÑÐ¸Ñ‚


TG_GEO_FRESH = 600      # Ð³ÐµÐ¾ ÑÐ²ÐµÐ¶Ð°Ñ Ð´Ð»Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð¾Ð² <= 10 Ð¼Ð¸Ð½
TG_GEO_AT_PLACE = 0.15  # Ð±Ð»Ð¸Ð¶Ðµ 150 Ð¼ = Â«Ð½Ð° Ð¼ÐµÑÑ‚ÐµÂ» (Ð´ÐµÐ¿Ð¾/Ð·Ð°ÐºÐ°Ð·)
_LOAD_DWELL_S = 120     # ÑÑ‚Ð¾Ð»ÑŒÐºÐ¾ Ð½ÑƒÐ¶Ð½Ð¾ Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ñ Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸, Ñ‡Ñ‚Ð¾Ð±Ñ‹ ÑÑ‡Ð¸Ñ‚Ð°Ñ‚ÑŒ Ð²Ñ‹Ð´Ð°Ñ‡Ñƒ ÑÐ¾ÑÑ‚Ð¾ÑÐ²ÑˆÐµÐ¹ÑÑ


def _courier_out_orders(c):
    """Ð—Ð°ÐºÐ°Ð·Ñ‹ Â«Ñƒ ÐºÑƒÑ€ÑŒÐµÑ€Ð°Â»: Ð²Ñ‹Ð´Ð°Ð½Ñ‹ Ð¸ ÐµÑ‰Ñ‘ Ð½Ðµ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ñ‹ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð¾Ð¼."""
    return [o for o in STATE["orders"]
            if (o.get("status") or "ready") == "out"
            and (o.get("assigned") or "") == c.get("id")]


def _courier_has_out(c):
    return bool(_courier_out_orders(c))


def _load_track(c, pos, now=None):
    """Ð¢Ñ€ÐµÐºÐµÑ€ Â«Ð·Ð°ÐºÐ°Ð·Ñ‹ Ð¾Ñ‚Ð´Ð°Ð»Ð¸Â»: ÐºÑƒÑ€ÑŒÐµÑ€ Ð¾Ð±ÑÐ·Ð°Ð½ Ñ€ÐµÐ°Ð»ÑŒÐ½Ð¾ Ð¿Ð¾ÑÑ‚Ð¾ÑÑ‚ÑŒ Ñƒ ÑÐ²Ð¾ÐµÐ¹ Ñ‚Ð¾Ñ‡ÐºÐ¸.

    Ð›Ð¾Ð¶Ð½Ñ‹Ðµ ÑÑ€Ð°Ð±Ð°Ñ‚Ñ‹Ð²Ð°Ð½Ð¸Ñ Ð¾Ñ‚ÑÐµÐºÐ°ÑŽÑ‚ÑÑ Ñ‚Ñ€ÐµÐ¼Ñ ÑÐ¿Ð¾ÑÐ¾Ð±Ð°Ð¼Ð¸: Ð¿Ð¾Ð·Ð¸Ñ†Ð¸Ñ ÑÐ³Ð»Ð°Ð¶ÐµÐ½Ð° Ð¼ÐµÐ´Ð¸Ð°Ð½Ð¾Ð¹
    (GPS-Ð¿Ñ€Ñ‹Ð¶Ð¾Ðº Ð½Ðµ Ð´Ð¾ÐµÐ·Ð¶Ð°ÐµÑ‚ Ð´Ð¾ Ñ‚Ð¾Ñ‡ÐºÐ¸), Ð½ÑƒÐ¶ÐµÐ½ Ð½ÐµÐ¿Ñ€ÐµÑ€Ñ‹Ð²Ð½Ñ‹Ð¹ Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ð¹ _LOAD_DWELL_S
    (Ð·Ð°ÐµÐ·Ð´ Ð¼Ð¸Ð¼Ð¾ Ð½Ðµ ÑÑ‡Ð¸Ñ‚Ð°ÐµÑ‚ÑÑ), Ð¸ Ñƒ ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð´Ð¾Ð»Ð¶Ð½Ñ‹ Ð±Ñ‹Ñ‚ÑŒ Ð²Ñ‹Ð´Ð°Ð½Ð½Ñ‹Ðµ Ð·Ð°ÐºÐ°Ð·Ñ‹.
    """
    chat = c.get("tg_chat_id") or ""
    home = _home_point(c)
    if not chat or not home:
        return
    if not _courier_has_out(c):
        STATE["tg_load"].pop(chat, None)  # Ð¿Ð°Ñ€Ñ‚Ð¸Ñ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð° â€” Ð³Ð¾Ñ‚Ð¾Ð²Ð¸Ð¼ÑÑ Ðº ÑÐ»ÐµÐ´ÑƒÑŽÑ‰ÐµÐ¹
        return
    now = now or time.time()
    rec = STATE["tg_load"].setdefault(chat, {"since": None, "loaded_at": None})
    if rec["loaded_at"]:
        return
    if haversine_km(pos, home) <= TG_GEO_AT_PLACE:
        rec["since"] = rec["since"] or now
        if now - rec["since"] >= _LOAD_DWELL_S:
            rec["loaded_at"] = now
            log.info("load tracked: %s Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ð» Ð·Ð°ÐºÐ°Ð·Ñ‹ Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸ Â«%sÂ»", c.get("name"), home.get("name"))
            _bump()
    else:
        rec["since"] = None  # Ð¾Ñ‚Ð¾ÑˆÑ‘Ð», Ð½Ðµ Ð´Ð¾Ð¶Ð´Ð°Ð²ÑˆÐ¸ÑÑŒ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ â€” Ð¾Ñ‚ÑÑ‡Ñ‘Ñ‚ Ð·Ð°Ð½Ð¾Ð²Ð¾


_DELIVER_DWELL_S = 90  # ÑÐºÐ¾Ð»ÑŒÐºÐ¾ ÑÑ‚Ð¾ÑÑ‚ÑŒ Ñƒ Ð°Ð´Ñ€ÐµÑÐ°, Ñ‡Ñ‚Ð¾Ð±Ñ‹ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚ ÑÑ‡Ñ‘Ð» Ð·Ð°ÐºÐ°Ð· Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½Ð½Ñ‹Ð¼


def _deliver_track(c, pos, now=None):
    """Ð’Ñ‹Ð²Ð¾Ð´ Â«ÐºÑƒÑ€ÑŒÐµÑ€ Ð¾Ñ‚Ð²Ñ‘Ð· Ð·Ð°ÐºÐ°Ð·Â» ÐŸÐž Ð“Ð•Ðž â€” Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð´Ð»Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð° Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚Ð°.

    Ð—Ð°ÐºÐ°Ð· ÑÑ‡Ð¸Ñ‚Ð°ÐµÑ‚ÑÑ Ñ€Ð°Ð·Ð²ÐµÐ·Ñ‘Ð½Ð½Ñ‹Ð¼ Ð² Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ðµ, ÐºÐ¾Ð³Ð´Ð° ÐºÑƒÑ€ÑŒÐµÑ€ Ð½ÐµÐ¿Ñ€ÐµÑ€Ñ‹Ð²Ð½Ð¾ Ð¿Ñ€Ð¾ÑÑ‚Ð¾ÑÐ»
    _DELIVER_DWELL_S Ð² Ñ€Ð°Ð´Ð¸ÑƒÑÐµ TG_GEO_AT_PLACE Ð¾Ñ‚ Ð°Ð´Ñ€ÐµÑÐ°. Ð¡Ñ‚Ð°Ñ‚ÑƒÑ Ð·Ð°ÐºÐ°Ð·Ð° Ð¿Ñ€Ð¸
    ÑÑ‚Ð¾Ð¼ ÐÐ• Ð¼ÐµÐ½ÑÐµÑ‚ÑÑ â€” ÐµÐ³Ð¾ Ð¿Ð¾-Ð¿Ñ€ÐµÐ¶Ð½ÐµÐ¼Ñƒ Ð·Ð°ÐºÑ€Ñ‹Ð²Ð°ÐµÑ‚ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€ Ð²Ñ€ÑƒÑ‡Ð½ÑƒÑŽ.
    Ð—Ð°ÐµÐ·Ð´ Ð¼Ð¸Ð¼Ð¾ Ð±ÐµÐ· Ð¾ÑÑ‚Ð°Ð½Ð¾Ð²ÐºÐ¸ Ð½Ðµ ÑÑ‡Ð¸Ñ‚Ð°ÐµÑ‚ÑÑ (ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸Ðº Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ñ ÑÐ±Ñ€Ð°ÑÑ‹Ð²Ð°ÐµÑ‚ÑÑ).
    """
    chat = c.get("tg_chat_id") or ""
    if not chat:
        return
    out_orders = _courier_out_orders(c)
    st = STATE["tg_deliv"].setdefault(chat, {})
    alive = {o["id"] for o in out_orders}
    for k in list(st):  # Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ñ‹Ðµ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð¾Ð¼ Ð·Ð°Ð¿Ð¸ÑÐ¸ Ñ‡Ð¸ÑÑ‚Ð¸Ð¼
        if k not in alive:
            st.pop(k, None)
            STATE["tg_ask"].get(chat, {}).pop(k, None)
    if not out_orders:
        STATE["tg_deliv"].pop(chat, None)
        return
    now = now or time.time()
    for o in out_orders:
        if o.get("lat") is None or o.get("lng") is None:
            continue  # Ð±ÐµÐ· ÐºÐ¾Ð¾Ñ€Ð´Ð¸Ð½Ð°Ñ‚ Ð°Ð´Ñ€ÐµÑ Ð½Ðµ ÑÐ²ÐµÑ€Ð¸Ñ‚ÑŒ â€” Ð¿Ð¾Ð»Ð»ÐµÑ€ ÐºÑ€Ð°ÑˆÐ¸Ñ‚ÑŒ Ð½ÐµÐ»ÑŒÐ·Ñ
        rec = st.setdefault(o["id"], {})
        if haversine_km(pos, o) <= TG_GEO_AT_PLACE:
            rec["since"] = rec.get("since") or now
            # Ð±Ð¾Ñ‚ ÑÐ¿Ñ€Ð°ÑˆÐ¸Ð²Ð°ÐµÑ‚ ÐºÑƒÑ€ÑŒÐµÑ€Ð° Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½?Â» â€” Ñ€Ð°Ð· Ð·Ð° Ð·Ð°ÐµÐ·Ð´: ÑÐ½Ð¾Ð²Ð°
            # ÑÐ¿Ñ€Ð¾ÑÐ¸Ñ‚ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð¿Ð¾ÑÐ»Ðµ Ð²Ñ‹ÐµÐ·Ð´Ð° Ð¸Ð· Ñ€Ð°Ð´Ð¸ÑƒÑÐ° Ð¸ Ð½Ð¾Ð²Ð¾Ð³Ð¾ 30-Ñ Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ñ
            if (now - rec["since"] >= _BOT_ASK_AFTER_S and not rec.get("asked")
                    and o["id"] not in STATE["tg_ask"].get(chat, {})):
                rec["asked"] = True
                mid = _tg_send_kb(chat, _bot_ask_text(o), _bot_ask_kb(o["id"]))
                if mid is not None or not CFG["tg_poll"]:
                    STATE["tg_ask"].setdefault(chat, {})[o["id"]] = {
                        "msg": mid or 0, "stage": "ask"}
                    log.info("bot ask: %s Ñƒ Â«%sÂ» â€” ÑÐ¿Ñ€Ð¾ÑÐ¸Ð»Ð¸ Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½?Â»",
                             c.get("name"), o.get("address"))
                    _ev("bot", f"ÑÐ¿Ñ€Ð¾ÑÐ¸Ð» {c.get('name')}: Â«{o.get('address')}Â» â€” Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½?")
            if not rec.get("at") and now - rec["since"] >= _DELIVER_DWELL_S:
                rec["at"] = now
                log.info("deliver tracked: %s Ð±Ñ‹Ð» Ñƒ Ð°Ð´Ñ€ÐµÑÐ° Â«%sÂ» â€” Ð¸Ð· Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð° Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚Ð°",
                         c.get("name"), o.get("address"))
                _ev("sys", f"{c.get('name')} Ð±Ñ‹Ð» Ñƒ Ð°Ð´Ñ€ÐµÑÐ° Â«{o.get('address')}Â»")
                _bump()
        else:
            # Ð²Ñ‹ÐµÑ…Ð°Ð» Ð¸Ð· Ñ€Ð°Ð´Ð¸ÑƒÑÐ° â€” Ð·Ð°ÐµÐ·Ð´ Ð·Ð°ÐºÑ€Ñ‹Ñ‚: ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ð¹ Ð·Ð°ÐµÐ·Ð´ ÑÐ¿Ñ€Ð¾ÑÐ¸Ñ‚ Ð·Ð°Ð½Ð¾Ð²Ð¾
            rec.pop("since", None)
            rec.pop("asked", None)


_AWAY_AUTO_KM = 0.5    # Ð´Ð°Ð»ÑŒÑˆÐµ ÑÑ‚Ð¾Ð³Ð¾ Ð¾Ñ‚ ÑÐ²Ð¾ÐµÐ¹ Ñ‚Ð¾Ñ‡ÐºÐ¸ ÐºÑƒÑ€ÑŒÐµÑ€ Â«ÑƒÐµÑ…Ð°Ð»Â»
_AWAY_DWELL_S = 60     # Ð½ÐµÐ¿Ñ€ÐµÑ€Ñ‹Ð²Ð½Ð¾, ÑÑ‚Ð¾Ð»ÑŒÐºÐ¾ ÑÐµÐºÑƒÐ½Ð´ (Ð³Ð»ÑƒÑˆÐ¸Ñ‚ GPS-Ð¿Ñ€Ñ‹Ð¶Ð¾Ðº Ð¸ Â«Ð¾Ñ‚Ð¾ÑˆÑ‘Ð» Ðº Ð¼Ð°ÑˆÐ¸Ð½ÐµÂ»)
_BACK_DWELL_S = 120    # Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ð¹ Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð¿Ð¾ÑÐ»Ðµ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð¸Ñ Ð²ÑÐµÑ… Ð·Ð°ÐºÐ°Ð·Ð¾Ð² â€” Â«Ð½Ð° Ð±Ð°Ð·ÐµÂ»


def _auto_status_apply(c, new_status):
    """ÐŸÐµÑ€ÐµÐ²Ð¾Ð´ ÑÑ‚Ð°Ñ‚ÑƒÑÐ° ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð¿Ð¾ Ð³ÐµÐ¾: Ð‘Ð”, ÑÐ±Ñ€Ð¾Ñ ÐµÐ³Ð¾ Ð¼Ð°Ñ€ÑˆÑ€ÑƒÑ‚Ð¾Ð², Ð¿Ð¸Ð½Ð¾Ðº Ð¿Ð¾Ð´Ð¿Ð¸ÑÑ‡Ð¸ÐºÐ°Ð¼."""
    was = c.get("status")
    c["status"] = new_status
    _persist_couriers()
    _invalidate_plan(courier_id=c["id"], geo=True)
    if was != new_status:
        _ev("sys", f"{c['name']}: " + ("ÑƒÐµÑ…Ð°Ð» Ð² Ð¿ÑƒÑ‚ÑŒ" if new_status == "away"
                                       else "Ð²ÐµÑ€Ð½ÑƒÐ»ÑÑ Ð½Ð° Ð±Ð°Ð·Ñƒ"))
    else:
        # ÑÑ‚Ð°Ñ‚ÑƒÑ Ð½Ðµ ÑÐ¼ÐµÐ½Ð¸Ð»ÑÑ â€” ÑÑ‚Ð¾ Ð¿Ñ€Ð¾ÑÑ‚Ð¾ Ð³ÐµÐ¾-Ñ‚Ð¸Ðº Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ñ, Ð½Ðµ ÑÐ¾Ð±Ñ‹Ñ‚Ð¸Ðµ
        _bump(geo=True)
        return
    _bump()


def _auto_status_track(c, pos, now=None):
    """ÐÐ²Ñ‚Ð¾-ÑÑ‚Ð°Ñ‚ÑƒÑÑ‹ Ð¿Ð¾ Ð³ÐµÐ¾ (Ð² Ð¾Ð±Ðµ ÑÑ‚Ð¾Ñ€Ð¾Ð½Ñ‹, Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ñ Ð¶Ð¸Ð²Ñ‹Ð¼ Ð³ÐµÐ¾):

    Â«Ð±Ð°Ð·Ð°Â» -> Â«Ð² Ð¿ÑƒÑ‚Ð¸Â»: ÑƒÐµÑ…Ð°Ð» Ð´Ð°Ð»ÑŒÑˆÐµ _AWAY_AUTO_KM Ð¸ Ð´ÐµÑ€Ð¶Ð¸Ñ‚ÑÑ _AWAY_DWELL_S.
    Â«Ð² Ð¿ÑƒÑ‚Ð¸Â» -> Â«Ð±Ð°Ð·Ð°Â»: Ð‘Ð«Ð› Ð² Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐµ (Ð²Ñ‹Ð´Ð°Ð½Ð½Ñ‹Ðµ Ð·Ð°ÐºÐ°Ð·Ñ‹ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ñ‹) Ð¸ Ð¿Ñ€Ð¾ÑÑ‚Ð¾ÑÐ»
    Ñƒ ÑÐ²Ð¾ÐµÐ¹ Ñ‚Ð¾Ñ‡ÐºÐ¸ _BACK_DWELL_S. ÐšÑƒÑ€ÑŒÐµÑ€, ÐºÐ¾Ñ‚Ð¾Ñ€Ñ‹Ð¹ Â«Ð² Ð¿ÑƒÑ‚Ð¸Â» ÑÑ‚Ð¾Ð¸Ñ‚ Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð¸
    Ð¶Ð´Ñ‘Ñ‚ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸, Ð½Ð°Ð·Ð°Ð´ ÐÐ• Ð¿ÐµÑ€ÐµÐ²Ð¾Ð´Ð¸Ñ‚ÑÑ â€” Ð·Ð°ÐºÐ°Ð·Ð¾Ð² Ð½Ðµ Ð±Ñ‹Ð»Ð¾, Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚ Ð·Ð° Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð¾Ð¼.
    Ð—Ð¾Ð½Ð° 150 Ð¼..500 Ð¼ â€” Ð³Ð¸ÑÑ‚ÐµÑ€ÐµÐ·Ð¸Ñ: ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸Ðº Ð½Ðµ Ñ‚Ð¸ÐºÐ°ÐµÑ‚ Ð¸ Ð½Ðµ ÑÐ±Ñ€Ð°ÑÑ‹Ð²Ð°ÐµÑ‚ÑÑ.
    """
    home = _home_point(c)
    chat = c.get("tg_chat_id") or ""
    if not home or not chat:
        return
    status = c.get("status")
    if status not in ("base", "away"):
        STATE["tg_away"].pop(chat, None)
        return
    now = now or time.time()
    rec = STATE["tg_away"].setdefault(chat, {"since": None, "went_out": False})
    d = haversine_km(pos, home)
    out = _courier_out_orders(c)
    if out:
        rec["went_out"] = True
    if status == "base":
        if d > _AWAY_AUTO_KM:
            rec["since"] = rec["since"] or now
            if now - rec["since"] >= _AWAY_DWELL_S:
                rec["since"], rec["went_out"] = None, False
                log.info("auto-away: %s ÑƒÐµÑ…Ð°Ð» Ð¾Ñ‚ Ñ‚Ð¾Ñ‡ÐºÐ¸ Â«%sÂ» (%.0f Ð¼) â€” ÑÑ‚Ð°Ñ‚ÑƒÑ Â«Ð² Ð¿ÑƒÑ‚Ð¸Â»",
                         c.get("name"), home.get("name"), d * 1000)
                _auto_status_apply(c, "away")
        elif d <= TG_GEO_AT_PLACE:
            rec["since"] = None  # Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸ â€” Ð¾Ñ‚ÑÑ‡Ñ‘Ñ‚ Ð·Ð°Ð½Ð¾Ð²Ð¾
            rec["went_out"] = False
        return
    # ÑÑ‚Ð°Ñ‚ÑƒÑ Â«Ð² Ð¿ÑƒÑ‚Ð¸Â»
    if d <= TG_GEO_AT_PLACE:
        if not out and rec["went_out"]:
            rec["since"] = rec["since"] or now
            if now - rec["since"] >= _BACK_DWELL_S:
                STATE["tg_away"].pop(chat, None)
                log.info("auto-return: %s Ð²ÐµÑ€Ð½ÑƒÐ»ÑÑ Ðº Ñ‚Ð¾Ñ‡ÐºÐµ Â«%sÂ» â€” ÑÑ‚Ð°Ñ‚ÑƒÑ Â«Ð½Ð° Ð±Ð°Ð·ÐµÂ»",
                         c.get("name"), home.get("name"))
                _auto_status_apply(c, "base")
        else:
            rec["since"] = None  # Ð¶Ð´Ñ‘Ñ‚ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ Ð¸Ð»Ð¸ ÐµÑ‰Ñ‘ Ñ€Ð°Ð·Ð²Ð¾Ð·Ð¸Ñ‚ â€” Ð½Ðµ Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚
    else:
        rec["since"] = None     # ÑÐ½Ð¾Ð²Ð° ÑƒÐµÑ…Ð°Ð» â€” ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸Ðº Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ñ ÑÐ±Ñ€Ð¾ÑˆÐµÐ½


def _courier_geo(c, depot, now=None):
    """Ð“ÐµÐ¾-Ð´Ð°Ð½Ð½Ñ‹Ðµ ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð´Ð»Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð¾Ð²: ÑÐ³Ð»Ð°Ð¶ÐµÐ½Ð½Ð°Ñ Ð¿Ð¾Ð·Ð¸Ñ†Ð¸Ñ + Ð¾Ñ†ÐµÐ½ÐºÐ° Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚Ð° Ð½Ð° Ð´ÐµÐ¿Ð¾.

    Ð’Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÑ‚ None, ÐµÑÐ»Ð¸ Ð¿Ñ€Ð¸Ð²ÑÐ·ÐºÐ¸ Ð½ÐµÑ‚ Ð¸Ð»Ð¸ Ð³ÐµÐ¾ ÑÑ‚Ð°Ñ€ÑˆÐµ TG_GEO_FRESH.
    back_min - Ð·Ð° ÑÐºÐ¾Ð»ÑŒÐºÐ¾ ÐºÑƒÑ€ÑŒÐµÑ€ Ñ„Ð¸Ð·Ð¸Ñ‡ÐµÑÐºÐ¸ Ð´Ð¾ÐµÐ´ÐµÑ‚ Ð´Ð¾ Ð´ÐµÐ¿Ð¾ (Ð°Ð½Ñ‚Ð¸-Ð¿Ñ€Ñ‹Ð¶ÐºÐ¸ ÑƒÐ¶Ðµ
    Ð¿Ñ€Ð¸Ð¼ÐµÐ½ÐµÐ½Ñ‹ Ð¼ÐµÐ´Ð¸Ð°Ð½Ð¾Ð¹ Ð¿Ñ€Ð¸ Ð¿Ñ€Ð¸Ñ‘Ð¼Ðµ Ñ‚Ð¾Ñ‡ÐºÐ¸, Ð·Ð´ÐµÑÑŒ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ñ€Ð°ÑÑÑ‚Ð¾ÑÐ½Ð¸Ðµ).
    """
    chat = c.get("tg_chat_id") or ""
    pos = STATE["tg_pos"].get(chat)
    if not pos or not depot:
        return None
    now = now or time.time()
    age = now - pos["ts"]
    if age > TG_GEO_FRESH:
        return None
    g = {"lat": pos["lat"], "lng": pos["lng"], "age_min": int(age // 60),
         "live": bool(pos.get("live"))}
    km = haversine_km(pos, depot)
    if km <= TG_GEO_AT_PLACE:
        g["at_depot"] = True
        g["back_min"] = 0
    else:
        kmh, _src = _courier_speed(c)
        g["at_depot"] = False
        g["back_min"] = int(min(480, max(1, round(
            km * ROAD_FACTOR / kmh * 60))))
    # Ñ„Ð°Ð·Ð° Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸: Ð·Ð°ÐºÐ°Ð·Ñ‹ Ð²Ñ‹Ð´Ð°Ð½Ñ‹? Ð·Ð°Ð³Ñ€ÑƒÐ·ÐºÐ° Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸ Ð·Ð°Ñ„Ð¸ÐºÑÐ¸Ñ€Ð¾Ð²Ð°Ð½Ð°?
    out_orders = _courier_out_orders(c)
    has_out = bool(out_orders)
    load = STATE["tg_load"].get(chat) or {}
    g["has_out"] = has_out
    g["loaded"] = bool(load.get("loaded_at"))
    if has_out and not g["at_depot"]:
        g["delivering"] = True   # Ð²Ñ‹Ð´Ð°Ð½Ñ‹ Ð¸ Ð½Ðµ Ñƒ Ñ‚Ð¾Ñ‡ÐºÐ¸ â€” Ð·Ð½Ð°Ñ‡Ð¸Ñ‚, ÐµÐ´ÐµÑ‚ Ñ Ð·Ð°ÐºÐ°Ð·Ð°Ð¼Ð¸
        # Ñ‡ÐµÑÑ‚Ð½Ñ‹Ð¹ Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚: Ð´Ð¾Ñ€Ð¾Ð³Ð° Ð´Ð¾ Ñ‚Ð¾Ñ‡ÐºÐ¸ + Ñ€Ð°Ð·Ð²Ð¾Ð· Ð½ÐµÐ²Ñ‹Ð´Ð°Ð½Ð½Ñ‹Ñ…-Ð½ÐµÐ´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½Ð½Ñ‹Ñ….
        # Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½Ð½Ñ‹ÐµÂ» Ð²Ñ‹Ð²Ð¾Ð´Ð¸Ð¼ Ð¿Ð¾ Ð³ÐµÐ¾ (Ð´Ð¾Ð»Ð³Ð¾ ÑÑ‚Ð¾ÑÐ» Ñƒ Ð°Ð´Ñ€ÐµÑÐ°) â€” Ð´Ð»Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚Ð°
        # Ð¸Ñ… ÑÑ‡Ð¸Ñ‚Ð°ÐµÐ¼ Ñ€Ð°Ð·Ð²ÐµÐ·Ñ‘Ð½Ð½Ñ‹Ð¼Ð¸; ÑÑ‚Ð°Ñ‚ÑƒÑ Ð·Ð°ÐºÐ°Ð·Ð° Ð½Ðµ Ñ‚Ñ€Ð¾Ð³Ð°ÐµÐ¼
        dst = STATE["tg_deliv"].get(chat) or {}
        rem = sum(1 for o in out_orders
                  if not dst.get(o["id"], {}).get("at"))
        per = _courier_del_avg_min(c)
        g["back_min"] = int(min(480, g["back_min"] + rem * per))
    if not has_out and not g["at_depot"]:
        # Ð·Ð°ÐºÐ°Ð·Ñ‹ ÐµÑ‰Ñ‘ Ð½Ðµ Ð² Ð¼Ð°ÑˆÐ¸Ð½Ðµ: Ñ‡ÐµÑÑ‚Ð½Ñ‹Ð¹ ETA â€” ÑÐ½Ð°Ñ‡Ð°Ð»Ð° Ð´Ð¾ÐµÑ…Ð°Ñ‚ÑŒ Ð´Ð¾ Ñ‚Ð¾Ñ‡ÐºÐ¸
        kmh2, _ = _courier_speed(c)
        g["to_point_min"] = int(min(240, max(1, round(
            km * ROAD_FACTOR / kmh2 * 60))))
    # ÑÑ‚Ð¾Ð¸Ñ‚ Ð»Ð¸ ÐºÑƒÑ€ÑŒÐµÑ€ Ð¿Ñ€ÑÐ¼Ð¾ ÑÐµÐ¹Ñ‡Ð°Ñ Ñƒ Ð¾Ð´Ð½Ð¾Ð³Ð¾ Ð¸Ð· ÑÐ²Ð¾Ð¸Ñ… Ð²Ñ‹Ð´Ð°Ð½Ð½Ñ‹Ñ… Ð·Ð°ÐºÐ°Ð·Ð¾Ð²
    best, best_km = None, None
    for o in out_orders:
        d = haversine_km(pos, o)
        if d <= TG_GEO_AT_PLACE and (best_km is None or d < best_km):
            best, best_km = o["address"], d
    if best:
        g["at_order"] = best
    return g


def _flip_return_route(courier_id):
    """Ð’ÑÐµ Ð·Ð°ÐºÐ°Ð·Ñ‹ Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ñ‹: Ñ€Ð°Ð·Ð²Ð¾Ñ€Ð°Ñ‡Ð¸Ð²Ð°ÐµÐ¼ Ñ‚Ñ€Ð°ÑÑÑƒ â€” ÐºÑƒÑ€ÑŒÐµÑ€ ÐµÐ´ÐµÑ‚ Ð´Ð¾Ð¼Ð¾Ð¹
    Ð¿Ð¾ ÑƒÐ»Ð¸Ñ†Ð°Ð¼, ÐºÐ°Ñ€Ñ‚Ð° Ñ€Ð¸ÑÑƒÐµÑ‚ Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚ (ret_geom Ð²Ð¼ÐµÑÑ‚Ð¾ out_geom)."""
    c = next((x for x in STATE["couriers"] if x["id"] == courier_id), None)
    if c and c.get("out_geom") and not any(
            o.get("assigned") == courier_id and o.get("status") == "out"
            for o in STATE["orders"]):
        c["ret_geom"] = list(reversed(c["out_geom"]))
        c.pop("out_geom", None)


def _bot_close_delivered(oid, outcome="delivered", reason=""):
    """Ð—Ð°ÐºÑ€Ñ‹Ñ‚ÑŒ Ð·Ð°ÐºÐ°Ð· Ð¿Ð¾ Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð¶Ð´ÐµÐ½Ð¸ÑŽ ÐšÐ£Ð Ð¬Ð•Ð Ð (Ð±ÐµÐ· ÑÐµÑÑÐ¸Ð¸): Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½ Ð¸Ð»Ð¸
    Ð¾Ñ‚Ð¼ÐµÐ½Ñ‘Ð½ (Ñ Ð¿Ñ€Ð¸Ñ‡Ð¸Ð½Ð¾Ð¹ Ð¸Ð· Ð´Ð¸Ð°Ð»Ð¾Ð³Ð° Ð±Ð¾Ñ‚Ð°).

    Ð¢Ð¾Ñ‚ Ð¶Ðµ ÑÐ»ÐµÐ´, Ñ‡Ñ‚Ð¾ Ñƒ Ñ€ÑƒÑ‡Ð½Ð¾Ð³Ð¾ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð¸Ñ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð¾Ð¼: Ð°Ñ€Ñ…Ð¸Ð², ÑÐ½ÑÑ‚Ð¸Ðµ Ð¸Ð·
    Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸, Ñ€Ð°Ð·Ð²Ð¾Ñ€Ð¾Ñ‚ Ñ‚Ñ€Ð°ÑÑÑ‹ Ð½Ð° Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚, Ð¸Ð½Ð²Ð°Ð»Ð¸Ð´Ð°Ñ†Ð¸Ñ Ð¿Ð»Ð°Ð½Ð°.
    """
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order or (order.get("status") or "ready") != "out":
        return False, ""
    cid = order.get("assigned") or ""
    c = next((x for x in STATE["couriers"] if x["id"] == cid), None)
    _archive_order(order, outcome, c["name"] if c else cid, cid, reason=reason)
    STATE["orders"] = [o for o in STATE["orders"] if o["id"] != oid]
    _flip_return_route(cid)
    _persist_orders()
    _invalidate_plan(drop_plan=True, pid=_obj_point(order))
    _bump()
    log.info("bot confirm: Ð·Ð°ÐºÐ°Ð· %s (%s) Ð·Ð°ÐºÑ€Ñ‹Ñ‚ ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð¼ %s (%s)",
             oid, order.get("address"), c.get("name") if c else cid, outcome)
    who = c.get("name") if c else cid
    _ev("bot", (f"Ð¾Ñ‚Ð¼ÐµÐ½Ð¸Ð» Â«{order.get('address')}Â» â€” {who}, Ð¿Ñ€Ð¸Ñ‡Ð¸Ð½Ð°: {reason}"
                if outcome == "cancelled" else
                f"Ð·Ð°ÐºÑ€Ñ‹Ð» Â«{order.get('address')}Â» â€” {who} Ð¿Ð¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸Ð»"))
    return True, who


# ÐŸÑ€Ð¸Ñ‡Ð¸Ð½Ñ‹ Ð¾Ñ‚Ð¼ÐµÐ½Ñ‹ â€” Ð¿Ð¾ Ñ‡Ð°ÑÑ‚Ð¾Ñ‚Ðµ (Ñ‡Ð°Ñ‰Ðµ Ð²ÑÐµÐ³Ð¾ Ð² Ð½Ð°Ñ‡Ð°Ð»Ðµ, Â«Ð”Ñ€ÑƒÐ³Ð¾ÐµÂ» Ð²ÑÐµÐ³Ð´Ð° Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ð¼)
_CANCEL_REASONS = [
    "Ð”Ð¾Ð»Ð³Ð¾Ðµ Ð¾Ð¶Ð¸Ð´Ð°Ð½Ð¸Ðµ",
    "Ð§ÐµÐ»Ð¾Ð²ÐµÐº Ð½Ðµ Ð¾Ñ‚Ð²ÐµÑ‡Ð°ÐµÑ‚",
    "ÐŸÑ€Ð¾ÑÑ‚Ð¾ Ð¾Ñ‚ÐºÐ°Ð·",
    "ÐÐµÐ¿Ñ€Ð°Ð²Ð¸Ð»ÑŒÐ½Ñ‹Ð¹ Ð·Ð°ÐºÐ°Ð·",
    "ÐŸÐ»Ð¾Ñ…Ð¾Ðµ ÐºÐ°Ñ‡ÐµÑÑ‚Ð²Ð¾ Ñ‚Ð¾Ð²Ð°Ñ€Ð°",
    "ÐÐµ Ñ‚Ð¾Ñ‚ Ð°Ð´Ñ€ÐµÑ",
    "Ð”Ñ€ÑƒÐ³Ð¾Ðµ",
]


def _bot_ask_text(o):
    return (f"ðŸ› ÐšÐ°Ð¶ÐµÑ‚ÑÑ, Ð·Ð°ÐºÐ°Ð· Ð¿Ð¾ Ð°Ð´Ñ€ÐµÑÑƒ <b>{_esc(o.get('address') or '')}</b> "
            "Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½. Ð­Ñ‚Ð¾ Ñ‚Ð°Ðº?")


def _bot_ask_kb(oid):
    return [[{"text": "âœ… Ð”Ð¾ÑÑ‚Ð°Ð²Ð¸Ð»", "callback_data": f"dlv:{oid}:y"}],
            [{"text": "âŒ ÐÐµÑ‚", "callback_data": f"dlv:{oid}:n"}],
            [{"text": "ðŸš« Ð—Ð°ÐºÐ°Ð· Ð¾Ñ‚Ð¼ÐµÐ½Ñ‘Ð½", "callback_data": f"dlv:{oid}:ref"}]]


def _tg_callback(cb):
    """ÐÐ°Ð¶Ð°Ñ‚Ð¸Ðµ Ð¸Ð½Ð»Ð°Ð¹Ð½-ÐºÐ½Ð¾Ð¿ÐºÐ¸ ÐºÑƒÑ€ÑŒÐµÑ€Ð¾Ð¼: Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð¸Ð»?Â» â†’ Â«Ñ‚Ð¾Ñ‡Ð½Ð¾?Â» â†’ Ð·Ð°ÐºÑ€Ñ‹Ñ‚Ð¸Ðµ."""
    data = cb.get("data") or ""
    cbid = cb.get("id") or ""
    msg = cb.get("message") or {}
    chat = str((msg.get("chat") or {}).get("id") or "")
    if not data.startswith("dlv:"):
        _tg_answer_cb(cbid)
        return
    parts = data.split(":", 2)
    if len(parts) < 3 or not parts[1]:
        _tg_answer_cb(cbid, "ÐšÐ½Ð¾Ð¿ÐºÐ° Ð½Ðµ Ñ€Ð°ÑÐ¿Ð¾Ð·Ð½Ð°Ð½Ð°")
        return
    _, oid, act = parts
    # Ñ‚ÐµÑÑ‚-Ñ€ÐµÐ¶Ð¸Ð¼: ÐºÐ½Ð¾Ð¿ÐºÐ¸ Ð¶Ð¼Ñ‘Ñ‚ Ð¶Ð¸Ð²Ð¾Ð¹ Ñ‡ÐµÐ»Ð¾Ð²ÐµÐº Ð² Ñ€ÐµÐ´Ð¸Ñ€ÐµÐºÑ‚-Ñ‡Ð°Ñ‚Ðµ â€” Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‰Ð°ÐµÐ¼
    # Ð´Ð¸Ð°Ð»Ð¾Ð³ Ðº ÑÐ¸Ð½Ñ‚ÐµÑ‚Ð¸Ñ‡ÐµÑÐºÐ¾Ð¼Ñƒ Ñ‡Ð°Ñ‚Ñƒ ÐºÑƒÑ€ÑŒÐµÑ€Ð°, ÐºÐ¾Ñ‚Ð¾Ñ€Ð¾Ð¼Ñƒ Ð²Ñ‹Ð´Ð°Ð½ Ð·Ð°ÐºÐ°Ð·
    if TG_TEST_REDIRECT and chat == TG_TEST_REDIRECT:
        order0 = next((o for o in STATE["orders"] if o["id"] == oid), None)
        c0 = next((c for c in STATE["couriers"]
                   if c.get("id") == (order0 or {}).get("assigned")), None) \
            if order0 else None
        if c0 and c0.get("tg_chat_id"):
            chat = str(c0["tg_chat_id"])
    pend = STATE["tg_ask"].get(chat, {}).get(oid)
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    courier = next((c for c in STATE["couriers"]
                    if (c.get("tg_chat_id") or "") == chat), None)
    if not pend or not order or not courier or order.get("assigned") != courier.get("id"):
        if pend:
            _tg_edit_msg(chat, pend["msg"],
                         "Ð­Ñ‚Ð¾Ñ‚ Ð²Ð¾Ð¿Ñ€Ð¾Ñ ÑƒÐ¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÐµÐ½ â€” Ð·Ð°ÐºÐ°Ð· Ð·Ð°ÐºÑ€Ñ‹Ñ‚ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð¾Ð¼.")
            STATE["tg_ask"].get(chat, {}).pop(oid, None)
        _tg_answer_cb(cbid, "Ð£Ð¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÑŒÐ½Ð¾")
        return
    addr = _esc(order.get("address") or "")
    if act == "y" and pend["stage"] == "ask":
        pend["stage"] = "confirm"
        _tg_edit_msg(chat, pend["msg"], f"Ð¢Ð¾Ñ‡Ð½Ð¾ Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½? Ð—Ð°ÐºÐ°Ð·: <b>{addr}</b>",
                     [[{"text": "âœ… ÐŸÐ¾Ð´Ñ‚Ð²ÐµÑ€Ð´Ð¸Ñ‚ÑŒ", "callback_data": f"dlv:{oid}:ok"}],
                      [{"text": "â†©ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    elif act == "ref" and pend["stage"] == "ask":
        pend["stage"] = "refconfirm"
        _tg_edit_msg(chat, pend["msg"],
                     f"Ð¢Ð¾Ñ‡Ð½Ð¾ Ð¾Ñ‚Ð¼ÐµÐ½ÑÐµÐ¼? Ð—Ð°ÐºÐ°Ð·: <b>{addr}</b>",
                     [[{"text": "âœ… Ð”Ð°, Ð¾Ñ‚Ð¼ÐµÐ½ÑÐµÐ¼", "callback_data": f"dlv:{oid}:refyes"}],
                      [{"text": "â†©ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    elif act == "n" and pend["stage"] == "ask":
        pend["stage"] = "noconfirm"
        _tg_edit_msg(chat, pend["msg"],
                     f"Ð¢Ð¾Ñ‡Ð½Ð¾ ÐµÑ‰Ñ‘ Ð½ÐµÑ‚? Ð—Ð°ÐºÐ°Ð·: <b>{addr}</b>",
                     [[{"text": "âœ… Ð”Ð°, ÐµÑ‰Ñ‘ Ð²ÐµÐ·Ñƒ", "callback_data": f"dlv:{oid}:nok"}],
                      [{"text": "â†©ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    elif act == "nok" and pend["stage"] == "noconfirm":
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        _tg_edit_msg(chat, pend["msg"],
                     f"ÐŸÐ¾Ð½ÑÐ»: <b>{addr}</b> ÐµÑ‰Ñ‘ Ð² Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐµ. "
                     "Ð—Ð°ÐºÑ€Ð¾ÐµÑ‚ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€ Ð¸Ð»Ð¸ ÑÐ¿Ñ€Ð¾ÑÐ¸Ð¼ Ð¿Ð¾Ð·Ð¶Ðµ.")
        _tg_answer_cb(cbid)
        _ev("cour", f"{courier['name']}: Â«{order.get('address') or oid}Â» ÐµÑ‰Ñ‘ Ð² Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐµ")
    elif act == "refyes" and pend["stage"] == "refconfirm":
        pend["stage"] = "reason"
        _tg_edit_msg(chat, pend["msg"],
                     f"ÐŸÑ€Ð¸Ñ‡Ð¸Ð½Ð° Ð¾Ñ‚Ð¼ÐµÐ½Ñ‹: <b>{addr}</b>",
                     [[{"text": t, "callback_data": f"dlv:{oid}:r:{i}"}]
                      for i, t in enumerate(_CANCEL_REASONS)]
                     + [[{"text": "â†©ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    elif act.startswith("r:") and pend["stage"] == "reason":
        try:
            reason = _CANCEL_REASONS[int(act[2:])]
        except (IndexError, ValueError):
            reason = "Ð”Ñ€ÑƒÐ³Ð¾Ðµ"
        ok, name = _bot_close_delivered(oid, outcome="cancelled", reason=reason)
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        if ok:
            _tg_edit_msg(chat, pend["msg"],
                         f"ðŸ—‘ Ð—Ð°Ð¿Ð¸ÑÐ°Ð½Ð¾: <b>{addr}</b> â€” Ð·Ð°ÐºÐ°Ð· Ð¾Ñ‚Ð¼ÐµÐ½Ñ‘Ð½.\n"
                         f"ÐŸÑ€Ð¸Ñ‡Ð¸Ð½Ð°: <b>{_esc(reason)}</b>")
            _tg_answer_cb(cbid, "Ð—Ð°ÐºÐ°Ð· Ð¾Ñ‚Ð¼ÐµÐ½Ñ‘Ð½ âœ“")
        else:
            _tg_edit_msg(chat, pend["msg"], "ÐÐµ Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ð»Ð¾ÑÑŒ Ð·Ð°ÐºÑ€Ñ‹Ñ‚ÑŒ â€” ÑƒÐ¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÐµÐ½.")
            _tg_answer_cb(cbid, "Ð£Ð¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÑŒÐ½Ð¾")
    elif act == "ok" and pend["stage"] == "confirm":
        ok, name = _bot_close_delivered(oid)
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        if ok:
            _tg_edit_msg(chat, pend["msg"],
                         f"âœ… Ð—Ð°Ð¿Ð¸ÑÐ°Ð½Ð¾: <b>{addr}</b> Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½. Ð¡Ð¿Ð°ÑÐ¸Ð±Ð¾!")
            _tg_answer_cb(cbid, "Ð—Ð°ÐºÐ°Ð· Ð·Ð°ÐºÑ€Ñ‹Ñ‚ âœ“")
        else:
            _tg_edit_msg(chat, pend["msg"], "ÐÐµ Ð¿Ð¾Ð»ÑƒÑ‡Ð¸Ð»Ð¾ÑÑŒ Ð·Ð°ÐºÑ€Ñ‹Ñ‚ÑŒ â€” ÑƒÐ¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÐµÐ½.")
            _tg_answer_cb(cbid, "Ð£Ð¶Ðµ Ð½ÐµÐ°ÐºÑ‚ÑƒÐ°Ð»ÑŒÐ½Ð¾")
    elif act == "no":
        # Â«ÐÐ°Ð·Ð°Ð´Â»: Ð½Ð° ÑˆÐ°Ð³ Ð´Ð¸Ð°Ð»Ð¾Ð³Ð° Ð½Ð°Ð·Ð°Ð´, Ð´Ð¸Ð°Ð»Ð¾Ð³ Ð½Ðµ Ð·Ð°ÐºÑ€Ñ‹Ð²Ð°ÐµÐ¼
        if pend["stage"] in ("confirm", "refconfirm", "noconfirm"):
            pend["stage"] = "ask"
            _tg_edit_msg(chat, pend["msg"], _bot_ask_text(order), _bot_ask_kb(oid))
        elif pend["stage"] == "reason":
            pend["stage"] = "refconfirm"
            _tg_edit_msg(chat, pend["msg"],
                         f"Ð¢Ð¾Ñ‡Ð½Ð¾ Ð¾Ñ‚Ð¼ÐµÐ½ÑÐµÐ¼? Ð—Ð°ÐºÐ°Ð·: <b>{addr}</b>",
                         [[{"text": "âœ… Ð”Ð°, Ð¾Ñ‚Ð¼ÐµÐ½ÑÐµÐ¼", "callback_data": f"dlv:{oid}:refyes"}],
                          [{"text": "â†©ï¸ ÐÐ°Ð·Ð°Ð´", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    else:  # Â«Ð½ÐµÑ‚Â» â€” Ð·Ð°ÐºÐ°Ð· Ð¾ÑÑ‚Ð°Ñ‘Ñ‚ÑÑ Ð² Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐµ
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        _tg_edit_msg(chat, pend["msg"],
                     f"ÐŸÐ¾Ð½ÑÐ»: <b>{addr}</b> ÐµÑ‰Ñ‘ Ð² Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐµ. "
                     "Ð—Ð°ÐºÑ€Ð¾ÐµÑ‚ Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€ Ð¸Ð»Ð¸ ÑÐ¿Ñ€Ð¾ÑÐ¸Ð¼ Ð¿Ð¾Ð·Ð¶Ðµ.")
        _tg_answer_cb(cbid)
        _ev("cour", f"{courier['name']}: Â«{order.get('address') or oid}Â» ÐµÑ‰Ñ‘ Ð² Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐµ")


def _tg_handle_update(u):
    """ÐžÐ´Ð¸Ð½ Ð°Ð¿Ð´ÐµÐ¹Ñ‚ Ð¾Ñ‚ Telegram: Ñ‚ÐµÐºÑÑ‚ (/start), Ð³ÐµÐ¾Ð»Ð¾ÐºÐ°Ñ†Ð¸Ñ Ð¸Ð»Ð¸ ÐºÐ½Ð¾Ð¿ÐºÐ°."""
    cb = u.get("callback_query")
    if cb:
        _tg_callback(cb)
        return
    msg = u.get("message") or u.get("edited_message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    if not chat_id:
        return
    frm = msg.get("from") or {}
    login = frm.get("username") or " ".join(
        filter(None, [frm.get("first_name"), frm.get("last_name")])).strip() or chat_id
    STATE["tg_seen"][chat_id] = {"chat_id": chat_id, "login": login[:64],
                                 "ts": time.time()}
    if len(STATE["tg_seen"]) > 50:  # Ñ…Ñ€Ð°Ð½Ð¸Ð¼ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð½ÐµÐ´Ð°Ð²Ð½Ð¸Ñ…
        for k in sorted(STATE["tg_seen"], key=lambda x: STATE["tg_seen"][x]["ts"])[:-50]:
            STATE["tg_seen"].pop(k, None)
            STATE["tg_nagged"].pop(k, None)  # Ð°Ð½Ñ‚Ð¸ÑÐ¿Ð°Ð¼-Ð¿Ð°Ð¼ÑÑ‚ÑŒ Ñ‡Ð¸ÑÑ‚Ð¸Ð¼ Ð²Ð¼ÐµÑÑ‚Ðµ

    courier = next((c for c in STATE["couriers"]
                    if (c.get("tg_chat_id") or "") == chat_id), None)
    loc = msg.get("location")
    log.info("tg upd: chat=%s %s%s bound=%s", chat_id,
             "edit " if u.get("edited_message") else "msg ",
             ("live-geo" if loc.get("live_period") else
              "static-geo" if loc else "text"),
             courier["name"] if courier else "-")
    if loc:
        if courier:
            raw = {"lat": loc["latitude"], "lng": loc["longitude"],
                   "ts": time.time(), "live": bool(loc.get("live_period")),
                   "acc": loc.get("horizontal_accuracy") or 0}
            # Ð°Ð½Ñ‚Ð¸-Ð´Ñ€ÐµÐ±ÐµÐ·Ð³: Ð±ÑƒÑ„ÐµÑ€ Ð¿Ð¾ÑÐ»ÐµÐ´Ð½Ð¸Ñ… Ñ‚Ð¾Ñ‡ÐµÐº, ÑÐ³Ð»Ð°Ð¶Ð¸Ð²Ð°Ð½Ð¸Ðµ Ð¼ÐµÐ´Ð¸Ð°Ð½Ð¾Ð¹
            hist = STATE["tg_pos"].get(chat_id, {}).get("hist", [])
            hist = [h for h in hist if raw["ts"] - h["ts"] <= 600][-4:]
            hist.append(raw)
            recent = [h for h in hist if raw["ts"] - h["ts"] <= 600][-3:]
            lats = sorted(h["lat"] for h in recent)
            lngs = sorted(h["lng"] for h in recent)
            smoothed = {"lat": lats[len(lats) // 2], "lng": lngs[len(lngs) // 2],
                        "ts": raw["ts"], "acc": raw["acc"]}
            # Ð·Ð°Ð¼ÐµÑ€ ÑÐºÐ¾Ñ€Ð¾ÑÑ‚Ð¸ â€” Ð¿Ð¾ ÑÐ³Ð»Ð°Ð¶ÐµÐ½Ð½Ð¾Ð¼Ñƒ Ñ‚Ñ€ÐµÐºÑƒ: Ð¾Ð´Ð¸Ð½Ð¾Ñ‡Ð½Ñ‹Ð¹ GPS-Ð¿Ñ€Ñ‹Ð¶Ð¾Ðº
            # Ð³Ð°ÑÐ¸Ñ‚ÑÑ Ð¼ÐµÐ´Ð¸Ð°Ð½Ð¾Ð¹ Ð¸ Ð² Ð¾Ñ‚Ñ€ÐµÐ·Ð¾Ðº Ð½Ðµ Ð¿Ð¾Ð¿Ð°Ð´Ð°ÐµÑ‚
            prev_s = STATE["tg_pos"].get(chat_id, {}).get("sprev")
            if prev_s:
                _speed_geo_sample(courier["id"], prev_s, smoothed)
            STATE["tg_pos"][chat_id] = {
                "lat": smoothed["lat"], "lng": smoothed["lng"],
                "ts": raw["ts"], "live": raw["live"], "acc": raw["acc"],
                "hist": hist, "sprev": smoothed}
            _load_track(courier, smoothed, raw["ts"])
            _deliver_track(courier, smoothed, raw["ts"])
            _auto_status_track(courier, smoothed, raw["ts"])
            # Ð´Ð¸Ð°Ð»Ð¾Ð³Ð¸ Â«Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÐµÐ½?Â» Ð¸ Ñ‚Ñ€ÐµÐºÐµÑ€Ñ‹ Ð¿Ñ€Ð¾ÑÑ‚Ð¾Ñ â€” Ð² Ð‘Ð” Ð½Ðµ Ñ€ÐµÐ¶Ðµ Ñ€Ð°Ð·Ð° Ð² 15 Ñ
            # (ÑÑ‚Ð¾Ñ‚ Ð¶Ðµ Ð¿Ð¾Ñ‚Ð¾Ðº Ð¾Ð±Ñ€Ð°Ð±Ð°Ñ‚Ñ‹Ð²Ð°ÐµÑ‚ Ð½Ð°Ð¶Ð°Ñ‚Ð¸Ñ ÐºÐ½Ð¾Ð¿Ð¾Ðº â€” Ð³Ð¾Ð½Ð¾Ðº Ð½ÐµÑ‚)
            if raw["ts"] - STATE.get("_tg_persist_ts", 0) > 15:
                STATE["_tg_persist_ts"] = raw["ts"]
                try:
                    with _db_lock, _db() as cx:
                        cx.executemany(
                            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                            [("tg_ask", json.dumps(STATE["tg_ask"], ensure_ascii=False)),
                             ("tg_deliv",
                              json.dumps(STATE["tg_deliv"], ensure_ascii=False))])
                except sqlite3.Error:
                    pass
            _bump(geo=True)  # Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ðµ ÐºÑƒÑ€ÑŒÐµÑ€Ð° â€” ÐºÐ°Ñ€Ñ‚Ð° Ð¾Ð±Ð½Ð¾Ð²Ð¸Ñ‚ÑÑ (Ñ…Ð°Ð± Ð±Ð°Ñ‚Ñ‡Ð¸Ñ‚ â‰¥1 Ñ)
        else:
            # live-Ð»Ð¾ÐºÐ°Ñ†Ð¸Ñ ÑˆÐ»Ñ‘Ñ‚ Ð¿Ñ€Ð°Ð²ÐºÐ¸ ÐºÐ°Ð¶Ð´Ñ‹Ðµ Ð½ÐµÑÐºÐ¾Ð»ÑŒÐºÐ¾ ÑÐµÐºÑƒÐ½Ð´ â€” Â«Ð½Ðµ Ð¿Ñ€Ð¸Ð²ÑÐ·Ð°Ð½Â»
            # Ð¾Ñ‚Ð¿Ñ€Ð°Ð²Ð»ÑÐµÐ¼ Ð½Ðµ Ñ‡Ð°Ñ‰Ðµ Ñ€Ð°Ð·Ð° Ð² 30 Ð¼Ð¸Ð½ÑƒÑ‚ Ð½Ð° Ñ‡Ð°Ñ‚
            now_ts = time.time()
            if now_ts - STATE["tg_nagged"].get(chat_id, 0) > 1800:
                STATE["tg_nagged"][chat_id] = now_ts
                _tg_send(chat_id,
                          f"ÐŸÐ¾Ñ…Ð¾Ð¶Ðµ, Ð²Ð°Ñ ÐµÑ‰Ñ‘ Ð½Ðµ Ð¿Ñ€Ð¸Ð²ÑÐ·Ð°Ð»Ð¸ Ðº ÐºÑƒÑ€ÑŒÐµÑ€Ñƒ. ÐžÑ‚Ð¿Ñ€Ð°Ð²ÑŒÑ‚Ðµ ÑÑ‚Ð¾Ñ‚ ID "
                          f"Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑ‚Ñ€Ð°Ñ‚Ð¾Ñ€Ñƒ: <code>{chat_id}</code>")
    elif (msg.get("text") or "").strip().startswith("/start"):
        _tg_send(chat_id,
                 "ÐŸÑ€Ð¸Ð²ÐµÑ‚! Ð­Ñ‚Ð¾ Ð±Ð¾Ñ‚ Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸.\n\n"
                 "ÐÑƒÐ¶Ð½Ð° <b>Ð¶Ð¸Ð²Ð°Ñ Ð³ÐµÐ¾Ð»Ð¾ÐºÐ°Ñ†Ð¸Ñ</b>:\n"
                 "ÑÐºÑ€ÐµÐ¿ÐºÐ° â†’ Â«Ð“ÐµÐ¾Ð»Ð¾ÐºÐ°Ñ†Ð¸ÑÂ» â†’ Â«ÐŸÐ¾Ð´ÐµÐ»Ð¸Ñ‚ÑŒÑÑ Ð¼Ð¾ÐµÐ¹ Ð³ÐµÐ¾Ð»Ð¾ÐºÐ°Ñ†Ð¸ÐµÐ¹Â» â†’ "
                 "Ð²Ñ€ÐµÐ¼Ñ <b>Â«ÐŸÐ¾ÐºÐ° Ð½Ðµ Ð¾Ñ‚ÐºÐ»ÑŽÑ‡ÑƒÂ»</b>.\n\n"
                 "Ð¢Ð¾Ð³Ð´Ð° Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€ Ð²Ð¸Ð´Ð¸Ñ‚ Ð²Ð°Ñ Ð½Ð° ÐºÐ°Ñ€Ñ‚Ðµ Ð²ÑÑŽ ÑÐ¼ÐµÐ½Ñƒ.\n\n"
                 f"Ð’Ð°Ñˆ ID: <code>{chat_id}</code>\n"
                 "Ð¡ÐºÐ°Ð¶Ð¸Ñ‚Ðµ ÐµÐ³Ð¾ Ð°Ð´Ð¼Ð¸Ð½Ð¸ÑÑ‚Ñ€Ð°Ñ‚Ð¾Ñ€Ñƒ, Ð¸ Ð²Ð°Ñ Ð¿Ð¾Ð´ÐºÐ»ÑŽÑ‡Ð°Ñ‚ Ðº ÐºÑƒÑ€ÑŒÐµÑ€Ñƒ.")


def _tg_poll_loop():
    while True:
        try:
            r = requests.get(
                _tg_api("getUpdates"),
                params={"offset": STATE["tg_offset"], "timeout": 25,
                        "allowed_updates": json.dumps(
                            ["message", "edited_message", "callback_query"])},
                timeout=30)
            data = r.json()
            if not data.get("ok"):
                # 409 Conflict: Ð±Ð¾Ñ‚Ð° ÑƒÐ¶Ðµ ÑÐ»ÑƒÑˆÐ°ÐµÑ‚ Ð´Ñ€ÑƒÐ³Ð¾Ð¹ Ð¿Ñ€Ð¾Ñ†ÐµÑÑ. ÐÐµ Ð±Ð¾Ñ€ÐµÐ¼ÑÑ Ð·Ð°
                # getUpdates Ð² Ð»Ð¾Ð± â€” Ð¶Ð´Ñ‘Ð¼: Ð²Ñ‚Ð¾Ñ€Ð¾Ð¹ Ð¸Ð½ÑÑ‚Ð°Ð½Ñ ÑƒÐ¼Ñ€Ñ‘Ñ‚ Ð¸ ÐºÐ°Ð½Ð°Ð» Ð²ÐµÑ€Ð½Ñ‘Ñ‚ÑÑ.
                if r.status_code == 409:
                    log.warning("tg poll: Ð±Ð¾Ñ‚ ÑƒÐ¶Ðµ ÑÐ»ÑƒÑˆÐ°ÐµÑ‚ÑÑ Ð´Ñ€ÑƒÐ³Ð¸Ð¼ Ð¿Ñ€Ð¾Ñ†ÐµÑÑÐ¾Ð¼ "
                                "(409) â€” Ð¿Ð¾Ð²Ñ‚Ð¾Ñ€ Ñ‡ÐµÑ€ÐµÐ· 5 Ð¼Ð¸Ð½")
                    time.sleep(300)
                    continue
                log.warning("tg poll: api error %s", data.get("description"))
                time.sleep(10)
                continue
            for u in data.get("result", []):
                STATE["tg_offset"] = max(STATE["tg_offset"], u.get("update_id", 0) + 1)
                _tg_handle_update(u)
        except requests.RequestException as e:
            log.warning("tg poll: %s", e)
            time.sleep(5)
        except Exception as e:  # Ð½ÐµÐ¾Ð¶Ð¸Ð´Ð°Ð½Ð½Ñ‹Ð¹ Ñ„Ð¾Ñ€Ð¼Ð°Ñ‚ â€” Ð½Ðµ Ñ€Ð¾Ð½ÑÐµÐ¼ Ð¿Ð¾Ð»Ð»ÐµÑ€
            log.warning("tg update parse: %s", e)
            time.sleep(2)


def _tg_start_polling():
    """Ð—Ð°Ð¿ÑƒÑÐº Ð¿Ð¾Ð»Ð»ÐµÑ€Ð° Ð¿Ñ€Ð¸ ÑÑ‚Ð°Ñ€Ñ‚Ðµ, ÐµÑÐ»Ð¸ Ð·Ð°Ð´Ð°Ð½ Ñ‚Ð¾ÐºÐµÐ½ Ð±Ð¾Ñ‚Ð°."""
    if not CFG["tg_bot_token"]:
        return
    try:
        me = requests.get(_tg_api("getMe"), timeout=10).json().get("result") or {}
        STATE["tg_bot"] = "@" + me.get("username", "")
        log.info("tg bot: %s", STATE["tg_bot"])
    except requests.RequestException as e:
        log.warning("tg getMe failed: %s", e)
    if not CFG["tg_poll"]:
        log.info("tg bot: Ð¿Ð¾Ð»Ð»ÐµÑ€ Ð²Ñ‹ÐºÐ»ÑŽÑ‡ÐµÐ½ (tg_poll=0) â€” Ð³ÐµÐ¾Ð»Ð¾ÐºÐ°Ñ†Ð¸Ð¸ ÑÐ»ÑƒÑˆÐ°ÐµÑ‚ "
                 "Ð´Ñ€ÑƒÐ³Ð¾Ð¹ ÑÐµÑ€Ð²ÐµÑ€")
        return
    threading.Thread(target=_tg_poll_loop, daemon=True).start()


def _ev(actor, text):
    """Ð›ÐµÐ½Ñ‚Ð° Ð°ÐºÑ‚Ð¸Ð²Ð½Ð¾ÑÑ‚Ð¸ Ð² UI: bot=Ð±Ð¾Ñ‚, disp=Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€, cour=ÐºÑƒÑ€ÑŒÐµÑ€, sys=ÑÐ¸ÑÑ‚ÐµÐ¼Ð°."""
    STATE["events"].append({"t": int(time.time()), "actor": actor,
                            "text": str(text)[:200]})
    del STATE["events"][:-60]  # Ñ…Ñ€Ð°Ð½Ð¸Ð¼ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÑÐ²ÐµÐ¶Ð¸Ðµ
    _bump()


def _payload(me=None, myp=None):
    """ÐžÑ‚Ð²ÐµÑ‚ Ð¿Ð¾ÑÐ»Ðµ Ð¼ÑƒÑ‚Ð°Ñ†Ð¸Ð¸: ÑÐ¾ÑÑ‚Ð¾ÑÐ½Ð¸Ðµ + ÐºÐ²Ð¾Ñ‚Ð° ORS + ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸ÐºÐ¸ Ð´Ð½Ñ + Ñ‚ÐµÐºÑƒÑ‰Ð¸Ð¹ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒ.

    me/myp Ð·Ð°Ð´Ð°ÑŽÑ‚ÑÑ ÑÐ²Ð½Ð¾ Ð¿Ñ€Ð¸ WS-Ð±Ñ€Ð¾Ð´ÐºÐ°ÑÑ‚Ðµ (Ñ‚Ð°Ð¼ Ð½ÐµÑ‚ ÑÐµÑÑÐ¸Ð¸ Ð·Ð°Ð¿Ñ€Ð¾ÑÐ°):
    payload ÐºÐ¾Ð½ÐºÑ€ÐµÑ‚Ð½Ð¾Ð³Ð¾ Ð´ÐµÐ¿Ð¾ Ð´Ð»Ñ Ð²ÑÐµÑ… ÐµÐ³Ð¾ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑ‡Ð¸ÐºÐ¾Ð².
    """
    me = _me() if me is None else me
    now = time.time()
    if myp is None:
        myp = _my_point() if me else ""
    else:
        myp = myp or ""
    couriers = []
    for c in STATE["couriers"]:
        cc = dict(c)
        home = _home_point(c)
        pos = STATE["tg_pos"].get(c.get("tg_chat_id") or "")
        if pos and now - pos["ts"] < TG_POS_TTL:
            cc["pos"] = {k: v for k, v in pos.items() if k != "hist"}
        cur = _speed_current_kmh(pos, now)
        if cur is not None:
            cc["cur_kmh"] = cur
        avg_kmh, avg_src = _courier_speed(c)
        cc["avg_kmh"] = round(avg_kmh, 1)
        cc["speed_src"] = avg_src
        geo = _courier_geo(c, home, now)
        if geo:
            cc["geo"] = geo
        # Ð°ÐºÑ‚Ð¸Ð²Ð½Ð°Ñ Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ°: Ð²Ñ‹Ð´Ð°Ð½Ð½Ñ‹Ðµ Ð·Ð°ÐºÐ°Ð·Ñ‹ Ð² Ð¿Ð¾Ñ€ÑÐ´ÐºÐµ Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ (Ð¼Ð°Ñ€ÑˆÑ€ÑƒÑ‚ Ð¼Ð¾Ð³
        # ÑƒÐ¶Ðµ ÑƒÐ¹Ñ‚Ð¸ Ð¸Ð· Ð¿Ð»Ð°Ð½Ð° â€” ÐºÐ°Ñ€Ñ‚Ð° Ñ€Ð¸ÑÑƒÐµÑ‚ ÐµÐ³Ð¾ Ð¿ÑƒÐ½ÐºÑ‚Ð¸Ñ€Ð¾Ð¼ Ð¿Ð¾ ÑÑ‚Ð¸Ð¼ Ð´Ð°Ð½Ð½Ñ‹Ð¼)
        outs = sorted((o for o in STATE["orders"]
                       if o.get("assigned") == c["id"]
                       and (o.get("status") or "ready") == "out"),
                      key=lambda o: (o.get("out_no", float("inf")),
                                     o.get("out_at") or "", o["id"]))
        if outs:
            cc["out_route"] = {
                # [lat, lng, order_id]: id Ð½ÑƒÐ¶ÐµÐ½ Ñ„Ñ€Ð¾Ð½Ñ‚Ñƒ, Ñ‡Ñ‚Ð¾Ð±Ñ‹ ÐºÑ€Ð°ÑÐ¸Ñ‚ÑŒ Ñ‚Ð¾Ñ‡ÐºÐ¸
                # Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ¸ Ð²Ñ‹Ð±Ñ€Ð°Ð½Ð½Ð¾Ð³Ð¾ ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð² ÐµÐ³Ð¾ Ñ†Ð²ÐµÑ‚
                "stops": [[o["lat"], o["lng"], o["id"]] for o in outs],
                "home": {"lat": home["lat"], "lng": home["lng"]} if home else None,
            }
            if c.get("out_geom"):
                cc["out_route"]["geom"] = c["out_geom"]
        elif c.get("status") == "away" and c.get("ret_geom"):
            # Ð²Ð¾Ð·Ð²Ñ€Ð°Ñ‚ Ð½Ð° Ð±Ð°Ð·Ñƒ: Ð¾Ð±Ñ€Ð°Ñ‚Ð½Ð°Ñ Ñ‚Ñ€Ð°ÑÑÐ° Ð±ÐµÐ· Ñ‚Ð¾Ñ‡ÐµÐº Ð´Ð¾ÑÑ‚Ð°Ð²ÐºÐ¸
            cc["out_route"] = {
                "stops": [],
                "home": {"lat": home["lat"], "lng": home["lng"]} if home else None,
                "geom": c["ret_geom"],
            }
        couriers.append(cc)
    # ÐºÑƒÑ€ÑŒÐµÑ€Ñ‹ Ð²Ð¸Ð´Ð½Ñ‹ Ð²ÑÐµÐ¼ Ð´ÐµÐ¿Ð¾, Ð½Ð¾ ÑÐ²Ð¾Ð¸ â€” Ð¿ÐµÑ€Ð²Ñ‹Ð¼Ð¸ (ÑÑ‚Ð°Ð±Ð¸Ð»ÑŒÐ½Ð¾ Ð¿Ð¾ Ð¸ÑÑ…Ð¾Ð´Ð½Ð¾Ð¼Ñƒ Ð¿Ð¾Ñ€ÑÐ´ÐºÑƒ)
    couriers.sort(key=lambda cc: 0 if _obj_point(cc) == myp else 1)
    st = {k: v for k, v in STATE.items()
          if k not in ("tg_seen", "tg_pos", "tg_offset", "tg_nagged", "tg_load",
                       "tg_deliv", "tg_away", "plans", "advice_modes", "solving")}
    seen = sorted(STATE["tg_seen"].values(), key=lambda x: -x["ts"])[:20]
    # Ð¶Ð¸Ð²Ñ‹Ðµ ÑÑ‡Ñ‘Ñ‚Ñ‡Ð¸ÐºÐ¸ Ð¿Ð¾ Ñ‚Ð¾Ñ‡ÐºÐ°Ð¼: ÐºÑƒÑ€ÑŒÐµÑ€Ñ‹ + Ð°Ð´Ð¼Ð¸Ð½Ñ‹ Ð¾Ð½Ð»Ð°Ð¹Ð½
    now2 = time.time()
    with _ONLINE_LOCK:
        for sid in [s for s, r in ONLINE.items() if now2 - r["last"] > ONLINE_WINDOW * 4]:
            ONLINE.pop(sid, None)  # Ð¿Ð¾Ð´Ñ‡Ð¸ÑÑ‚Ð¸Ð»Ð¸ Ð´Ð°Ð²Ð½Ð¾ ÑƒÑˆÐµÐ´ÑˆÐ¸Ñ…
        live = [r for r in ONLINE.values() if now2 - r["last"] < ONLINE_WINDOW]
    st["points"] = [dict(p,
                         couriers=sum(1 for c in STATE["couriers"]
                                      if _obj_point(c) == p["id"]),
                         admins=list(dict.fromkeys(  # Ð¾Ð´Ð¸Ð½ Ñ‡ÐµÐ»Ð¾Ð²ÐµÐº Ð² Ð½ÐµÑÐºÐ¾Ð»ÑŒÐºÐ¸Ñ…
                             a["email"] for a in live  # ÑÐµÑÑÐ¸ÑÑ… = Ð¾Ð´Ð½Ð° Ð·Ð°Ð¿Ð¸ÑÑŒ
                             if a["point_id"] == p["id"])))
                    for p in st.get("points") or []]
    # ÑÐºÐ¾ÑƒÐ¿ Ð´ÐµÐ¿Ð¾: ÑÐ²Ð¾Ð¸ Ð·Ð°ÐºÐ°Ð·Ñ‹, ÑÐ²Ð¾Ð¹ Ð¿Ð»Ð°Ð½ Ð¸ ÑÐ²Ð¾Ñ Ð¸ÑÑ‚Ð¾Ñ€Ð¸Ñ; Ñ‡ÑƒÐ¶Ð¸Ðµ â€” Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐºÑƒÑ€ÑŒÐµÑ€Ñ‹
    st["orders"] = [o for o in st.get("orders") or [] if _obj_point(o) == myp]
    st["plan"] = _plan_for(myp)
    # Ð¸Ð´Ñ‘Ñ‚ Ð»Ð¸ ÑÐµÐ¹Ñ‡Ð°Ñ Ñ€Ð°ÑÑ‡Ñ‘Ñ‚ Ñ€Ð°Ð·Ð²Ð¾Ð·ÐºÐ¸ Ð² Ð­Ð¢ÐžÐœ Ð´ÐµÐ¿Ð¾ (Ð±Ð»Ð¾ÐºÐ¸Ñ€ÑƒÐµÑ‚ UI Ð²ÑÐµÑ… ÐµÐ³Ð¾
    # Ð´Ð¸ÑÐ¿ÐµÑ‚Ñ‡ÐµÑ€Ð¾Ð²; ÑÐ»Ð¾Ð²Ð°Ñ€ÑŒ pid->bool Ð½Ð°Ñ€ÑƒÐ¶Ñƒ Ð½Ðµ Ð¾Ñ‚Ð´Ð°Ñ‘Ð¼)
    st["solving"] = bool(STATE.get("solving", {}).get(myp))
    return jsonify({**st, "couriers": couriers,
                    "tg": {"bot": STATE["tg_bot"], "seen": seen},
                    "ors": ors_status(), "today": _history_today(point_id=myp),
                    "cfg": {"tg": bool(CFG["tg_bot_token"])},
                    "me": me, "my_point": myp,
                    "users": _admin_users() if me and me["is_admin"] else []})


# ---------- live-Ñ€Ð°ÑÑÑ‹Ð»ÐºÐ°: WS-Ñ…Ð°Ð± (socket.io) Ð²Ð¼ÐµÑÑ‚Ð¾ long-poll /api/rev ----------

def _bump(geo: bool = False):
    """ÐŸÐ¾Ð¼ÐµÑ‚Ð¸Ñ‚ÑŒ ÑÐ¾ÑÑ‚Ð¾ÑÐ½Ð¸Ðµ Ð¸Ð·Ð¼ÐµÐ½Ñ‘Ð½Ð½Ñ‹Ð¼ Ð¸ Ñ€Ð°Ð·Ð±ÑƒÐ´Ð¸Ñ‚ÑŒ Ð¿Ð¾Ð´Ð¿Ð¸ÑÑ‡Ð¸ÐºÐ¾Ð² WS-Ñ…Ð°Ð±Ð°.

    Ð’Ñ‹Ð·Ñ‹Ð²Ð°ÐµÑ‚ÑÑ Ð¸Ð· Ð»ÑŽÐ±Ð¾Ð³Ð¾ Ð¿Ð¾Ñ‚Ð¾ÐºÐ° (HTTP-Ð¾Ð±Ñ€Ð°Ð±Ð¾Ñ‚Ñ‡Ð¸ÐºÐ¸, TG-Ð¿Ð¾Ð»Ð»ÐµÑ€); Ñ…Ð°Ð±
    ÐºÐ¾Ð°Ð»ÐµÑÐ¸Ñ‚ Ñ‡Ð°ÑÑ‚Ñ‹Ðµ Ð±Ð°Ð¼Ð¿Ñ‹. geo=True â€” Ð¾Ð±Ð½Ð¾Ð²Ð»ÐµÐ½Ð¸Ðµ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ Ð¾Ñ‚ÑÐ»ÐµÐ¶Ð¸Ð²Ð°Ð½Ð¸Ñ
    (Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ðµ ÐºÑƒÑ€ÑŒÐµÑ€Ð°): Ñ…Ð°Ð± ÑˆÐ»Ñ‘Ñ‚ Ñ‚Ð°ÐºÐ¾Ðµ Ð½Ðµ Ñ‡Ð°Ñ‰Ðµ Ñ€Ð°Ð·Ð° Ð² ÑÐµÐºÑƒÐ½Ð´Ñƒ; Ð»ÑŽÐ±Ð¾Ðµ
    ÑÐ¾Ð±Ñ‹Ñ‚Ð¸Ðµ (Ð²Ñ‹Ð´Ð°Ñ‡Ð°, ÑÑ‚Ð°Ñ‚ÑƒÑ, Ð·Ð°ÐºÐ°Ð·) Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÑÐµÑ‚ÑÑ ÑÑ€Ð°Ð·Ñƒ.
    """
    STATE["rev"] = STATE.get("rev", 0) + 1
    from . import ws  # Ð¿Ð¾Ð·Ð´Ð½Ð¸Ð¹ Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚: ws Ð¸Ð¼Ð¿Ð¾Ñ€Ñ‚Ð¸Ñ€ÑƒÐµÑ‚ core â€” Ñ€Ð²Ñ‘Ð¼ Ñ†Ð¸ÐºÐ»
    ws.notify_changed(geo=geo)

def _points_ids():
    """Ð’ÑÐµ id Ñ‚Ð¾Ñ‡ÐµÐº Ð²Ñ‹Ð´Ð°Ñ‡Ð¸ (Ñ€ÑƒÐ¼Ñ‹ WS-Ñ…Ð°Ð±Ð°)."""
    return [p["id"] for p in STATE.get("points") or []]


# ---------- Ð¸Ð½Ð²Ð°Ð»Ð¸Ð´Ð°Ñ†Ð¸Ñ Ð¿Ð»Ð°Ð½Ð° (Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐµÑ‚ÑÑ Ð¸ HTTP-Ñ€ÑƒÑ‡ÐºÐ°Ð¼Ð¸, Ð¸ TG-Ð±Ð¾Ñ‚Ð¾Ð¼) ----------
def _invalidate_plan(drop_plan=False, pid=None, courier_id=None, geo=False):
    """â•¨Æ’â•¨â•—â•¨â–‘â•¨â•œ â•¨â•œâ•¨â•¡ â•¨â”â•¨â•¡â•¤Ã‡â•¨â•¡â•¤Ã¼â•¤Ã§â•¨â••â•¤Ã©â•¤Ã¯â•¨â–“â•¨â–‘â•¨â•¡â•¨â• â•¨â–“ â•¤Ã¤â•¨â•›â•¨â•œâ•¨â•¡ Î“Ã‡Ã¶ â•¤Ã©â•¨â•›â•¨â•—â•¤Ã®â•¨â•‘â•¨â•› â•¨â”â•¨â•›â•¨â•â•¨â•¡â•¤Ã§â•¨â–‘â•¨â•¡â•¨â•/â•¤Ã¼â•¨â–’â•¤Ã‡â•¨â–‘â•¤Ã¼â•¤Ã¯â•¨â–“â•¨â–‘â•¨â•¡â•¨â•.

    pid Î“Ã‡Ã¶ â•¨â”¤â•¨â•¡â•¨â”â•¨â•›, â•¤Ã§â•¨â•¡â•¨â•£ â•¨â”â•¨â•—â•¨â–‘â•¨â•œ â•¨â••â•¨â•œâ•¨â–“â•¨â–‘â•¨â•—â•¨â••â•¨â”¤â•¨â••â•¤Ã‡â•¤Ã¢â•¨â•¡â•¨â• (None = â•¨â–“â•¤Ã¼â•¨â•¡ â•¨â”¤â•¨â•¡â•¨â”â•¨â•›: â•¨â”â•¤Ã‡â•¨â–‘â•¨â–“â•¨â•‘â•¨â–‘ â•¤Ã©â•¨â•›â•¤Ã§â•¨â•¡â•¨â•‘/â•¨â•œâ•¨â–‘â•¤Ã¼â•¤Ã©â•¤Ã‡â•¨â•›â•¨â•¡â•¨â•‘).
    drop_plan=True Î“Ã‡Ã¶ â•¤Ã¼â•¤Ã©â•¨â–‘â•¤Ã‡â•¤Ã¯â•¨â•£ â•¨â”â•¨â•—â•¨â–‘â•¨â•œ â•¤Ã©â•¨â•›â•¤Ã§â•¨â•œâ•¨â•› â•¨â•œâ•¨â•¡â•¨â–“â•¨â–‘â•¨â•—â•¨â••â•¨â”¤â•¨â•¡â•¨â•œ (â•¤Ã¢â•¨â”¤â•¨â–‘â•¨â•—â•¨â•¡â•¨â•œâ•¨â••â•¨â•¡ â•¨â•–â•¨â–‘â•¨â•‘â•¨â–‘â•¨â•–â•¨â–‘/â•¨â•‘â•¤Ã¢â•¤Ã‡â•¤Ã®â•¨â•¡â•¤Ã‡â•¨â–‘,
    â•¤Ã¼â•¨â•â•¨â•¡â•¨â•œâ•¨â–‘ â•¨â”¤â•¨â•¡â•¨â”â•¨â•›): â•¤Ã¼â•¨â–’â•¤Ã‡â•¨â–‘â•¤Ã¼â•¤Ã¯â•¨â–“â•¨â–‘â•¨â•¡â•¨â• â•¤Ã¼â•¤Ã‡â•¨â–‘â•¨â•–â•¤Ã¢. â•¨Ã¿â•¨â•œâ•¨â–‘â•¤Ã§â•¨â•¡ â•¨â”â•¨â•—â•¨â–‘â•¨â•œ â•¨â”â•¨â•›â•¨â•‘â•¨â–‘â•¨â•–â•¤Ã¯â•¨â–“â•¨â–‘â•¨â•¡â•¤Ã©â•¤Ã¼â•¤Ã… â•¤Ã¼ â•¨â”â•¨â•›â•¨â•â•¨â•¡â•¤Ã©â•¨â•‘â•¨â•›â•¨â•£
    â”¬Â½â•¤Ã¢â•¤Ã¼â•¤Ã©â•¨â–‘â•¤Ã‡â•¨â•¡â•¨â•—â”¬â•—, â•¨â”â•¨â•›â•¨â•‘â•¨â–‘ â•¨â–‘â•¨â”¤â•¨â•â•¨â••â•¨â•œâ•¨â••â•¤Ã¼â•¤Ã©â•¤Ã‡â•¨â–‘â•¤Ã©â•¨â•›â•¤Ã‡ â•¨â•œâ•¨â•¡ â•¨â•œâ•¨â–‘â•¨â•¢â•¨â•â•¤Ã¦â•¤Ã© â”¬Â½â•¨Ã¡â•¨â–‘â•¤Ã¼â•¤Ã¼â•¤Ã§â•¨â••â•¤Ã©â•¨â–‘â•¤Ã©â•¤Ã®â”¬â•—.
    courier_id Î“Ã‡Ã¶ â•¨â•‘â•¤Ã¢â•¤Ã‡â•¤Ã®â•¨â•¡â•¤Ã‡-â•¤Ã¼â•¨â”â•¨â•¡â•¤Ã¥â•¨â••â•¤Ã¤â•¨â••â•¤Ã§â•¨â•œâ•¨â–‘â•¤Ã… â•¨â••â•¨â•œâ•¨â–“â•¨â–‘â•¨â•—â•¨â••â•¨â”¤â•¨â–‘â•¤Ã¥â•¨â••â•¤Ã… (â•¤Ã¼â•¨â•â•¨â•¡â•¨â•œâ•¨â–‘ â•¤Ã¼â•¤Ã©â•¨â–‘â•¤Ã©â•¤Ã¢â•¤Ã¼â•¨â–‘, â•¤Ã¢â•¨â”¤â•¨â–‘â•¨â•—â•¨â•¡â•¨â•œâ•¨â••â•¨â•¡,
    â•¨â–“â•¨â•›â•¨â•–â•¨â–“â•¤Ã‡â•¨â–‘â•¤Ã© â•¨â•œâ•¨â–‘ â•¨â–’â•¨â–‘â•¨â•–â•¤Ã¢, â•¨â”â•¨â•¡â•¤Ã‡â•¨â•¡â•¨â–“â•¨â•›â•¨â”¤ â•¨â–“ â•¨â”¤â•¤Ã‡â•¤Ã¢â•¨â”‚â•¨â•›â•¨â•¡ â•¨â”¤â•¨â•¡â•¨â”â•¨â•›): â•¨â••â•¨â•– â•¨â–“â•¤Ã¼â•¨â•¡â•¤Ã  â•¨â”â•¨â•—â•¨â–‘â•¨â•œâ•¨â•›â•¨â–“ â•¤Ã¢â•¨â–’â•¨â••â•¤Ã‡â•¨â–‘â•¤Ã„â•¤Ã©â•¤Ã¼â•¤Ã… â•¤Ã©â•¨â•›â•¨â•—â•¤Ã®â•¨â•‘â•¨â•›
    â•¨â•â•¨â–‘â•¤Ã‡â•¤Ãªâ•¤Ã‡â•¤Ã¢â•¤Ã©â•¤Ã¯ â•¤Ã¬â•¤Ã©â•¨â•›â•¨â”‚â•¨â•› â•¨â•‘â•¤Ã¢â•¤Ã‡â•¤Ã®â•¨â•¡â•¤Ã‡â•¨â–‘, â•¨â•â•¨â–‘â•¤Ã‡â•¤Ãªâ•¤Ã‡â•¤Ã¢â•¤Ã©â•¤Ã¯ â•¨â•›â•¤Ã¼â•¤Ã©â•¨â–‘â•¨â•—â•¤Ã®â•¨â•œâ•¤Ã¯â•¤Ã  â•¨â•‘â•¤Ã¢â•¤Ã‡â•¤Ã®â•¨â•¡â•¤Ã‡â•¨â•›â•¨â–“ â•¤Ã¼â•¨â•›â•¤Ã â•¤Ã‡â•¨â–‘â•¨â•œâ•¤Ã…â•¤Ã„â•¤Ã©â•¤Ã¼â•¤Ã…
    â•¤Ã¼ â•¨â”â•¨â•›â•¨â•â•¨â•¡â•¤Ã©â•¨â•‘â•¨â•›â•¨â•£ â”¬Â½â•¤Ã¢â•¤Ã¼â•¤Ã©â•¨â–‘â•¤Ã‡â•¨â•¡â•¨â•—â”¬â•— Î“Ã‡Ã¶ â•¨â”¤â•¨â••â•¤Ã¼â•¨â”â•¨â•¡â•¤Ã©â•¤Ã§â•¨â•¡â•¤Ã‡ â•¨â•â•¨â•›â•¨â•¢â•¨â•¡â•¤Ã© â•¨â–“â•¤Ã¯â•¨â”¤â•¨â–‘â•¤Ã©â•¤Ã® â•¨â••â•¤Ã  â•¨â–’â•¨â•¡â•¨â•– â•¨â”â•¨â•¡â•¤Ã‡â•¨â•¡â•¤Ã¼â•¤Ã§â•¤Ã¦â•¤Ã©â•¨â–‘.
    """
    if courier_id is not None:
        changed = False
        for key, plan in list(STATE["plans"].items()):
            if plan is None:
                continue
            routes = plan.get("routes") or []
            kept = [r for r in routes if r.get("courier_id") != courier_id]
            if len(kept) == len(routes):
                continue  # ÑÑ‚Ð¾Ð³Ð¾ ÐºÑƒÑ€ÑŒÐµÑ€Ð° Ð² Ð¿Ð»Ð°Ð½Ðµ Ð½ÐµÑ‚ â€” Ñ‡ÑƒÐ¶Ð¸Ðµ Ð¼Ð°Ñ€ÑˆÑ€ÑƒÑ‚Ñ‹ Ð½Ðµ Ñ‚Ñ€Ð¾Ð³Ð°ÐµÐ¼
            changed = True
            if kept:
                plan["routes"] = kept
                plan["stale"] = True
            else:
                STATE["plans"].pop(key, None)
        if changed:
            try:
                _persist_meta()
            except sqlite3.Error:
                pass
        # Ñ€ÐµÐ°Ð»ÑŒÐ½Ð°Ñ Ð¿Ñ€Ð°Ð²ÐºÐ° Ð¿Ð»Ð°Ð½Ð¾Ð² â€” ÑÐ¾Ð±Ñ‹Ñ‚Ð¸Ðµ (Ð´Ð¾ÑÑ‚Ð°Ð²Ð»ÑÐµÐ¼ ÑÑ€Ð°Ð·Ñƒ); Ð¿Ð»Ð°Ð½Ð¾Ð²Ð¾Ðµ
        # ÑÐ¾Ð¿Ñ€Ð¾Ð²Ð¾Ð¶Ð´ÐµÐ½Ð¸Ðµ Ð³ÐµÐ¾-Ñ‚Ð¸ÐºÐ° â€” Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ðµ, Ñ…Ð°Ð± Ð±Ð°Ñ‚Ñ‡Ð¸Ñ‚ ÐµÐ³Ð¾ Ð´Ð¾ 1/Ñ
        _bump(geo=geo and not changed)
        return
    plans = STATE["plans"] if pid is None else {pid: STATE["plans"].get(pid)}
    for key, plan in list(plans.items()):
        if plan is None:
            continue
        if drop_plan:
            STATE["plans"].pop(key, None)
        else:
            plan["stale"] = True
    try:
        _persist_meta()
    except sqlite3.Error:
        pass
    _bump()  # â•¤Ã¼â•¨â•›â•¤Ã¼â•¤Ã©â•¨â•›â•¤Ã…â•¨â•œâ•¨â••â•¨â•¡ â•¨â••â•¨â•–â•¨â•â•¨â•¡â•¨â•œâ•¨â••â•¨â•—â•¨â•›â•¤Ã¼â•¤Ã® Î“Ã‡Ã¶ â•¨â•‘â•¨â•›â•¨â•œâ•¤Ã¼â•¨â•›â•¨â•—â•¨â•• â•¨â•›â•¨â–’â•¨â•œâ•¨â•›â•¨â–“â•¤Ã…â•¤Ã©â•¤Ã¼â•¤Ã… â•¤Ã¼â•¨â–‘â•¨â•â•¨â••




def _valid_latlng(lat, lng):
    return (-90 <= lat <= 90) and (-180 <= lng <= 180) and (lat != 0 or lng != 0)

