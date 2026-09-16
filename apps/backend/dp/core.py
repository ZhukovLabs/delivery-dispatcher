# -*- coding: utf-8 -*-
"""Ядро диспетчерской: конфиг, состояние, БД, скорости, ORS/OSRM, TG-бот, payload.

Перенесено с Flask-монолита без изменения логики; Flask-зависимости
замещены шимами dp.shims.
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

# Минск: UTC+3, без перехода на летнее время. Все «настенные» времена
# (часы плана, история, дедлайны, сообщения) пишем по Минску независимо
# от часового пояса сервера. Значения остаются наивными (без оффсета),
# чтобы не ломать сравнения с уже записанными данными.
_MN = timezone(timedelta(hours=3))


def _now() -> datetime:
    return datetime.now(_MN).replace(tzinfo=None)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # apps/backend

from itsdangerous import URLSafeTimedSerializer

# BASE_DIR задаётся в шапке модуля (родитель dp/, чтобы config.ini/db лежали как раньше)


def _pick_data_dir():
    """Первый каталог, доступный на запись: рядом с кодом -> DISPATCHER_DATA -> temp.
    На PaaS с read-only FS (Belmo) код лежит в /app только для чтения."""
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

# ---------- конфигурация (config.ini рядом с app.py) ----------

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

# Окружение перекрывает config.ini (деплой в облако: Render и т.п.)
CFG["ors_key"] = os.environ.get("ORS_KEY", "").strip() or CFG["ors_key"]
CFG["tg_bot_token"] = os.environ.get("TG_BOT_TOKEN", "").strip() or CFG["tg_bot_token"]
# tg_poll=0 — этот инстанс НЕ слушает бота (когда бот занят другим сервером,
# например локальный + облачный одновременно; Telegram отдаёт getUpdates одному)
if os.environ.get("TG_POLL", "").strip():
    try:
        CFG["tg_poll"] = 1 if os.environ["TG_POLL"].strip() not in ("0", "false", "no") else 0
    except ValueError:
        pass
CFG["admin_email"] = (os.environ.get("ADMIN_EMAIL", "").strip() or CFG["admin_email"]).lower()
CFG["admin_password"] = os.environ.get("ADMIN_PASSWORD", "").strip() or CFG["admin_password"]
for _pn in ("PORT", "SERVER_PORT", "P_SERVER_PORT"):  # PaaS/Pterodactyl отдают порт по-разному
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


# ---------- логирование ----------

_handlers = [logging.StreamHandler()]  # всегда: stderr (доступен на любом PaaS)
try:
    _handlers.insert(0, RotatingFileHandler(os.path.join(DATA_DIR, "dispatcher.log"),
                                            maxBytes=1_000_000, backupCount=3,
                                            encoding="utf-8"))
except OSError:
    pass  # read-only FS: живём только в stderr
logging.basicConfig(
    handlers=_handlers,
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("waitress").setLevel(logging.WARNING)
log = logging.getLogger("dispatcher")

DEFAULT_DEPOT = {"address": "ул. Подгорная 12/1, Гомель", "lat": 52.44146, "lng": 31.01476}

STATE = {
    "rev": 0,            # счетчик изменений для long-poll /api/rev
    "depot": dict(DEFAULT_DEPOT),  # совместимый вид первой точки {address, lat, lng}
    "points": [],        # места выдачи: {"id", "name", "address", "lat", "lng"}
    "couriers": [],      # {"id", "name", "status": base|away|off, "color", "back_min", "point_id"}
    "orders": [],        # {"id", "address", "lat", "lng"}
    "settings": {"speed_kmh": 60, "handover_min": 5, "max_orders": 5, "traffic": 1.25,
                 "lights_sec_per_km": 15, "auto_prio_min": 0, "reload_min": 10,
                 "hour_traffic": 1, "approach_center_min": 4, "approach_far_min": 2},
    "plans": {},         # pid -> план развозки (у каждого депо свой)
    "advice_modes": {},  # pid -> ручной выбор «ждать/не ждать» (now|split)
    "solving": {},       # pid -> True: в депо идёт расчёт развозки (клиенты
                         # блокируют UI, повторный запуск отклоняется)
    "color_seq": 0,      # монотонный счётчик: цвета не перемешиваются при удалениях
    # Telegram: кто писал боту (для привязки), последние локации курьеров, курсор getUpdates
    "tg_seen": {},       # chat_id -> {"chat_id", "login", "ts"}
    "tg_pos": {},        # chat_id -> {"lat", "lng", "ts", "live"}
    "tg_nagged": {},     # chat_id -> ts последнего «не привязан» (антиспам live-правок)
    "tg_load": {},       # chat_id -> {"since", "loaded_at"} — трекер выдачи заказов
    "tg_deliv": {},      # chat_id -> {order_id: {"since", "at"}} — вывод «доставлен»
                         # ТОЛЬКО для расчёта возврата; статус заказа не меняет
    "tg_away": {},       # chat_id -> {"since"} — авто-«в пути» при отъезде от точки
    "tg_ask": {},        # chat_id -> {order_id: {"msg", "stage"}} — бот ждёт «доставил?»
    "tg_offset": 0,
    "tg_bot": "",        # @username бота (для подсказок в интерфейсе)
    "events": [],        # лента активности: {"t", "actor": bot|disp|cour|sys, "text"}
}

ROAD_FACTOR = 1.4  # запасной расчёт (если OSRM недоступен): прямая -> дорога
_PRIO_WEIGHT = 60  # вес минуты доставки приоритетного заказа (против 1 у обычного)
MAX_POINTS = 10    # максимум мест выдачи


def _depot_view():
    """Совместимый со старым API вид первой точки выдачи ({"address","lat","lng"})."""
    p = (STATE.get("points") or [None])[0]
    if not p:
        return dict(DEFAULT_DEPOT)
    return {"address": p["address"], "lat": p["lat"], "lng": p["lng"]}


def _home_point(courier):
    """Точка выдачи курьера (или первая, если привязка не задана/битая)."""
    pid = (courier.get("point_id") or "").strip()
    for p in STATE.get("points") or []:
        if p["id"] == pid:
            return p
    return (STATE.get("points") or [None])[0]


def _obj_point(x):
    """Точка выдачи заказа/курьера: пустая привязка = первая точка."""
    pid = (x.get("point_id") or "").strip()
    if pid and any(p["id"] == pid for p in STATE.get("points") or []):
        return pid
    return (STATE.get("points") or [{}])[0].get("id") or ""

OSRM_URLS = [
    "https://routing.openstreetmap.de/routed-car",  # серверы сообщества OSM (FOSSGIS) — надёжнее
    "http://router.project-osrm.org",               # официальный демо-сервер — запасной
]
_osrm_base = None  # последний рабочий сервер (проверяется первым)

# OpenRouteService: основной источник матриц/геометрии (по ключу, бесплатный тариф).
# Квота суток ограничена, поэтому: считаем запросы сами, при приближении
# к лимиту заранее уходим на OSRM, а при 429/403 отключаем ORS до
# восстановления (сутки — до полуночи UTC, минутный лимит — на 5 минут).
ORS_KEY = CFG["ors_key"]
ORS_BASE = "https://api.openrouteservice.org"
ORS_SOFT_LIMIT = 1800   # запас до паспортных 2000/сутки: дальше не тратим квоту
ORS_MAX_POINTS = 50     # лимит бесплатного тарифа на размер матрицы
ORS_STATE = {"day": None, "used": 0, "disabled_until": None, "last_error": None}

PALETTE = ["#e8482b", "#2563eb", "#059669", "#9333ea",
           "#d97706", "#0891b2", "#be185d", "#4d7c0f"]
STATUSES = {"base", "away", "off"}

# ---------- персистентность (SQLite) ----------

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
    # (таблица, колонка, DDL) — выполняется, если колонки ещё нет
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
        conn.executescript(_DB_SCHEMA)  # идемпотентно; переживает удаление файла на ходу
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
        try:  # темп доставок -> фоллбек-замер скорости
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
    """Строки истории за последние `days` дней + сводка (новые — первыми).

    point_id — только заказы этого депо (None = все; пустая point_id у старых
    строк трактуется как первая точка).
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


# ---------- индивидуальная скорость курьера ----------
# Замер по гео: пары СГЛАЖЕННЫХ (медиана) точек live-локации с dt >= 15 c,
# отрезком >= 40 м и скоростью 3..80 км/ч добавляют метры/секунды в speed_day
# за сегодня. Одиночный GPS-прыжок гасится медианой (не попадает в трек),
# мелкая дрожь на месте — порогом дистанции, выброс «1000 км/ч» — потолком,
# а дневная сумма усредняет остаточный шум.
# Фоллбек по доставкам: средний цикл курьера против среднего по флоту
# за тот же день — отношение масштабирует скорость по умолчанию.
_SPEED_MIN_GEO_S = 180.0   # нужно >= 3 минут движения, чтобы доверять гео
_SPEED_MIN_DEL_N = 2       # нужно >= 2 доставок, чтобы сравнивать темп
_SPEED_KMH_BOUNDS = (5.0, 80.0)
_SPEED_RATIO_BOUNDS = (0.6, 1.7)  # фоллбек не может уводить далеко от нормы
_SPEED_SEG_MIN_M = 40.0    # короче 40 м — дрожь стояния, не движение
_SPEED_MAX_ACC_M = 100.0   # точность хуже 100 м — точка мусорная


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
    """Складывает отрезок между двумя гео-точками в дневной замер (или игнор)."""
    dt = cur["ts"] - prev["ts"]
    if not (15 <= dt <= 600):
        return
    if max(prev.get("acc") or 0, cur.get("acc") or 0) > _SPEED_MAX_ACC_M:
        return  # точность хуже 100 м — верить отрезку нельзя
    m = haversine_km(prev, cur) * ROAD_FACTOR * 1000.0
    if m < _SPEED_SEG_MIN_M:
        return  # дрожь на месте / шаг внутри погрешности GPS
    kmh = m / 1000.0 / (dt / 3600.0)
    if 3.0 <= kmh <= 80.0:
        _speed_add(courier_id, geo_m=m, geo_s=dt)


_SPEED_CUR_WINDOW = 240.0  # окно «текущей» скорости, секунды
_SPEED_CUR_MAX_AGE = 300.0  # гео старше 5 минут — текущей скорости нет


def _speed_current_kmh(pos, now):
    """Скорость «прямо сейчас» по свежему гео-треку. None — гео нет/устарело,
    0.0 — стоит на месте (точки есть, движения нет)."""
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
            continue  # GPS-прыжок
        if m < 15.0 and kmh < 5.0:
            t_sum += dt  # стоит на месте: время идёт, метры — нет
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
    """Средний цикл доставок по всем курьерам за день (или None)."""
    with _db_lock, _db() as c:
        r = c.execute("SELECT SUM(del_min) AS s, SUM(del_n) AS n FROM speed_day "
                      "WHERE day = ? AND del_n > 0", (day,)).fetchone()
    return (r["s"] / r["n"]) if r and r["n"] else None


def _courier_del_avg_min(courier):
    """Средние минуты на один доставленный заказ: свой темп, иначе флот, иначе 15."""
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
    """Скорость из строки дня: сначала гео, иначе темп доставок. None — нет данных."""
    if row["geo_s"] >= _SPEED_MIN_GEO_S:
        kmh = row["geo_m"] / row["geo_s"] * 3.6
        if kmh > 0.5:
            return min(_SPEED_KMH_BOUNDS[1], max(_SPEED_KMH_BOUNDS[0], kmh)), "geo"
    if row["del_n"] >= _SPEED_MIN_DEL_N:
        mine = row["del_min"] / row["del_n"]
        fleet = _speed_fleet_cycle_avg(row["day"])
        if fleet and mine > 0:
            ratio = fleet / mine  # цикл длиннее среднего -> медленнее
            ratio = min(_SPEED_RATIO_BOUNDS[1], max(_SPEED_RATIO_BOUNDS[0], ratio))
            return min(_SPEED_KMH_BOUNDS[1],
                       max(_SPEED_KMH_BOUNDS[0], default_kmh * ratio)), "delivery"
    return None


def _courier_speed(courier, settings=None):
    """(км/ч, источник) индивидуальной скорости курьера.

    Лестница: сегодня (гео -> доставки) -> вчера -> самый свежий день с замером
    -> настройка speed_kmh (источник "default").
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
    """Загрузка сохранённого состояния при старте (данные переживают рестарт)."""
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
        # сид: две точки выдачи Barak (Гомель)
        STATE["points"] = [
            {"id": uuid.uuid4().hex[:8], "name": "Подгорная",
             "address": "ул. Подгорная 12/1, Гомель",
             "lat": 52.44175316939852, "lng": 31.01452592124391},
            {"id": uuid.uuid4().hex[:8], "name": "Барыкина",
             "address": "ул. Барыкина 230Б, Гомель",
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
        # диалоги «доставлен?» и трекеры простоя переживают рестарт:
        # без этого после каждого деплоя бот переспрашивал, а нажатия
        # кнопок на старых сообщениях попадали в «уже неактуально»
        if meta.get(key):
            try:
                saved = json.loads(meta[key])
                if isinstance(saved, dict):
                    STATE[key].update(saved)
            except ValueError:
                pass
    # сверка: если бот уже спросил (asked), а диалог не восстановился —
    # сбрасываем asked, чтобы переспросил заново свежим сообщением
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
    elif meta.get("plan"):  # старый формат: единый план -> план первой точки
        try:
            p = json.loads(meta["plan"])
            first = (STATE.get("points") or [{}])[0].get("id")
            if isinstance(p, dict) and p.get("routes") and first:
                STATE["plans"][first] = p
        except ValueError:
            pass
    log.info("state loaded: %d couriers, %d orders", len(couriers), len(orders))


# секрет сессий: стабилен между рестартами, автогенерация при первом старте
def _session_secret():
    with _db_lock, _db() as c:
        row = c.execute("SELECT value FROM meta WHERE key = 'session_secret'").fetchone()
        if row:
            return row["value"]
        secret = uuid.uuid4().hex + uuid.uuid4().hex
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('session_secret', ?)",
                  (secret,))
        return secret


SESSION_SECRET = _session_secret()  # подпись cookie-сессий (шимы берут при старте)
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
            log.warning("OSRM %s недоступен: %s", base, e)
    return None


def osrm_table(points):
    """Матрицы времени (сек) и расстояния (м) по дорогам. (None, None), если OSRM недоступен."""
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
    """Геометрия маршрута по дорогам: список [lat, lng]. None при сбое."""
    coords = ";".join(f"{p['lng']:.6f},{p['lat']:.6f}" for p in points)
    data = osrm_get(f"/route/v1/driving/{coords}",
                    {"overview": "full", "geometries": "geojson"})
    try:
        return [[c[1], c[0]] for c in data["routes"][0]["geometry"]["coordinates"]]
    except (KeyError, IndexError, TypeError):
        return None


# ---------- OpenRouteService (с защитой от исчерпания квоты) ----------

def _seconds_to_utc_midnight():
    now = datetime.now(timezone.utc)
    return 24 * 3600 - (now.hour * 3600 + now.minute * 60 + now.second)


def _ors_available():
    """Смена суток обнуляет счётчик и снимает отключение."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if ORS_STATE["day"] != day:
        ORS_STATE.update(day=day, used=0, disabled_until=None, last_error=None)
    if ORS_STATE["disabled_until"] and time.time() < ORS_STATE["disabled_until"]:
        return False
    return ORS_STATE["used"] < ORS_SOFT_LIMIT


def ors_post(path, body):
    """POST к ORS. None => ORS недоступен или квота исчерпана (уходим на OSRM)."""
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
    except Exception as exc:  # сеть/таймаут — короткая пауза и фолбэк
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
    _ors_available()  # обновляет счётчики при смене суток
    paused = bool(ORS_STATE["disabled_until"] and time.time() < ORS_STATE["disabled_until"])
    return {"used": ORS_STATE["used"], "soft_limit": ORS_SOFT_LIMIT,
            "paused": paused, "last_error": ORS_STATE["last_error"]}


_MATRIX_CACHE = {}          # ключ(точки) -> (ts, durations, distances, provider)
_MATRIX_TTL = 1800          # 30 минут: дорожная сеть не меняется так быстро
_MATRIX_CACHE_MAX = 40


def _matrix_key(points):
    # депо — на своём месте: матрица индексна, одинаковый набор точек
    # с другим депо (депо = чей-то адрес) не должен попадать на чужой кэш
    return (tuple((round(points[0]["lat"], 5), round(points[0]["lng"], 5))),
            tuple(sorted((round(p["lat"], 5), round(p["lng"], 5)) for p in points[1:])))


def _cache_matrix(key, value):
    if len(_MATRIX_CACHE) >= _MATRIX_CACHE_MAX:  # простая вытесняющая чистка
        oldest = min(_MATRIX_CACHE, key=lambda k: _MATRIX_CACHE[k][0])
        _MATRIX_CACHE.pop(oldest, None)
    _MATRIX_CACHE[key] = (time.time(), *value)


def routing_table(points):
    """Матрица времени/расстояния: ORS -> OSRM (FOSSGIS -> демо) -> offline.

    Результат кэшируется по набору точек (30 мин): повторный расчёт того же
    набора не тратит квоту внешних сервисов и занимает миллисекунды.
    Возвращает (durations|None, distances|None, provider).
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
    """Геометрия маршрута: ORS -> OSRM. None при полном сбое."""
    if len(points) <= ORS_MAX_POINTS:
        geom = ors_geometry(points)
        if geom:
            return geom
    return osrm_geometry(points)


def build_time_matrix(points, settings):
    """Матрица времени в минутах.

    Время дуги = (OSRM-время × коэффициент пробок)
               + (расстояние × задержка на светофорах, с/км)
               + вручение (на дуге прибытия в заказ).
    OSRM отдаёт время свободного потока: без пробок и без остановок на
    регулируемых перекрёстках, поэтому светофоры моделируются отдельной
    надбавкой за километр пути (по умолчанию 15 с/км ≈ светофор каждые
    ~1.2 км и ~18 с ожидания). Возврат в депо учитывается.
    Возвращает (матрица, дороги_использованы, матрица_расстояний_м|None, провайдер).
    """
    handover = max(0, int(settings["handover_min"]))
    traffic = max(1.0, float(settings.get("traffic", 1.3)))
    lights = max(0.0, float(settings.get("lights_sec_per_km", 24))) / 60.0  # мин/км
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
            else:  # запасной вариант: оценка по прямой
                km = haversine_km(points[i], points[j]) * ROAD_FACTOR
                minutes = km / speed * 60
            minutes += km * lights
            t = max(1, int(round(minutes)))
            if j != 0:
                t += handover
            m[i][j] = t
    return m, durations is not None, distances, provider


# Почасовые коэффициенты дорожной нагрузки (Гомель). Утренний пик резкий:
# с ~7:05 (пригородные потоки Новобелицы/Романовичей, школы), самый плотный
# 7:15-8:40; вечерний 17:00-19:00 (центр, мост, вокзал); обеденный мини-пик.
# Источники: местные СМИ (BGmedia, сентябрь 2026), разборы проспекта Ленина.
# Шкала консервативная: пик +35%, межпик -5..-10%.
_HOURLY_TRAFFIC = {0: 0.90, 1: 0.90, 2: 0.90, 3: 0.90, 4: 0.90, 5: 0.90,
                   6: 1.00, 7: 1.20, 8: 1.35, 9: 1.15, 10: 1.00, 11: 1.00,
                   12: 1.10, 13: 1.05, 14: 1.00, 15: 1.00, 16: 1.05,
                   17: 1.20, 18: 1.35, 19: 1.15, 20: 1.00, 21: 0.95,
                   22: 0.95, 23: 0.90}
_LATE_WEIGHT = 25    # штраф за минуту опоздания к дедлайну
_MAX_ROUNDS = 3      # максимальное число «заездов» на курьера


def _deadline_rel_min(hhmm, now_hm):
    """Дедлайн «обещали к HH:MM» в минутах от текущего момента. None, если не задан."""
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", (hhmm or "").strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2))) - now_hm


_APPROACH_RADIUS_KM = 2.5  # ближе к центру — плотная застройка, парковка дольше


def _approach_map(points, home, k_orders, settings):
    """Добавка на парковку/подъезд для заказов (узлы k_orders..) от точки home.

    Возвращает {узел_заказа: минуты}; центру ближе _APPROACH_RADIUS_KM - «центр».
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
    """ETA остановок поездки (минуты от solved_dt) с почасовыми коэффициентами.

    Матрица построена с базовым коэффициентом traffic: дуга очищается от него
    и домножается на коэффициент часа фактического выезда на дугу.
    spd_factor — индивидуальный множитель курьера (замедленная/быстрая езда).
    Возвращает (список ETA остановок, полная длительность поездки).
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
    """Развозка ОДНОГО депо (point_id; None = точка вызывающего).

    include_away=False — сценарий «не ждать»: только курьеры на базе.

    Заказов больше, чем влезает в один заезд (вместимость x курьеры), решается
    несколькими раундами: курьер вернётся на базу и поедет вторым заездом
    (задержка старта = длительность первого заезда + перезагрузка).

    helpers: {courier_id: point_id} — разовая «помощь»: курьер в этом расчёте
    стартует с чужой точки выдачи и берёт максимум один заказ. Его собственная
    точка и статус не меняются.
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
        raise ValueError("Сначала задайте место выдачи заказов (точку на карте)")
    if not orders:
        raise ValueError("Нет готовых заказов, добавьте хотя бы один")
    if not couriers:
        raise ValueError("Нет активных курьеров, добавьте курьера")

    def _eff_home(c):
        """Точка старта курьера в этом расчёте (помощник едет с чужой точки)."""
        hp = helpers.get(c["id"])
        if hp:
            p = next((p for p in STATE["points"] if p["id"] == hp), None)
            if p:
                return p
        return _home_point(c)

    # Узлы матрицы: 0..K-1 - уникальные точки выдачи активных курьеров, дальше заказы.
    # Каждый курьер стартует и финиширует в СВОЕЙ точке (RoutingIndexManager starts).
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

    # Индивидуальная скорость: дорожное время масштабируется на default/замер.
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
        """Когда курьер сможет выехать со своей точки выдачи с новой партией."""
        if c["status"] != "away":
            return 0
        g = _courier_geo(c, _home_point(c))
        if g:  # живая гео точнее ручной оценки
            if g.get("to_point_min") is not None:
                # заказы прежней партии ещё не забраны: доехать + погрузиться
                return min(480, g["to_point_min"] + reload_min)
            return g["back_min"]
        return max(0, int(c.get("back_min", 15)))

    avail = {c["id"]: _start_delay(c) for c in couriers}
    home_of = {c["id"]: home_idx[_eff_home(c)["id"]] for c in couriers}
    # Точка выдачи каждого заказа: везти его могут только курьеры этой точки.
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
        sub = round_homes + remaining        # узлы раунда: дома, потом заказы
        pos_of = {a: i for i, a in enumerate(sub)}
        starts = [pos_of[home_of[c["id"]]] for c in round_couriers]
        manager = pywrapcp.RoutingIndexManager(len(sub), n_veh, starts, starts)
        routing = pywrapcp.RoutingModel(manager)

        def make_cb(delay, hpos, appr, allowed, factor):
            def cb(from_index, to_index):
                i, j = sub[manager.IndexToNode(from_index)], sub[manager.IndexToNode(to_index)]
                arc = matrix[i][j]
                if j != 0 and arc > handover:
                    # дуга прибытия в заказ содержит вручение — его не масштабируем
                    # (arc == 0 — петля непосещённого узла, её не трогаем)
                    arc = int(round((arc - handover) * factor)) + handover
                cost = arc + (delay if i == sub[hpos] else 0)
                if j >= K:
                    cost += appr.get(j, 0)
                    if j not in allowed:      # чужая точка выдачи — везти нельзя
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
                # размерность считает дуги: простой = 1, ровно один заказ = 2
                orders_dim.CumulVar(routing.End(v)).SetRange(2, 2)
            elif round_no == 0 and c["id"] in force_ids:
                # перетащен в план вручную: обязан взять хотя бы один заказ
                orders_dim.CumulVar(routing.End(v)).SetMin(2)
        routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 60, True, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.SetGlobalSpanCostCoefficient(200)

        # Штраф за ожидание доставки: обычный заказ 1 мин, приоритетный 60,
        # просрочка дедлайна 25 (дедлайн сильнее приоритета).
        for ln in range(len(sub)):
            g = sub[ln]
            if g < K:
                continue  # точки выдачи - не остановки
            idx = manager.NodeToIndex(ln)
            if deadline_rel[g] is not None:
                time_dim.SetCumulVarSoftUpperBound(idx, max(0, deadline_rel[g]),
                                                   _LATE_WEIGHT)
            elif eff_prio[g]:
                time_dim.SetCumulVarSoftUpperBound(idx, 0, _PRIO_WEIGHT)
            else:
                time_dim.SetCumulVarSoftUpperBound(idx, 0, 1)

        # Разрешаем оставить заказ на следующий заезд: дроп-визит с подавляющим
        # штрафом. Без этого раунд без полной вместимости был бы неосуществим.
        # (Привязка заказа к точке выдачи уже в колбэке стоимости: чужой заказ
        # стоит миллиард и никогда не попадёт к курьеру другой точки.)
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
                raise RuntimeError("OR-Tools не нашёл решение, попробуйте ещё раз")
            break
        round_stops = set()
        for v in range(n_veh):
            idx, stops = routing.Start(v), []
            while not routing.IsEnd(idx):
                p = manager.IndexToNode(idx)
                if sub[p] >= K:  # пропускаем свою точку выдачи (старт)
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

    # Сначала «отдать сейчас» (на базе), потом «следующим»
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
    """Геометрия маршрутов (для линий на карте), по точке выдачи курьера."""
    for r in plan["routes"]:
        home = r.get("home_point") or STATE["depot"]
        for t in r.get("trips", []):
            seq = [home] + [{"lat": s["lat"], "lng": s["lng"]}
                            for s in t["stops"]] + [home]
            t["geometry"] = routing_geometry(seq) if plan["routing"] == "roads" else None


# ---------- аутентификация по email (пользователи в БД) ----------

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
    """Нормализует и валидирует email; бросает ValueError с текстом для 400."""
    email = (email or "").strip().lower()
    if not re.match(r"^[^@\s]{1,64}@[^@\s]{1,190}$", email):
        raise ValueError("Некорректный email")
    return email


def _check_user_contact(name, phone):
    """Нормализует и валидирует имя/телефон диспетчера; бросает ValueError."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if len(name) < 2:
        raise ValueError("Укажите имя диспетчера (минимум 2 символа)")
    if not re.match(r"^\+?[\d\s()-]{7,20}$", phone):
        raise ValueError("Укажите телефон для связи (например, +375291234567)")
    return name, phone


def _create_user(email, password, is_admin=0, name="", phone=""):
    email = _check_user_email(email)
    if len(password or "") < 4:
        raise ValueError("Пароль: минимум 4 символов")
    name, phone = _check_user_contact(name, phone)
    uid = uuid.uuid4().hex[:8]
    with _db_lock, _db() as c:
        try:
            c.execute("INSERT INTO users(id, email, pwd_hash, is_admin, created_at, name, phone) "
                      "VALUES(?, ?, ?, ?, ?, ?, ?)",
                      (uid, email, _hash_pwd(password), int(bool(is_admin)),
                       _now().isoformat(timespec="seconds"), name, phone))
        except sqlite3.IntegrityError:
            raise ValueError(f"Пользователь {email} уже существует") from None
    return uid


def ensure_default_admin():
    """Первый запуск: создаём администратора из config.ini (по умолчанию admin@local/admin)."""
    with _db_lock, _db() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if not n:
        _create_user(CFG["admin_email"], CFG["admin_password"], is_admin=1,
                     name="Администратор", phone="+375000000000")
        log.info("created default admin %s — смените пароль после входа", CFG["admin_email"])


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


_LOGIN_FAILS = {}  # ip -> [число ошибок, залочено_до_epoch]
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SEC = 60

# ---------- кто из админов онлайн и на какой точке работает ----------
# sid (из cookie-сессии) -> {"uid", "email", "point_id", "last"}.
# «Онлайн» = был любой запрос за ONLINE_WINDOW секунд (long-poll /api/rev
# висит до 25 с, поэтому окно с запасом).
ONLINE: dict = {}
_ONLINE_LOCK = threading.Lock()
ONLINE_WINDOW = 90


def _my_point():
    """Рабочая точка текущей сессии (селектор «Место работы» в шапке).

    Авторитетное значение — session["point"]: переживает простой,
    ONLINE-запись держит лишь живое зеркало для счётчиков «онлайн у точки».
    """
    pt = session.get("point") or ""
    if pt and any(p["id"] == pt for p in STATE.get("points") or []):
        sid = session.get("sid")
        if sid:
            with _ONLINE_LOCK:
                rec = ONLINE.get(sid)
                if rec is not None and rec.get("point_id") != pt:
                    rec["point_id"] = pt  # зеркало догоняет сессию
        return pt
    return (STATE.get("points") or [{}])[0].get("id") or ""


def _plan_for(pid):
    """План депо по id точки."""
    return STATE["plans"].get(pid)


def _courier_plan(c):
    """План депо, к которому приписан курьер."""
    hp = _home_point(c)
    return STATE["plans"].get(hp["id"]) if hp else None


def _touch_online(pt=None):
    """Отметить активность текущей сессии; pt задаёт её рабочую точку.

    Если запись стёрлась (долго висевший long-poll / фоновая вкладка),
    восстанавливаем её из сессии — иначе диспетчер «пропадал» из онлайн-пробок.
    """
    sid = session.get("sid")
    if not sid:
        return
    rec = ONLINE.get(sid)
    if rec is None:
        uid = session.get("uid")
        if not uid:
            return
        with _db_lock, _db() as c:  # чтение — вне ONLINE-блокировки
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




# --- Telegram: бот принимает геолокации курьеров ---------------------------------
TG_POS_TTL = 30 * 60  # локация старше 30 минут считается устаревшей


def _tg_api(method):
    return f"https://api.telegram.org/bot{CFG['tg_bot_token']}/{method}"


# Тест-режим: все диалоги бота (гео-запросы, «доставлен?», привязки) уходят
# одному живому человеку вместо реальных курьеров. Гео-конвейер при этом
# остаётся честным: каждый бот-курьер привязан к своему синтетическому chat_id,
# редирект происходит только в момент отправки сообщений.
TG_TEST_REDIRECT = os.environ.get("TG_TEST_REDIRECT", "").strip()


def _tg_out_chat(chat_id):
    """(адресат, префикс) для исходящего сообщения: в тест-режиме всё одному
    человеку, с пометкой, от какого курьера сообщение."""
    cid = str(chat_id)
    if TG_TEST_REDIRECT and cid != TG_TEST_REDIRECT:
        c = next((x for x in STATE["couriers"]
                  if str(x.get("tg_chat_id") or "") == cid), None)
        pref = f"[{_esc(c['name'])}] " if c else "[тест] "
        return TG_TEST_REDIRECT, pref
    return cid, ""
    return f"https://api.telegram.org/bot{CFG['tg_bot_token']}/{method}"


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plural(n, forms):
    """Русское склонение: _plural(3, ("заказ", "заказа", "заказов")) -> "заказа"."""
    n = abs(n)
    if n % 100 in (11, 12, 13, 14):
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if n % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]


def _tg_send(chat_id, text):
    """Исходящее сообщение курьеру (ошибки не критичны — молча в лог)."""
    chat_id, pref = _tg_out_chat(chat_id)
    try:
        requests.post(_tg_api("sendMessage"),
                      json={"chat_id": chat_id, "text": pref + text, "parse_mode": "HTML"}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg sendMessage: %s", e)


def _tg_send_kb(chat_id, text, buttons):
    """Сообщение с инлайн-кнопками. buttons = [[{text, callback_data}, ...], ...].
    Возвращает message_id или None (не отправилось)."""
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
    """Правка сообщения бота (смена текста/кнопок). Ошибки молча в лог."""
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
    """Ответ на нажатие кнопки (закрывает «часики» у курьера)."""
    try:
        requests.post(_tg_api("answerCallbackQuery"),
                      json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg answerCallbackQuery: %s", e)


_BOT_ASK_AFTER_S = 30   # столько секунд курьер стоит у адреса, прежде чем бот спросит


TG_GEO_FRESH = 600      # гео свежая для расчётов <= 10 мин
TG_GEO_AT_PLACE = 0.10  # ближе 100 м = «на месте» (депо/заказ)
_LOAD_DWELL_S = 120     # столько нужно простоя у точки, чтобы считать выдачу состоявшейся


def _courier_out_orders(c):
    """Заказы «у курьера»: выданы и ещё не закрыты диспетчером."""
    return [o for o in STATE["orders"]
            if (o.get("status") or "ready") == "out"
            and (o.get("assigned") or "") == c.get("id")]


def _courier_has_out(c):
    return bool(_courier_out_orders(c))


def _load_track(c, pos, now=None):
    """Трекер «заказы отдали»: курьер обязан реально постоять у своей точки.

    Ложные срабатывания отсекаются тремя способами: позиция сглажена медианой
    (GPS-прыжок не доезжает до точки), нужен непрерывный простой _LOAD_DWELL_S
    (заезд мимо не считается), и у курьера должны быть выданные заказы.
    """
    chat = c.get("tg_chat_id") or ""
    home = _home_point(c)
    if not chat or not home:
        return
    if not _courier_has_out(c):
        STATE["tg_load"].pop(chat, None)  # партия закрыта — готовимся к следующей
        return
    now = now or time.time()
    rec = STATE["tg_load"].setdefault(chat, {"since": None, "loaded_at": None})
    if rec["loaded_at"]:
        return
    if haversine_km(pos, home) <= TG_GEO_AT_PLACE:
        rec["since"] = rec["since"] or now
        if now - rec["since"] >= _LOAD_DWELL_S:
            rec["loaded_at"] = now
            log.info("load tracked: %s получил заказы у точки «%s»", c.get("name"), home.get("name"))
            _bump()
    else:
        rec["since"] = None  # отошёл, не дождавшись выдачи — отсчёт заново


_DELIVER_DWELL_S = 90  # сколько стоять у адреса, чтобы расчёт счёл заказ доставленным


def _deliver_track(c, pos, now=None):
    """Вывод «курьер отвёз заказ» ПО ГЕО — для расчёта возврата и вопросов бота.

    Заказ считается развезённым в расчёте, когда курьер непрерывно простоял
    _DELIVER_DWELL_S в радиусе TG_GEO_AT_PLACE от адреса. Статус заказа при
    этом НЕ меняется — его по-прежнему закрывает диспетчер вручную.
    Заезд мимо без остановки не считается (счётчик простоя сбрасывается).

    Здесь же бот спрашивает «доставлен?» — раз за заезд: после _BOT_ASK_AFTER_S
    простоя в радиусе, повторный вопрос только после выезда и нового заезда.
    Ответ «да, ещё везу» глушит вопрос до конца текущего заезда.
    """
    chat = c.get("tg_chat_id") or ""
    if not chat:
        return
    out_orders = _courier_out_orders(c)
    st = STATE["tg_deliv"].setdefault(chat, {})
    alive = {o["id"] for o in out_orders}
    for k in list(st):  # закрытые диспетчером записи чистим
        if k not in alive:
            st.pop(k, None)
            STATE["tg_ask"].get(chat, {}).pop(k, None)
    if not out_orders:
        STATE["tg_deliv"].pop(chat, None)
        return
    now = now or time.time()
    for o in out_orders:
        if o.get("lat") is None or o.get("lng") is None:
            continue  # без координат адрес не сверить — поллер крашить нельзя
        rec = st.setdefault(o["id"], {})
        if haversine_km(pos, o) <= TG_GEO_AT_PLACE:
            rec["since"] = rec.get("since") or now
            if (now - rec["since"] >= _BOT_ASK_AFTER_S and not rec.get("asked")
                    and o["id"] not in STATE["tg_ask"].get(chat, {})):
                rec["asked"] = True
                mid = _tg_send_kb(chat, _bot_ask_text(o), _bot_ask_kb(o["id"]))
                if mid is not None or not CFG["tg_poll"]:
                    STATE["tg_ask"].setdefault(chat, {})[o["id"]] = {
                        "msg": mid or 0, "stage": "ask"}
                    log.info("bot ask: %s у «%s» — спросили «доставлен?»",
                             c.get("name"), o.get("address"))
                    _ev("bot", f"спросил {c.get('name')}: «{o.get('address')}» — доставлен?")
            if not rec.get("at") and now - rec["since"] >= _DELIVER_DWELL_S:
                rec["at"] = now
                log.info("deliver tracked: %s был у адреса «%s» — из расчёта возврата",
                         c.get("name"), o.get("address"))
                _ev("sys", f"{c.get('name')} был у адреса «{o.get('address')}»")
                _bump()
        else:
            # выехал из радиуса — заезд закрыт: снимаем простой и закрываем
            # висящий вопрос, чтобы следующий заезд спросил заново
            rec.pop("since", None)
            rec.pop("asked", None)
            pend = STATE["tg_ask"].get(chat, {}).pop(o["id"], None)
            if pend and pend.get("msg"):
                _tg_edit_msg(chat, pend["msg"],
                             "Курьер отъехал от адреса — спрошу при следующем заезде.")


_AWAY_AUTO_KM = 0.5    # дальше этого от своей точки курьер «уехал»
_AWAY_DWELL_S = 60     # непрерывно, столько секунд (глушит GPS-прыжок и «отошёл к машине»)
_BACK_DWELL_S = 120    # простой у точки после закрытия всех заказов — «на базе»


def _auto_status_apply(c, new_status):
    """Перевод статуса курьера по гео: БД, сброс его маршрутов, пинок подписчикам."""
    was = c.get("status")
    c["status"] = new_status
    _persist_couriers()
    _invalidate_plan(courier_id=c["id"], geo=True)
    if was != new_status:
        _ev("sys", f"{c['name']}: " + ("уехал в путь" if new_status == "away"
                                       else "вернулся на базу"))
    else:
        # статус не сменился — это просто гео-тик движения, не событие
        _bump(geo=True)
        return
    _bump()


def _auto_status_track(c, pos, now=None):
    """Авто-статусы по гео (в обе стороны, только с живым гео):

    «база» -> «в пути»: уехал дальше _AWAY_AUTO_KM и держится _AWAY_DWELL_S.
    «в пути» -> «база»: БЫЛ в развозке (выданные заказы закрыты) и простоял
    у своей точки _BACK_DWELL_S. Курьер, который «в пути» стоит у точки и
    ждёт выдачи, назад НЕ переводится — заказов не было, возврат за диспетчером.
    Зона 150 м..500 м — гистерезис: счётчик не тикает и не сбрасывается.
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
                log.info("auto-away: %s уехал от точки «%s» (%.0f м) — статус «в пути»",
                         c.get("name"), home.get("name"), d * 1000)
                _auto_status_apply(c, "away")
        elif d <= TG_GEO_AT_PLACE:
            rec["since"] = None  # у точки — отсчёт заново
            rec["went_out"] = False
        return
    # статус «в пути»
    if d <= TG_GEO_AT_PLACE:
        if not out and rec["went_out"]:
            rec["since"] = rec["since"] or now
            if now - rec["since"] >= _BACK_DWELL_S:
                STATE["tg_away"].pop(chat, None)
                log.info("auto-return: %s вернулся к точке «%s» — статус «на базе»",
                         c.get("name"), home.get("name"))
                _auto_status_apply(c, "base")
        else:
            rec["since"] = None  # ждёт выдачи или ещё развозит — не возврат
    else:
        rec["since"] = None     # снова уехал — счётчик простоя сброшен


def _courier_geo(c, depot, now=None):
    """Гео-данные курьера для расчётов: сглаженная позиция + оценка возврата на депо.

    Возвращает None, если привязки нет или гео старше TG_GEO_FRESH.
    back_min - за сколько курьер физически доедет до депо (анти-прыжки уже
    применены медианой при приёме точки, здесь только расстояние).
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
    # фаза развозки: заказы выданы? загрузка у точки зафиксирована?
    out_orders = _courier_out_orders(c)
    has_out = bool(out_orders)
    load = STATE["tg_load"].get(chat) or {}
    g["has_out"] = has_out
    g["loaded"] = bool(load.get("loaded_at"))
    if has_out and not g["at_depot"]:
        g["delivering"] = True   # выданы и не у точки — значит, едет с заказами
        # честный возврат: дорога до точки + развоз невыданных-недоставленных.
        # «доставленные» выводим по гео (долго стоял у адреса) — для расчёта
        # их считаем развезёнными; статус заказа не трогаем
        dst = STATE["tg_deliv"].get(chat) or {}
        rem = sum(1 for o in out_orders
                  if not dst.get(o["id"], {}).get("at"))
        per = _courier_del_avg_min(c)
        g["back_min"] = int(min(480, g["back_min"] + rem * per))
    if not has_out and not g["at_depot"]:
        # заказы ещё не в машине: честный ETA — сначала доехать до точки
        kmh2, _ = _courier_speed(c)
        g["to_point_min"] = int(min(240, max(1, round(
            km * ROAD_FACTOR / kmh2 * 60))))
    # стоит ли курьер прямо сейчас у одного из своих выданных заказов
    best, best_km = None, None
    for o in out_orders:
        d = haversine_km(pos, o)
        if d <= TG_GEO_AT_PLACE and (best_km is None or d < best_km):
            best, best_km = o["address"], d
    if best:
        g["at_order"] = best
    return g


def _flip_return_route(courier_id):
    """Все заказы развозки закрыты: разворачиваем трассу — курьер едет домой
    по улицам, карта рисует возврат (ret_geom вместо out_geom)."""
    c = next((x for x in STATE["couriers"] if x["id"] == courier_id), None)
    if c and c.get("out_geom") and not any(
            o.get("assigned") == courier_id and o.get("status") == "out"
            for o in STATE["orders"]):
        c["ret_geom"] = list(reversed(c["out_geom"]))
        c.pop("out_geom", None)


def _bot_close_delivered(oid, outcome="delivered", reason=""):
    """Закрыть заказ по подтверждению КУРЬЕРА (без сессии): доставлен или
    отменён (с причиной из диалога бота).

    Тот же след, что у ручного закрытия диспетчером: архив, снятие из
    развозки, разворот трассы на возврат, инвалидация плана.
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
    log.info("bot confirm: заказ %s (%s) закрыт курьером %s (%s)",
             oid, order.get("address"), c.get("name") if c else cid, outcome)
    who = c.get("name") if c else cid
    _ev("bot", (f"отменил «{order.get('address')}» — {who}, причина: {reason}"
                if outcome == "cancelled" else
                f"закрыл «{order.get('address')}» — {who} подтвердил"))
    return True, who


# Причины отмены — по частоте (чаще всего в начале, «Другое» всегда последним)
_CANCEL_REASONS = [
    "Долгое ожидание",
    "Человек не отвечает",
    "Просто отказ",
    "Неправильный заказ",
    "Плохое качество товара",
    "Не тот адрес",
    "Другое",
]


def _bot_ask_text(o):
    return (f"🛍 Кажется, заказ по адресу <b>{_esc(o.get('address') or '')}</b> "
            "доставлен. Это так?")


def _bot_ask_kb(oid):
    return [[{"text": "✅ Доставил", "callback_data": f"dlv:{oid}:y"}],
            [{"text": "❌ Нет", "callback_data": f"dlv:{oid}:n"}],
            [{"text": "🚫 Заказ отменён", "callback_data": f"dlv:{oid}:ref"}]]


def _tg_step_kb(oid, label, act):
    """Клавиатура шага-подтверждения: «<label>» и назад к вопросу."""
    return [[{"text": label, "callback_data": f"dlv:{oid}:{act}"}],
            [{"text": "↩️ Назад", "callback_data": f"dlv:{oid}:no"}]]


def _bot_keep_rolling(chat, pend, oid, order, courier):
    """«Нет, ещё везу»: диалог закрыт, заказ остаётся в развозке."""
    addr = _esc(order.get("address") or "")
    STATE["tg_ask"].get(chat, {}).pop(oid, None)
    _tg_edit_msg(chat, pend["msg"],
                 f"Понял: <b>{addr}</b> ещё в развозке. "
                 "Закроет диспетчер или спрошу при следующем заезде.")
    _ev("cour", f"{courier['name']}: «{order.get('address') or oid}» ещё в развозке")


def _tg_callback(cb):
    """Нажатие инлайн-кнопки курьером: «доставил?» → «точно?» → закрытие.

    Стадии: ask → confirm/refconfirm/noconfirm → ok/r:i/nok; «Назад»
    возвращает на шаг назад. Неизвестная кнопка или нажатие вне своей
    стадии диалог не ломает и данные не меняет.
    """
    data = cb.get("data") or ""
    cbid = cb.get("id") or ""
    msg = cb.get("message") or {}
    chat = str((msg.get("chat") or {}).get("id") or "")
    if not data.startswith("dlv:"):
        _tg_answer_cb(cbid)
        return
    parts = data.split(":", 2)
    if len(parts) < 3 or not parts[1]:
        _tg_answer_cb(cbid, "Кнопка не распознана")
        return
    _, oid, act = parts
    # тест-режим: кнопки жмёт живой человек в редирект-чате — возвращаем
    # диалог к синтетическому чату курьера, которому выдан заказ
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
                         "Этот вопрос уже неактуален — заказ закрыт диспетчером.")
            STATE["tg_ask"].get(chat, {}).pop(oid, None)
        _tg_answer_cb(cbid, "Уже неактуально")
        return
    addr = _esc(order.get("address") or "")
    if act == "y" and pend["stage"] == "ask":
        pend["stage"] = "confirm"
        _tg_edit_msg(chat, pend["msg"], f"Точно доставлен? Заказ: <b>{addr}</b>",
                     _tg_step_kb(oid, "✅ Подтвердить", "ok"))
        _tg_answer_cb(cbid)
    elif act == "ref" and pend["stage"] == "ask":
        pend["stage"] = "refconfirm"
        _tg_edit_msg(chat, pend["msg"], f"Точно отменяем? Заказ: <b>{addr}</b>",
                     _tg_step_kb(oid, "✅ Да, отменяем", "refyes"))
        _tg_answer_cb(cbid)
    elif act == "n" and pend["stage"] == "ask":
        pend["stage"] = "noconfirm"
        _tg_edit_msg(chat, pend["msg"], f"Точно ещё нет? Заказ: <b>{addr}</b>",
                     _tg_step_kb(oid, "✅ Да, ещё везу", "nok"))
        _tg_answer_cb(cbid)
    elif act == "nok" and pend["stage"] == "noconfirm":
        _bot_keep_rolling(chat, pend, oid, order, courier)
        _tg_answer_cb(cbid)
    elif act == "refyes" and pend["stage"] == "refconfirm":
        pend["stage"] = "reason"
        _tg_edit_msg(chat, pend["msg"],
                     f"Причина отмены: <b>{addr}</b>",
                     [[{"text": t, "callback_data": f"dlv:{oid}:r:{i}"}]
                      for i, t in enumerate(_CANCEL_REASONS)]
                     + [[{"text": "↩️ Назад", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    elif act.startswith("r:") and pend["stage"] == "reason":
        try:
            reason = _CANCEL_REASONS[int(act[2:])]
        except (IndexError, ValueError):
            reason = "Другое"
        ok, _ = _bot_close_delivered(oid, outcome="cancelled", reason=reason)
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        if ok:
            _tg_edit_msg(chat, pend["msg"],
                         f"🗑 Записано: <b>{addr}</b> — заказ отменён.\n"
                         f"Причина: <b>{_esc(reason)}</b>")
            _tg_answer_cb(cbid, "Заказ отменён ✓")
        else:
            _tg_edit_msg(chat, pend["msg"], "Не получилось закрыть — уже неактуален.")
            _tg_answer_cb(cbid, "Уже неактуально")
    elif act == "ok" and pend["stage"] == "confirm":
        ok, _ = _bot_close_delivered(oid)
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        if ok:
            _tg_edit_msg(chat, pend["msg"],
                         f"✅ Записано: <b>{addr}</b> доставлен. Спасибо!")
            _tg_answer_cb(cbid, "Заказ закрыт ✓")
        else:
            _tg_edit_msg(chat, pend["msg"], "Не получилось закрыть — уже неактуален.")
            _tg_answer_cb(cbid, "Уже неактуально")
    elif act == "no":
        # «Назад»: на шаг диалога назад, диалог не закрываем
        if pend["stage"] in ("confirm", "refconfirm", "noconfirm"):
            pend["stage"] = "ask"
            _tg_edit_msg(chat, pend["msg"], _bot_ask_text(order), _bot_ask_kb(oid))
        elif pend["stage"] == "reason":
            pend["stage"] = "refconfirm"
            _tg_edit_msg(chat, pend["msg"], f"Точно отменяем? Заказ: <b>{addr}</b>",
                         _tg_step_kb(oid, "✅ Да, отменяем", "refyes"))
        _tg_answer_cb(cbid)
    else:
        # неизвестная кнопка или нажатие вне своей стадии (старый экран) —
        # диалог не трогаем, данные не меняем
        log.warning("tg cb: неизвестный act=%s stage=%s oid=%s chat=%s",
                    act, pend.get("stage"), oid, chat)
        _tg_answer_cb(cbid, "Кнопка не распознана")


def _tg_handle_update(u):
    """Один апдейт от Telegram: текст (/start), геолокация или кнопка."""
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
    if len(STATE["tg_seen"]) > 50:  # храним только недавних
        for k in sorted(STATE["tg_seen"], key=lambda x: STATE["tg_seen"][x]["ts"])[:-50]:
            STATE["tg_seen"].pop(k, None)
            STATE["tg_nagged"].pop(k, None)  # антиспам-память чистим вместе

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
            # анти-дребезг: буфер последних точек, сглаживание медианой
            hist = STATE["tg_pos"].get(chat_id, {}).get("hist", [])
            hist = [h for h in hist if raw["ts"] - h["ts"] <= 600][-4:]
            hist.append(raw)
            recent = [h for h in hist if raw["ts"] - h["ts"] <= 600][-3:]
            lats = sorted(h["lat"] for h in recent)
            lngs = sorted(h["lng"] for h in recent)
            smoothed = {"lat": lats[len(lats) // 2], "lng": lngs[len(lngs) // 2],
                        "ts": raw["ts"], "acc": raw["acc"]}
            # замер скорости — по сглаженному треку: одиночный GPS-прыжок
            # гасится медианой и в отрезок не попадает
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
            # диалоги «доставлен?» и трекеры простоя — в БД не реже раза в 15 с
            # (этот же поток обрабатывает нажатия кнопок — гонок нет)
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
            _bump(geo=True)  # движение курьера — карта обновится (хаб батчит ≥1 с)
        else:
            # live-локация шлёт правки каждые несколько секунд — «не привязан»
            # отправляем не чаще раза в 30 минут на чат
            now_ts = time.time()
            if now_ts - STATE["tg_nagged"].get(chat_id, 0) > 1800:
                STATE["tg_nagged"][chat_id] = now_ts
                _tg_send(chat_id,
                          f"Похоже, вас ещё не привязали к курьеру. Отправьте этот ID "
                          f"администратору: <code>{chat_id}</code>")
    elif (msg.get("text") or "").strip().startswith("/start"):
        _tg_send(chat_id,
                 "Привет! Это бот развозки.\n\n"
                 "Нужна <b>живая геолокация</b>:\n"
                 "скрепка → «Геолокация» → «Поделиться моей геолокацией» → "
                 "время <b>«Пока не отключу»</b>.\n\n"
                 "Тогда диспетчер видит вас на карте всю смену.\n\n"
                 f"Ваш ID: <code>{chat_id}</code>\n"
                 "Скажите его администратору, и вас подключат к курьеру.")


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
                # 409 Conflict: бота уже слушает другой процесс. Не боремся за
                # getUpdates в лоб — ждём: второй инстанс умрёт и канал вернётся.
                if r.status_code == 409:
                    log.warning("tg poll: бот уже слушается другим процессом "
                                "(409) — повтор через 5 мин")
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
        except Exception as e:  # неожиданный формат — не роняем поллер
            log.warning("tg update parse: %s", e)
            time.sleep(2)


def _tg_start_polling():
    """Запуск поллера при старте, если задан токен бота."""
    if not CFG["tg_bot_token"]:
        return
    try:
        me = requests.get(_tg_api("getMe"), timeout=10).json().get("result") or {}
        STATE["tg_bot"] = "@" + me.get("username", "")
        log.info("tg bot: %s", STATE["tg_bot"])
    except requests.RequestException as e:
        log.warning("tg getMe failed: %s", e)
    if not CFG["tg_poll"]:
        log.info("tg bot: поллер выключен (tg_poll=0) — геолокации слушает "
                 "другой сервер")
        return
    threading.Thread(target=_tg_poll_loop, daemon=True).start()


def _ev(actor, text):
    """Лента активности в UI: bot=бот, disp=диспетчер, cour=курьер, sys=система."""
    STATE["events"].append({"t": int(time.time()), "actor": actor,
                            "text": str(text)[:200]})
    del STATE["events"][:-60]  # храним только свежие
    _bump()


def _payload(me=None, myp=None):
    """Ответ после мутации: состояние + квота ORS + счётчики дня + текущий пользователь.

    me/myp задаются явно при WS-бродкасте (там нет сессии запроса):
    payload конкретного депо для всех его подписчиков.
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
        # активная развозка: выданные заказы в порядке выдачи (маршрут мог
        # уже уйти из плана — карта рисует его пунктиром по этим данным)
        outs = sorted((o for o in STATE["orders"]
                       if o.get("assigned") == c["id"]
                       and (o.get("status") or "ready") == "out"),
                      key=lambda o: (o.get("out_no", float("inf")),
                                     o.get("out_at") or "", o["id"]))
        if outs:
            cc["out_route"] = {
                # [lat, lng, order_id]: id нужен фронту, чтобы красить точки
                # доставки выбранного курьера в его цвет
                "stops": [[o["lat"], o["lng"], o["id"]] for o in outs],
                "home": {"lat": home["lat"], "lng": home["lng"]} if home else None,
            }
            if c.get("out_geom"):
                cc["out_route"]["geom"] = c["out_geom"]
        elif c.get("status") == "away" and c.get("ret_geom"):
            # возврат на базу: обратная трасса без точек доставки
            cc["out_route"] = {
                "stops": [],
                "home": {"lat": home["lat"], "lng": home["lng"]} if home else None,
                "geom": c["ret_geom"],
            }
        couriers.append(cc)
    # курьеры видны всем депо, но свои — первыми (стабильно по исходному порядку)
    couriers.sort(key=lambda cc: 0 if _obj_point(cc) == myp else 1)
    st = {k: v for k, v in STATE.items()
          if k not in ("tg_seen", "tg_pos", "tg_offset", "tg_nagged", "tg_load",
                       "tg_deliv", "tg_away", "plans", "advice_modes", "solving")}
    seen = sorted(STATE["tg_seen"].values(), key=lambda x: -x["ts"])[:20]
    # живые счётчики по точкам: курьеры + админы онлайн
    now2 = time.time()
    with _ONLINE_LOCK:
        for sid in [s for s, r in ONLINE.items() if now2 - r["last"] > ONLINE_WINDOW * 4]:
            ONLINE.pop(sid, None)  # подчистили давно ушедших
        live = [r for r in ONLINE.values() if now2 - r["last"] < ONLINE_WINDOW]
    st["points"] = [dict(p,
                         couriers=sum(1 for c in STATE["couriers"]
                                      if _obj_point(c) == p["id"]),
                         admins=list(dict.fromkeys(  # один человек в нескольких
                             a["email"] for a in live  # сессиях = одна запись
                             if a["point_id"] == p["id"])))
                    for p in st.get("points") or []]
    # скоуп депо: свои заказы, свой план и своя история; чужие — только курьеры
    st["orders"] = [o for o in st.get("orders") or [] if _obj_point(o) == myp]
    st["plan"] = _plan_for(myp)
    # идёт ли сейчас расчёт развозки в ЭТОМ депо (блокирует UI всех его
    # диспетчеров; словарь pid->bool наружу не отдаём)
    st["solving"] = bool(STATE.get("solving", {}).get(myp))
    return jsonify({**st, "couriers": couriers,
                    "tg": {"bot": STATE["tg_bot"], "seen": seen},
                    "ors": ors_status(), "today": _history_today(point_id=myp),
                    "cfg": {"tg": bool(CFG["tg_bot_token"])},
                    "me": me, "my_point": myp,
                    "users": _admin_users() if me and me["is_admin"] else []})


# ---------- live-рассылка: WS-хаб (socket.io) вместо long-poll /api/rev ----------

def _bump(geo: bool = False):
    """Пометить состояние изменённым и разбудить подписчиков WS-хаба.

    Вызывается из любого потока (HTTP-обработчики, TG-поллер); хаб
    коалесит частые бампы. geo=True — обновление только отслеживания
    (движение курьера): хаб шлёт такое не чаще раза в секунду; любое
    событие (выдача, статус, заказ) доставляется сразу.
    """
    STATE["rev"] = STATE.get("rev", 0) + 1
    from . import ws  # поздний импорт: ws импортирует core — рвём цикл
    ws.notify_changed(geo=geo)

def _points_ids():
    """Все id точек выдачи (румы WS-хаба)."""
    return [p["id"] for p in STATE.get("points") or []]


# ---------- инвалидация плана (используется и HTTP-ручками, и TG-ботом) ----------
def _invalidate_plan(drop_plan=False, pid=None, courier_id=None, geo=False):
    """╨ƒ╨╗╨░╨╜ ╨╜╨╡ ╨┐╨╡╤Ç╨╡╤ü╤ç╨╕╤é╤ï╨▓╨░╨╡╨╝ ╨▓ ╤ä╨╛╨╜╨╡ ΓÇö ╤é╨╛╨╗╤î╨║╨╛ ╨┐╨╛╨╝╨╡╤ç╨░╨╡╨╝/╤ü╨▒╤Ç╨░╤ü╤ï╨▓╨░╨╡╨╝.

    pid — депо, чей план инвалидируем (None = все депо: правка точек/настроек).
    drop_plan=True — старый план точно невалиден (удаление заказа/курьера,
    смена депо): сбрасываем сразу. Иначе план показывается с пометкой
    «устарел», пока администратор не нажмёт «Рассчитать».
    courier_id — курьер-специфичная инвалидация (смена статуса, удаление,
    возврат на базу, перевод в другое депо): из всех планов убираются только
    маршруты этого курьера, маршруты остальных курьеров сохраняются
    с пометкой «устарел» — диспетчер может выдать их без пересчёта.
    """
    if courier_id is not None:
        changed = False
        for key, plan in list(STATE["plans"].items()):
            if plan is None:
                continue
            routes = plan.get("routes") or []
            kept = [r for r in routes if r.get("courier_id") != courier_id]
            if len(kept) == len(routes):
                continue  # этого курьера в плане нет — чужие маршруты не трогаем
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
        # реальная правка планов — событие (доставляем сразу); плановое
        # сопровождение гео-тика — движение, хаб батчит его до 1/с
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
    _bump()  # ╤ü╨╛╤ü╤é╨╛╤Å╨╜╨╕╨╡ ╨╕╨╖╨╝╨╡╨╜╨╕╨╗╨╛╤ü╤î ΓÇö ╨║╨╛╨╜╤ü╨╛╨╗╨╕ ╨╛╨▒╨╜╨╛╨▓╤Å╤é╤ü╤Å ╤ü╨░╨╝╨╕




def _valid_latlng(lat, lng):
    return (-90 <= lat <= 90) and (-180 <= lng <= 180) and (lat != 0 or lng != 0)

