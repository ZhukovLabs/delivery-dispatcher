"""Диспетчер доставки: админ-интерфейс + оптимизация развозки (OR-Tools)."""
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

# Минск: UTC+3, без перехода на летнее время. Все «настенные» времена
# (часы плана, история, дедлайны, сообщения) пишем по Минску независимо
# от часового пояса сервера. Значения остаются наивными (без оффсета),
# чтобы не ломать сравнения с уже записанными данными.
_MN = timezone(timedelta(hours=3))


def _now() -> datetime:
    return datetime.now(_MN).replace(tzinfo=None)
from logging.handlers import RotatingFileHandler

import requests
from flask import (Flask, after_this_request, jsonify,
                   request, send_file, session)
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

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
    "color_seq": 0,      # монотонный счётчик: цвета не перемешиваются при удалениях
    # Telegram: кто писал боту (для привязки), последние локации курьеров, курсор getUpdates
    "tg_seen": {},       # chat_id -> {"chat_id", "login", "ts"}
    "tg_pos": {},        # chat_id -> {"lat", "lng", "ts", "live"}
    "tg_nagged": {},     # chat_id -> ts последнего «не привязан» (антиспам live-правок)
    "tg_load": {},       # chat_id -> {"since", "loaded_at"} — трекер выдачи заказов
    "tg_deliv": {},      # chat_id -> {order_id: {"since", "at"}} — вывод «доставлен»
                         # ТОЛЬКО для расчёта возврата; статус заказа не меняет
    "tg_away": {},       # chat_id -> {"since"} — авто-«в пути» при отъезде от точки
    "tg_offset": 0,
    "tg_bot": "",        # @username бота (для подсказок в интерфейсе)
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
             if STATE.get("plans") else "")])
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


def _archive_order(order, outcome, courier="", courier_id=""):
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
        c.execute("INSERT OR REPLACE INTO history VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (order["id"], order["address"], order["lat"], order["lng"],
                   order.get("created_at"),
                   closed, outcome, courier,
                   order.get("deadline") or "", _obj_point(order)))


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
                    "cycle_min": cycle_min, "deadline": r["deadline"] or ""})
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
        if m < _SPEED_SEG_MIN_M:
            continue  # дрожь — не движение, но и не выброс
        kmh = m / 1000.0 / (dt / 3600.0)
        if kmh > 90.0:
            continue  # GPS-прыжок
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
        # миграция: единственное депо -> первая точка выдачи
        base = STATE.get("depot") or dict(DEFAULT_DEPOT)
        STATE["points"] = [{"id": uuid.uuid4().hex[:8], "name": "Основная",
                            "address": base.get("address") or "",
                            "lat": float(base.get("lat") or 0),
                            "lng": float(base.get("lng") or 0)}]
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


app.secret_key = _session_secret()
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
    """Отметить активность текущей сессии; pt задаёт её рабочую точку."""
    sid = session.get("sid")
    if not sid:
        return
    with _ONLINE_LOCK:
        rec = ONLINE.get(sid)
        if rec is None:
            return
        rec["last"] = time.time()
        if pt is not None:
            rec["point_id"] = pt


def _drop_online():
    sid = session.get("sid")
    if sid:
        with _ONLINE_LOCK:
            ONLINE.pop(sid, None)


@app.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pwd = data.get("password") or ""
    ip = request.remote_addr or "?"
    now = time.time()
    fails = _LOGIN_FAILS.get(ip)
    if len(_LOGIN_FAILS) > 1000:  # защита от роста в долгоживущем процессе
        _LOGIN_FAILS.clear()
    if fails and fails[1] > now:
        wait = int(fails[1] - now) + 1
        log.warning("login locked: %s (%ds left)", ip, wait)
        return jsonify(error=f"Слишком много попыток входа. Подождите {wait} с"), 429
    with _db_lock, _db() as c:
        r = c.execute("SELECT id, pwd_hash FROM users WHERE email = ?", (email,)).fetchone()
    if r and _verify_pwd(pwd, r["pwd_hash"]):
        session["uid"] = r["id"]
        session["sid"] = uuid.uuid4().hex  # метка онлайн-присутствия
        session.permanent = True  # сессия живёт 12 ч, а не до закрытия браузера
        with _ONLINE_LOCK:
            ONLINE[session["sid"]] = {"uid": r["id"], "email": email,
                                      "point_id": (STATE.get("points") or [{}])[0].get("id"),
                                      "last": time.time()}
        _LOGIN_FAILS.pop(ip, None)
        log.info("login ok: %s", email)
        return jsonify(ok=True)
    time.sleep(0.3)  # тормозим перебор паролей
    n = (fails[0] + 1) if fails else 1
    _LOGIN_FAILS[ip] = [n, now + _LOGIN_LOCK_SEC] if n >= _LOGIN_MAX_FAILS else [n, 0]
    log.warning("login failed: %s (attempt %d from %s)", email or "?", n, ip)
    return jsonify(error="Неверный email или пароль"), 401


@app.post("/api/login")
def login_api():
    """Алиас для SPA (Next.js проксирует /api/* сюда)."""
    return login()


@app.get("/api/logout")
def logout_api():
    _drop_online()
    session.clear()
    return jsonify(ok=True)


@app.post("/api/workpoint")
def api_workpoint():
    """Рабочая точка выдачи текущего администратора (селектор в шапке)."""
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    data = request.get_json(silent=True) or {}
    pid = str(data.get("point_id") or "")
    if pid not in {p["id"] for p in STATE.get("points") or []}:
        return jsonify({"error": "Неизвестная точка выдачи"}), 400
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    session["point"] = pid  # авторитетное значение — переживёт простой сессии
    with _ONLINE_LOCK:
        rec = ONLINE.get(session["sid"])
        if rec is None:
            ONLINE[session["sid"]] = {"uid": me["id"], "email": me["email"],
                                      "point_id": pid, "last": time.time()}
        else:
            rec["point_id"] = pid
    _touch_online(pid)
    _bump()  # другие админы увидят обновлённые счётчики на карточках точек
    return jsonify(ok=True)


@app.get("/api/rev")
def api_rev():
    """Long-poll версии состояния: висит до изменения (или 25 с), отдаёт rev.

    Надёжнее SSE за буферизующими прокси: обычный запрос-ответ, потоков нет.
    """
    if not _me():
        return jsonify({"error": "Требуется вход"}), 401
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    deadline = time.time() + 25
    while time.time() < deadline:
        with _REV_LOCK:
            _REV_LOCK.wait(timeout=max(0.2, deadline - time.time()))
        if STATE.get("rev", 0) > since:
            break
    return jsonify({"rev": STATE.get("rev", 0)})


# ---------- live-рассылка: сервер сообщает клиентам, что состояние изменилось ----
# Каждое изменение состояния поднимает счётчик rev и будит все ожидающие
# long-poll /api/rev; клиенты (несколько админов) получают новую версию и сами
# перезаказывают /api/state. Мутации по-прежнему идут обычными POST/PATCH.

_REV_LOCK = threading.Condition()


def _bump():
    """Пометить состояние изменённым и разбудить ожидающие long-poll."""
    STATE["rev"] = STATE.get("rev", 0) + 1
    with _REV_LOCK:
        _REV_LOCK.notify_all()


@app.before_request
def _guard():
    if request.path in ("/login", "/api/login", "/health", "/"):
        return None
    if _me():
        _touch_online()  # любое действие админа продлевает его «онлайн»
        return None
    return jsonify({"error": "Требуется вход"}), 401


# ---------- управление пользователями (только админ) ----------

@app.post("/api/users")
def api_add_user():
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может добавлять пользователей"}), 403
    data = _json()
    try:
        _create_user(data.get("email") or "", data.get("password") or "",
                     is_admin=data.get("is_admin"),
                     name=data.get("name") or "", phone=data.get("phone") or "")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    log.info("user added by %s: %s", me["email"], data.get("email"))
    _bump()
    return _payload()


@app.delete("/api/users/<uid>")
def api_del_user(uid):
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может удалять пользователей"}), 403
    if me["id"] == uid:
        return jsonify({"error": "Нельзя удалить собственную учётную запись"}), 400
    with _db_lock, _db() as c:
        admins = c.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1").fetchone()["n"]
        victim = c.execute("SELECT email, is_admin FROM users WHERE id = ?", (uid,)).fetchone()
        if not victim:
            return jsonify({"error": "Пользователь не найден"}), 404
        if victim["is_admin"] and admins <= 1:
            return jsonify({"error": "Нельзя удалить последнего администратора"}), 400
        c.execute("DELETE FROM users WHERE id = ?", (uid,))
    log.info("user removed by %s: %s", me["email"], victim["email"])
    _bump()
    return _payload()


@app.put("/api/users/<uid>")
def api_upd_user(uid):
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может изменять пользователей"}), 403
    data = _json()
    try:
        email = _check_user_email(data.get("email"))
        name, phone = _check_user_contact(data.get("name"), data.get("phone"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    is_admin = int(bool(data.get("is_admin")))
    with _db_lock, _db() as c:
        victim = c.execute("SELECT id, email, is_admin FROM users WHERE id = ?", (uid,)).fetchone()
        if not victim:
            return jsonify({"error": "Пользователь не найден"}), 404
        admins = c.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1").fetchone()["n"]
        if victim["is_admin"] and not is_admin and admins <= 1:
            return jsonify({"error": "Нельзя снять права с последнего администратора"}), 400
        try:
            c.execute("UPDATE users SET email = ?, name = ?, phone = ?, is_admin = ? WHERE id = ?",
                      (email, name, phone, is_admin, uid))
        except sqlite3.IntegrityError:
            return jsonify({"error": f"Пользователь {email} уже существует"}), 400
    log.info("user updated by %s: %s", me["email"], victim["email"])
    _bump()
    return _payload()


@app.put("/api/users/<uid>/password")
def api_reset_user_pwd(uid):
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может сбрасывать пароли"}), 403
    new = _json().get("new") or ""
    if len(new) < 4:
        return jsonify({"error": "Пароль: минимум 4 символа"}), 400
    with _db_lock, _db() as c:
        victim = c.execute("SELECT email FROM users WHERE id = ?", (uid,)).fetchone()
        if not victim:
            return jsonify({"error": "Пользователь не найден"}), 404
        c.execute("UPDATE users SET pwd_hash = ? WHERE id = ?", (_hash_pwd(new), uid))
    log.info("user password reset by %s: %s", me["email"], victim["email"])
    return jsonify({"ok": True})


@app.post("/api/password")
def api_change_password():
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    data = _json()
    old, new = data.get("old") or "", data.get("new") or ""
    if len(new) < 4:
        return jsonify({"error": "Новый пароль: минимум 4 символа"}), 400
    with _db_lock, _db() as c:
        row = c.execute("SELECT pwd_hash FROM users WHERE id = ?", (me["id"],)).fetchone()
        if not row or not _verify_pwd(old, row["pwd_hash"]):
            return jsonify({"error": "Старый пароль неверен"}), 400
        c.execute("UPDATE users SET pwd_hash = ? WHERE id = ?",
                  (_hash_pwd(new), me["id"]))
    log.info("password changed: %s", me["email"])
    return _payload()


# ---------- служебные ----------

@app.get("/health")
def health():
    return jsonify({"status": "ok", "time": _now().isoformat(timespec="seconds")})


@app.errorhandler(Exception)
def _unhandled(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e  # 404/405 и прочие — как есть, без заворачивания в 500
    log.exception("unhandled error on %s %s", request.method, request.path)
    return jsonify({"error": f"Внутренняя ошибка: {e}"}), 500


# ---------- api ----------

@app.get("/")
def root():
    return jsonify(service="dispatcher-api", time=_now().isoformat())

def _json():
    """Тело запроса как dict. Битый/пустой JSON => {} (валидацию делают ручки)."""
    return request.get_json(silent=True) or {}


# --- Telegram: бот принимает геолокации курьеров ---------------------------------
TG_POS_TTL = 30 * 60  # локация старше 30 минут считается устаревшей


def _tg_api(method):
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
    try:
        requests.post(_tg_api("sendMessage"),
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg sendMessage: %s", e)


TG_GEO_FRESH = 600      # гео свежая для расчётов <= 10 мин
TG_GEO_AT_PLACE = 0.15  # ближе 150 м = «на месте» (депо/заказ)
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
    """Вывод «курьер отвёз заказ» ПО ГЕО — только для расчёта возврата.

    Заказ считается развезённым в расчёте, когда курьер непрерывно простоял
    _DELIVER_DWELL_S в радиусе TG_GEO_AT_PLACE от адреса. Статус заказа при
    этом НЕ меняется — его по-прежнему закрывает диспетчер вручную.
    Заезд мимо без остановки не считается (счётчик простоя сбрасывается).
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
    if not out_orders:
        STATE["tg_deliv"].pop(chat, None)
        return
    now = now or time.time()
    for o in out_orders:
        if o.get("lat") is None or o.get("lng") is None:
            continue  # без координат адрес не сверить — поллер крашить нельзя
        rec = st.setdefault(o["id"], {})
        if rec.get("at"):
            continue
        if haversine_km(pos, o) <= TG_GEO_AT_PLACE:
            rec["since"] = rec.get("since") or now
            if now - rec["since"] >= _DELIVER_DWELL_S:
                rec["at"] = now
                log.info("deliver tracked: %s был у адреса «%s» — из расчёта возврата",
                         c.get("name"), o.get("address"))
                _bump()
        else:
            rec.pop("since", None)  # проехал мимо — не считается


_AWAY_AUTO_KM = 0.5    # дальше этого от своей точки курьер «уехал»
_AWAY_DWELL_S = 60     # непрерывно, столько секунд (глушит GPS-прыжок и «отошёл к машине»)
_BACK_DWELL_S = 120    # простой у точки после закрытия всех заказов — «на базе»


def _auto_status_apply(c, new_status):
    """Перевод статуса курьера по гео: БД, сброс плана, пинок подписчикам."""
    c["status"] = new_status
    _persist_couriers()
    _invalidate_plan(drop_plan=True)
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


def _tg_handle_update(u):
    """Один апдейт от Telegram: текст (/start) или геолокация (в т.ч. live)."""
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
            _bump()  # курьер двигается — карта обновится у всех
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
                        "allowed_updates": json.dumps(["message", "edited_message"])},
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


def _payload():
    """Ответ после мутации: состояние + квота ORS + счётчики дня + текущий пользователь."""
    me = _me()
    now = time.time()
    myp = _my_point() if me else ""
    couriers = []
    for c in STATE["couriers"]:
        cc = dict(c)
        pos = STATE["tg_pos"].get(c.get("tg_chat_id") or "")
        if pos and now - pos["ts"] < TG_POS_TTL:
            cc["pos"] = {k: v for k, v in pos.items() if k != "hist"}
        cur = _speed_current_kmh(pos, now)
        if cur is not None:
            cc["cur_kmh"] = cur
        avg_kmh, avg_src = _courier_speed(c)
        cc["avg_kmh"] = round(avg_kmh, 1)
        cc["speed_src"] = avg_src
        geo = _courier_geo(c, _home_point(c), now)
        if geo:
            cc["geo"] = geo
        couriers.append(cc)
    # курьеры видны всем депо, но свои — первыми (стабильно по исходному порядку)
    couriers.sort(key=lambda cc: 0 if _obj_point(cc) == myp else 1)
    st = {k: v for k, v in STATE.items()
          if k not in ("tg_seen", "tg_pos", "tg_offset", "tg_nagged", "tg_load",
                       "tg_deliv", "tg_away", "plans", "advice_modes")}
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
    return jsonify({**st, "couriers": couriers,
                    "tg": {"bot": STATE["tg_bot"], "seen": seen},
                    "ors": ors_status(), "today": _history_today(point_id=myp),
                    "cfg": {"tg": bool(CFG["tg_bot_token"])},
                    "me": me, "my_point": myp,
                    "users": _admin_users() if me and me["is_admin"] else []})


@app.get("/api/state")
def get_state():
    return _payload()


def _valid_latlng(lat, lng):
    return (-90 <= lat <= 90) and (-180 <= lng <= 180) and (lat != 0 or lng != 0)


def _points_changed(persist_couriers=False):
    """После любой правки точек выдачи: обновить совместимый вид депо,
    сохранить мета (или курьеров) и сбросить рассчитанный план."""
    STATE["depot"] = _depot_view()
    if persist_couriers:
        _persist_couriers()
    else:
        _persist_meta()
    _invalidate_plan(drop_plan=True)


@app.post("/api/depot")
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


@app.post("/api/points")
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


@app.post("/api/points/<pid>")
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


@app.delete("/api/points/<pid>")
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


@app.post("/api/couriers/<cid>/point")
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
    _points_changed(persist_couriers=True)
    return _payload()


@app.post("/api/couriers")
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


@app.patch("/api/couriers/<cid>")
def upd_courier(cid):
    data = _json()
    for c in STATE["couriers"]:
        if c["id"] == cid:
            if _home_point(c)["id"] != _my_point():
                return jsonify({"error": "Курьер другого депо — управлять может "
                                         "только диспетчер его точки"}), 403
            if "name" in data and data["name"].strip():
                c["name"] = data["name"].strip()
            if data.get("status") in STATUSES:
                c["status"] = data["status"]
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
            _invalidate_plan(drop_plan=True)  # курьер может быть помощником в чужом плане
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@app.post("/api/couriers/<cid>/bind")
def bind_courier(cid):
    """Привязка курьера к Telegram-пользователю (по chat_id из бота)."""
    data = _json()
    chat_id = str(data.get("chat_id") or "").strip()
    if not chat_id.isdigit():
        return jsonify({"error": "ID Telegram должен быть числом"}), 400
    for c in STATE["couriers"]:
        if c["id"] == cid:
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
            _tg_send(chat_id,
                     f"Готово! Вы привязаны: курьер «{_esc(c['name'])}».\n\n"
                     "Включите <b>живую геолокацию</b>:\n"
                     "скрепка → «Геолокация» → «Поделиться моей геолокацией» → "
                     "время <b>«Пока не отключу»</b>.\n\n"
                     "Диспетчер увидит вас на карте.")
            _bump()
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@app.post("/api/couriers/<cid>/unbind")
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


@app.post("/api/sim/geo")
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


@app.delete("/api/couriers/<cid>")
def del_courier(cid):
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if courier and _home_point(courier)["id"] != _my_point():
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    # его развозимые заказы возвращаются в очередь, чтобы не зависли
    for o in STATE["orders"]:
        if o.get("assigned") == cid and o.get("status") == "out":
            o["status"] = "ready"
            o["assigned"] = ""
            o["out_at"] = ""
    STATE["couriers"] = [c for c in STATE["couriers"] if c["id"] != cid]
    _persist_orders()
    _persist_couriers()
    _invalidate_plan(drop_plan=True)
    return _payload()


@app.post("/api/orders")
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
    return _payload()


@app.patch("/api/orders/<oid>")
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


@app.delete("/api/orders/<oid>")
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
    else:
        opid = None
    STATE["orders"] = [o for o in STATE["orders"] if o["id"] != oid]
    _persist_orders()
    _invalidate_plan(drop_plan=True, pid=opid)
    return _payload()


def _retiming_bases(routes):
    """Точки выдачи для маршрутов: (список домов, {courier_id: узел дома}).

    Берёт home_point из плана (падение в текущую точку курьера для старых планов).
    """
    homes, home_idx, home_of = [], {}, {}
    for r in routes:
        hp = r.get("home_point")
        if not hp:
            c = next((x for x in STATE["couriers"] if x["id"] == r["courier_id"]), None)
            hp = _home_point(c) if c else (STATE.get("points") or [None])[0]
        key = hp["id"] if hp else ""
        if key not in home_idx:
            home_idx[key] = len(homes)
            homes.append(hp)
        home_of[r["courier_id"]] = home_idx[key]
    return homes, home_of


def _patch_plan_after_assign(oids, cid=None):
    """Убрать выданные заказы из плана и пересчитать ETA оставшихся курьеров.

    План остаётся рабочим: диспетчер сразу выдаёт маршруты следующим курьерам,
    не дожидаясь полного пересчёта. Матрица берётся из кэша (набор точек тот же,
    что при расчёте), ETA пересчитываются от текущего момента.
    """
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    plan = _courier_plan(courier) if courier else _plan_for(_my_point())
    pid = (_home_point(courier)["id"] if courier and _home_point(courier)
           else _my_point())
    if not plan or not plan.get("routes"):
        return False
    oidset = set(oids)
    touched = set()
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            before = len(tr["stops"])
            tr["stops"] = [s for s in tr["stops"] if s["order_id"] not in oidset]
            if len(tr["stops"]) != before:
                touched.add(r["courier_id"])
        r["trips"] = [tr for tr in r.get("trips", []) if tr["stops"]]
    if not touched:
        return False
    now = _now()
    ready = [o for o in STATE["orders"] if (o.get("status") or "ready") == "ready"]
    touched_routes = [r for r in plan["routes"] if r["courier_id"] in touched]
    homes, home_of = _retiming_bases(touched_routes)
    K = len(homes)
    points = homes + ready
    try:
        matrix, _, _, _ = build_time_matrix(points, STATE["settings"])
        node = {o["id"]: K + i for i, o in enumerate(ready)}
        appr_home = [_approach_map(points, h, K, STATE["settings"]) for h in homes]
        for r in touched_routes:
            h = home_of[r["courier_id"]]
            _retime_route(r, matrix, node, STATE["settings"], now, appr_home[h], h)
    except Exception:
        log.warning("plan retime after assign failed, агрегаты пересобраны без матрицы")
        for r in plan["routes"]:
            r["stops"] = [s for tr in r.get("trips", []) for s in tr["stops"]]
            r["count"] = len(r["stops"])
    plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
    if not plan["routes"]:
        # всё выдано: мёртвый пустой план никому не нужен, а фоновый
        # пересчёт подхватит заказы, которые могли остаться вне маршрутов
        STATE["plans"].pop(pid, None)
        _persist_meta()
        _invalidate_plan(pid=pid)
        return True
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» больше не соответствуют плану
    plan.pop("moved", None)
    _persist_meta()
    _bump()  # выдача видна всем консолям сразу
    return True


@app.post("/api/plan/pin")
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
    try:
        solve_plan(point_id=opid)
    except (ValueError, RuntimeError) as e:
        order["pin"] = ""
        _persist_orders()
        return jsonify({"error": str(e)}), 400
    log.info("pin: %s -> %s", oid, courier["name"])
    _bump()
    return _payload()


@app.post("/api/plan/help")
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
    try:
        solve_plan(helpers=helpers, point_id=pid)
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)}), 400
    log.info("plan help: %s -> точка %s", courier["name"], point["name"])
    _bump()
    return _payload()


@app.post("/api/orders/assign")
def assign_orders():
    """Выдать заказы курьеру: статус ready -> out (в развозке)."""
    data = _json()
    oids = data.get("order_ids") or []
    cid = data.get("courier_id")
    if not isinstance(oids, list) or not oids:
        return jsonify({"error": "Не указаны заказы"}), 400
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    courier_pid = _home_point(courier)["id"]
    bad = [o for o in STATE["orders"]
           if o["id"] in oids and (o.get("status") or "ready") == "ready"
           and _obj_point(o) != courier_pid]
    if bad:
        pt = next((p["name"] for p in STATE.get("points", [])
                   if p["id"] == _obj_point(bad[0])), "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — выдать может только "
                                 f"курьер этой точки"}), 400
    now = _now().isoformat(timespec="seconds")
    given = 0
    for o in STATE["orders"]:
        if o["id"] in oids and (o.get("status") or "ready") == "ready":
            o["status"] = "out"
            o["assigned"] = cid
            o["out_at"] = now
            o["pin"] = ""  # выдан — закрепление больше не нужно
            given += 1
    if not given:
        return jsonify({"error": "Заказы уже выданы или не найдены"}), 400
    _persist_orders()
    if not _patch_plan_after_assign(oids, cid=cid):
        _invalidate_plan(drop_plan=True, pid=courier_pid)
    log.info("assign: %d заказ(ов) -> %s", given, courier["name"])
    return _payload()


@app.post("/api/orders/<oid>/return")
def return_order(oid):
    """Вернуть заказ из развозки в очередь готовых."""
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if (order.get("status") or "ready") != "out":
        return jsonify({"error": "Заказ не в развозке"}), 400
    order["status"] = "ready"
    order["assigned"] = ""
    order["out_at"] = ""
    _persist_orders()
    _invalidate_plan(pid=_obj_point(order))
    return _payload()


@app.post("/api/couriers/<cid>/returned")
def courier_returned(cid):
    """Курьер вернулся на базу: все его развозимые заказы доставлены (факт)."""
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    delivered = 0
    for o in STATE["orders"]:
        if o.get("assigned") == cid and o.get("status") == "out":
            _archive_order(o, "delivered", courier["name"], courier_id=cid)
            delivered += 1
    STATE["orders"] = [o for o in STATE["orders"]
                       if not (o.get("assigned") == cid and o.get("status") == "out")]
    courier["status"] = "base"
    courier["back_min"] = 0
    _persist_orders()
    _persist_couriers()
    _invalidate_plan(drop_plan=True, pid=_obj_point(courier))
    log.info("courier returned: %s, доставлено %d", courier["name"], delivered)
    return _payload()


@app.post("/api/settings")
def set_settings():
    data = _json()
    s = STATE["settings"]
    try:
        s["speed_kmh"] = min(120, max(5, int(data.get("speed_kmh", s["speed_kmh"]))))
        s["handover_min"] = min(60, max(0, int(data.get("handover_min", s["handover_min"]))))
        s["max_orders"] = min(50, max(1, int(data.get("max_orders", s["max_orders"]))))
        s["traffic"] = round(min(3.0, max(1.0, float(data.get("traffic", s["traffic"])))), 2)
        s["lights_sec_per_km"] = round(min(60.0, max(0.0, float(data.get("lights_sec_per_km", s["lights_sec_per_km"])))), 0)
        s["auto_prio_min"] = min(240, max(0, int(data.get("auto_prio_min", s.get("auto_prio_min", 0)))))
        s["reload_min"] = min(120, max(0, int(data.get("reload_min", s.get("reload_min", 10)))))
        s["hour_traffic"] = 1 if data.get("hour_traffic", s.get("hour_traffic", 1)) else 0
        s["approach_center_min"] = min(15, max(0, int(data.get("approach_center_min", s.get("approach_center_min", 4)))))
        s["approach_far_min"] = min(15, max(0, int(data.get("approach_far_min", s.get("approach_far_min", 2)))))
    except (TypeError, ValueError):
        return jsonify({"error": "Параметры должны быть числами"}), 400
    _persist_meta()
    _invalidate_plan(drop_plan=True)
    return _payload()


# Пороги «стоит ли ждать возвращающегося курьера»: ожидание выгодно при
# выигрыше ≥5 мин до последней доставки или ≥2 мин к среднему времени доставки.
_GAIN_LAST_MIN = 5
_GAIN_AVG_MIN = 2


def _advice_side(plan):
    counts = ", ".join(f'{r["courier_name"]}: {r["count"]}' for r in plan["routes"])
    return {"counts": counts or "–",
            "last_clock": plan["last_delivery_clock"],
            "last_min": plan["last_delivery_min"],
            "avg_min": plan["avg_delivery_min"]}


@app.post("/api/solve")
def solve():
    body = _json()
    mode = body.get("mode") if body.get("mode") in ("auto", "split", "now") else "auto"
    force = [x for x in (body.get("force") or []) if isinstance(x, str)]
    myp = _my_point()
    if force:
        for cid in force:
            c = next((c for c in STATE["couriers"] if c["id"] == cid), None)
            if not c:
                return jsonify({"error": "Курьер не найден"}), 404
            pid = _home_point(c)["id"]
            if pid != myp:
                pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == pid),
                          "другой точки")
                return jsonify({"error": f"«{c['name']}» работает с точкой «{pt}» — "
                                         f"он не может участвовать в плане вашего депо"}), 400
            if not any((o.get("status") or "ready") == "ready"
                       and _obj_point(o) == pid
                       for o in STATE["orders"]):
                pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == pid),
                          "его точки")
                return jsonify({"error": f"У точки «{pt}» нет готовых заказов — "
                                         f"«{c['name']}» не сможет участвовать в плане"}), 400
    try:
        t0 = time.time()
        plan = _compute_plan(mode, force=force, point_id=myp)
        log.info("solve[%s]: %d routes, provider=%s, scenario=%s, %.1fs",
                 myp[:6], len(plan["routes"]), plan.get("provider"),
                 (plan.get("advice") or {}).get("chosen", "no-away"), time.time() - t0)
    except (ValueError, RuntimeError) as e:
        log.warning("solve failed: %s", e)
        return jsonify({"error": str(e)}), 400
    _persist_meta()
    return _payload()


def _compute_plan(mode="auto", advice=True, force=None, point_id=None):
    """Ядро расчёта (без HTTP): план + совет «ждать/не ждать» своего депо.

    Вызывается ТОЛЬКО вручную: кнопка «Рассчитать» и выбор сценария совета.
    Ручной выбор («Не ждать»/«Ждать») помнится до смены обстановки.
    """
    point_id = point_id or _my_point()
    if mode in ("now", "split"):
        STATE["advice_modes"][point_id] = mode
    mode = STATE["advice_modes"].get(point_id) or mode
    plan_split = solve_plan(force=force, point_id=point_id)
    advice_obj = None
    mine = [c for c in STATE["couriers"] if _obj_point(c) == point_id]
    away = [c for c in mine
            if c["status"] == "away" and int(c.get("back_min", 15) or 0) > 0]
    has_base = any(c["status"] == "base" for c in mine)
    ready_n = sum(1 for o in STATE["orders"]
                  if (o.get("status") or "ready") == "ready" and _obj_point(o) == point_id)
    scenario = away and has_base and ready_n >= 2
    if not scenario:
        STATE["advice_modes"].pop(point_id, None)  # выбирать больше не из чего
    plan_now = None
    if (advice or mode == "now") and scenario:
        try:
            plan_now = solve_plan(include_away=False, with_geometry=False,
                                  force=force, point_id=point_id)
        except (ValueError, RuntimeError):
            plan_now = None
    if plan_now is not None:
        g_last = plan_now["last_delivery_min"] - plan_split["last_delivery_min"]
        g_avg = plan_now["avg_delivery_min"] - plan_split["avg_delivery_min"]
        recommend = ("split" if (g_last >= _GAIN_LAST_MIN or
                                 g_avg >= _GAIN_AVG_MIN) else "now")
        chosen = {"split": plan_split, "now": plan_now}[
            mode if mode != "auto" else recommend]
        if chosen is plan_now:
            _attach_geometry(plan_now)
        STATE["plans"][point_id] = chosen
        if advice:
            advice_obj = {
                "recommend": recommend, "mode": mode,
                "chosen": "split" if chosen is plan_split else "now",
                "wait_couriers": [{"name": c["name"],
                                   "back_min": int(c.get("back_min", 15) or 0),
                                   "back_clock": (_now() + timedelta(
                                       minutes=int(c.get("back_min", 15) or 0))
                                                  ).strftime("%H:%M")} for c in away],
                "now": _advice_side(plan_now),
                "split": _advice_side(plan_split),
                "gain_last_min": g_last, "gain_avg_min": g_avg,
                "held": [{"courier": r["courier_name"], "address": s["address"]}
                         for r in plan_split["routes"] if r["status"] == "away"
                         for s in r["stops"]],
            }
            chosen["advice"] = advice_obj
    _bump()  # план готов — сообщаем всем открытым консолям
    return STATE["plans"].get(point_id)


# ---------- инвалидация плана (расчёт — только вручную, по кнопке) ----------


def _invalidate_plan(drop_plan=False, pid=None):
    """План не пересчитываем в фоне — только помечаем/сбрасываем.

    pid — депо, чей план инвалидируем (None = все депо: правка точек/настроек).
    drop_plan=True — старый план точно невалиден (удаление заказа/курьера,
    смена депо): сбрасываем сразу. Иначе план показывается с пометкой
    «устарел», пока администратор не нажмёт «Рассчитать».
    """
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
    _bump()  # состояние изменилось — консоли обновятся сами


def _days_param():
    """?days=1..31 из запроса (по умолчанию 1)."""
    try:
        return min(31, max(1, int(request.args.get("days", 1))))
    except ValueError:
        return 1


@app.get("/api/history")
def api_history():
    return jsonify(_history_period(_days_param(), point_id=_my_point()))


@app.get("/api/history/export")
def api_history_export():
    """CSV за период (BOM — чтобы Excel сразу открыл кириллицу)."""
    days = _days_param()
    rows = _history_period(days, point_id=_my_point())["rows"]
    csv = ["Время закрытия;Адрес;Курьер;Исход;Цикл, мин"]
    for r in rows:
        csv.append(";".join(str(x if x is not None else "")
                            for x in (r["closed_at"].replace("T", " "), r["address"],
                                      r["courier"],
                                      "выдан" if r["outcome"] == "delivered" else "отменён",
                                      r["cycle_min"])))
    body = "\ufeff" + "\n".join(csv) + "\n"
    return (body, 200, {"Content-Type": "text/csv; charset=utf-8",
                        "Content-Disposition": f"attachment; filename=history-{days}d.csv"})


@app.get("/api/backup")
def api_backup():
    """Снимок базы данных (только админ) — скачивание файла."""
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может делать резервную копию"}), 403
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        with sqlite3.connect(_db_path, timeout=10) as src, \
                sqlite3.connect(tmp.name) as dst:
            src.backup(dst)
    except Exception:
        os.unlink(tmp.name)
        raise

    @after_this_request
    def _cleanup(resp):
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return resp
    return send_file(tmp.name, as_attachment=True,
                     download_name=f"dispatcher-backup-"
                                   f"{_now().strftime('%Y%m%d-%H%M')}.db")


def _geocode_center():
    d = STATE.get("depot")
    if d:
        return float(d["lat"]), float(d["lng"])
    return 52.4345, 31.0137  # центр Гомеля по умолчанию


# ---------- ручные правки плана: перенести остановку другому курьеру ----------

@app.post("/api/plan/move")
def plan_move():
    """Перенос заказа к другому курьеру в готовом плане (без полного пересчёта).

    Остановка снимается со своего места и вставляется в лучшую позицию лучшего
    заезда целевого курьера (минимальное удлинение маршрута). ETA обоих курьеров
    пересчитываются от текущего момента.
    """
    data = _json()
    oid, target = data.get("order_id"), data.get("to_courier")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    plan = _plan_for(_my_point())
    if not plan or not plan.get("routes"):
        return jsonify({"error": "Сначала рассчитайте план"}), 400
    dst = next((r for r in plan["routes"] if r["courier_id"] == target), None)
    if not dst:
        return jsonify({"error": "Курьер отсутствует в плане"}), 400
    dst_courier = next((c for c in STATE["couriers"] if c["id"] == target), None)
    opid = _obj_point(order)
    if dst_courier and opid and opid != _home_point(dst_courier)["id"]:
        pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == opid),
                  "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — перенести можно только "
                                 f"курьеру этой точки"}), 400

    stop, src_id = None, None
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
            if hit:
                stop, src_id = hit, r["courier_id"]
                tr["stops"].remove(hit)
    if stop is None:
        return jsonify({"error": "Заказа нет в текущем плане"}), 400

    touched_routes = [r for r in plan["routes"]
                      if r["courier_id"] in {src_id, dst["courier_id"]}]
    homes, home_of = _retiming_bases(touched_routes)
    K = len(homes)
    h_dst = home_of[dst["courier_id"]]
    points = homes + STATE["orders"]
    matrix, _, _, _ = build_time_matrix(points, STATE["settings"])
    appr_home = [_approach_map(points, h, K, STATE["settings"]) for h in homes]
    node = {o["id"]: K + i for i, o in enumerate(STATE["orders"])}
    g_x = node[oid]

    best = None  # (удлинение, индекс заезда, позиция вставки)
    for ti, tr in enumerate(dst.get("trips", [])):
        seq = [h_dst] + [node[s["order_id"]] for s in tr["stops"]] + [h_dst]
        for pos in range(1, len(seq)):
            a, b = seq[pos - 1], seq[pos]
            delta = matrix[a][g_x] + matrix[g_x][b] - matrix[a][b]
            if best is None or delta < best[0]:
                best = (delta, ti, pos - 1)
    if best is None:  # у цели не было заездов — создаём первый
        dst["trips"] = [{"stops": [], "total_min": 0, "start_delay_min": 0,
                         "start_clock": "", "end_clock": "", "distance_km": None}]
        best = (0, 0, 0)
    dst["trips"][best[1]]["stops"].insert(best[2], stop)
    order["pin"] = target  # закрепляем и на будущие пересчёты плана

    # пересчёт ETA затронутых курьеров от текущего момента
    now = _now()
    touched = {src_id, dst["courier_id"]}
    for r in touched_routes:
        if r["courier_id"] in touched:
            h = home_of[r["courier_id"]]
            _retime_route(r, matrix, node, STATE["settings"], now, appr_home[h], h)
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["moved"] = True
    log.info("plan move: %s -> %s (удлинение +%d мин)", oid, dst["courier_name"], best[0])
    _persist_meta()
    _bump()
    return _payload()


def _retime_route(route, matrix, node, settings, now_dt, appr=None, home=0):
    """Пересчёт ETA всех заездов курьера от now_dt. Меняет route на месте."""
    now_hm = now_dt.hour * 60 + now_dt.minute
    courier = next((c for c in STATE["couriers"]
                    if c["id"] == route.get("courier_id")), None)
    default_kmh = max(5.0, float(settings.get("speed_kmh", 60)))
    kmh, _src = _courier_speed(courier, settings) if courier else (default_kmh, "default")
    spd_factor = max(0.25, min(4.0, default_kmh / kmh))
    route["speed_kmh"] = round(kmh, 1)
    route["speed_src"] = _src
    for tr in route.get("trips", []):
        seq = [node[s["order_id"]] for s in tr["stops"]]
        etas, total = _eta_pass(seq, tr["start_delay_min"], matrix, settings,
                                now_dt, appr, home, spd_factor=spd_factor)
        for s, eta in zip(tr["stops"], etas):
            s["eta_min"] = eta
            s["eta_clock"] = (now_dt + timedelta(minutes=eta)).strftime("%H:%M")
            rel = _deadline_rel_min(s.get("deadline"), now_hm)
            s["late_min"] = max(0, eta - rel) if rel is not None else 0
        tr["total_min"] = total
        tr["end_clock"] = (now_dt + timedelta(minutes=total)).strftime("%H:%M")
        tr["start_clock"] = (now_dt + timedelta(
            minutes=tr["start_delay_min"])).strftime("%H:%M")
    route["stops"] = [s for tr in route.get("trips", []) for s in tr["stops"]]
    route["count"] = len(route["stops"])
    if route["stops"]:
        route["total_min"] = max(tr["total_min"] for tr in route["trips"])


# ---------- Telegram-уведомление курьеру (заготовка) ----------

@app.post("/api/notify/courier/<cid>")
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


@app.post("/api/profile")
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


@app.post("/api/notify/tg")
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
    except requests.RequestException as e:
        return jsonify({"error": f"Telegram недоступен: {e}"}), 502
    if not data.get("ok"):
        return jsonify({"error": f"Telegram: {data.get('description', 'ошибка')}"}), 400
    return jsonify({"ok": True})


# ---------- статистика недели ----------

@app.get("/api/stats/week")
def stats_week():
    """Динамика за 7 дней: по дням и по курьерам + доля вовремя."""
    hist = _history_period(7, point_id=_my_point())
    days, couriers = {}, {}
    on_time = on_time_total = 0
    for r in hist["rows"]:
        day = (r["closed_at"] or "")[:10]
        d = days.setdefault(day, {"day": day, "delivered": 0, "cancelled": 0,
                                  "cycles": []})
        if r["outcome"] == "delivered":
            d["delivered"] += 1
            if r["cycle_min"] is not None:
                d["cycles"].append(r["cycle_min"])
            name = r["courier"] or "–"
            c = couriers.setdefault(name, {"courier": name, "delivered": 0,
                                           "cycles": []})
            c["delivered"] += 1
            if r["cycle_min"] is not None:
                c["cycles"].append(r["cycle_min"])
            dl = r.get("deadline")
            closed_hm = (r["closed_at"] or "T")[11:16]
            if dl and closed_hm:
                try:
                    dh, dm = int(dl[:2]) * 60 + int(dl[3:5])
                    ch, cm = int(closed_hm[:2]) * 60 + int(closed_hm[3:5])
                    on_time_total += 1
                    if ch <= dh:
                        on_time += 1
                except (ValueError, IndexError):
                    pass
        else:
            d["cancelled"] += 1
    out_days = [{"day": d["day"], "delivered": d["delivered"],
                 "cancelled": d["cancelled"],
                 "avg_cycle_min": round(sum(d["cycles"]) / len(d["cycles"]))
                 if d["cycles"] else None}
                for d in sorted(days.values(), key=lambda x: x["day"])]
    out_cour = [{"courier": c["courier"], "delivered": c["delivered"],
                 "avg_cycle_min": round(sum(c["cycles"]) / len(c["cycles"]))
                 if c["cycles"] else None}
                for c in sorted(couriers.values(), key=lambda x: -x["delivered"])]
    return jsonify({"days": out_days, "couriers": out_cour,
                    "on_time": on_time, "on_time_total": on_time_total})


@app.get("/api/report/day")
def api_report_day():
    """Данные отчёта дня (печатную версию рендерит фронт)."""
    hist = _history_period(1, point_id=_my_point())
    plan = _plan_for(_my_point()) or {}
    routes = [{"courier_name": r.get("courier_name"), "status": r.get("status"),
               "stops": [s.get("address") for s in (r.get("stops") or [])]}
              for r in (plan.get("routes") or [])]
    return jsonify({"rows": hist["rows"], "summary": hist.get("summary") or {},
                    "routes": routes,
                    "today": _now().strftime("%d.%m.%Y"),
                    "now": _now().strftime("%H:%M")})


def _strip_street_type(s):
    s = re.sub(r"^(улица|вуліца|ул\.|ulitsa|ul\.|проспект|пр-т|переулок|пер\.|бульвар|площадь|пл\.)\s*",
               "", (s or "").strip(), flags=re.I)
    # «Советская улица» -> «Советская» (проспекты/площади не трогаем: тип — часть имени)
    return re.sub(r"\s*(улица|вуліца)$", "", s, flags=re.I).strip()


def _extract_house(q):
    m = re.search(r"(\d+[а-яa-zA-Z]*(?:\s*[/-]\s*\d+)?)\s*$", q.strip())
    return m.group(1).replace(" ", "") if m else ""


def _same_house(qnum, hnum):
    if not qnum or not hnum:
        return False
    a = str(hnum).lower().replace(" ", "")
    b = qnum.lower()
    return a == b or a.split("/")[0] == b.split("/")[0] or a.split("к")[0] == b.split("к")[0]


UA = {"User-Agent": "delivery-dispatcher/1.0 (local admin tool)"}
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter")
HOUSES_CACHE = {}  # street_lower -> (ts, houses[(num, lat, lng)])
HOUSES_CACHE_MAX = 200
HOUSES_DISK = os.path.join(DATA_DIR, "houses_cache.json")
HOUSES_DISK_LOCK = threading.Lock()


def _houses_disk_load():
    """дома с Overpass качаются дорого — переживаем рестарты через диск"""
    try:
        with open(HOUSES_DISK, encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            try:
                HOUSES_CACHE[k] = (float(v["ts"]), [tuple(h) for h in v["houses"]])
            except Exception:  # noqa: BLE001
                continue
        log.info("houses cache: %d улиц с диска", len(HOUSES_CACHE))
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("houses cache не читается: %s", exc)


def _houses_disk_save():
    with HOUSES_DISK_LOCK:
        try:
            with open(HOUSES_DISK, "w", encoding="utf-8") as f:
                json.dump({k: {"ts": ts, "houses": hs} for k, (ts, hs) in HOUSES_CACHE.items()},
                          f, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("houses cache не записан: %s", exc)
HOUSES_TTL_FULL, HOUSES_TTL_EMPTY = 24 * 3600, 600


def osm_local_street_name(osm_type, osm_id):
    """Локальное (OSM) название улицы: «вуліца Цялегіна» -> «Цялегіна».
    Сначала — из индекса улиц (без сети, улицы Гомеля); иначе теги объекта из Overpass."""
    if not osm_type or not osm_id:
        return ""
    try:
        names = STREET_IDX["ways"].get(int(osm_id))
        if names and names[0]:
            return names[0]
    except (TypeError, ValueError):
        pass
    t = {"way": "way", "relation": "rel", "node": "node"}.get(osm_type)
    if not t:
        return ""
    q = f'[out:json][timeout:10];{t}({osm_id});out tags 1;'
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=15)
            r.raise_for_status()
            els = r.json().get("elements", [])
            if not els:
                return ""
            tags = els[0].get("tags") or {}
            # addr:street у домов хранит локальное (бел) имя — оно и нужно для сверки
            return _strip_street_type(tags.get("name") or tags.get("name:ru") or "")
        except Exception:  # noqa: BLE001
            continue
    return ""


def overpass_street_houses(base, clat, clng, alt=None):
    """Дома с номерами на улице base вокруг точки (clat, clng). Кэш 24 ч.
    alt — второе написание улицы (рус/бел): в OSM addr:street бывает «вуліца Цялегіна»,
    а lookup имени упал и вернул русское «Телегина» — ищем по обоим."""
    bases = [b for b in (base, alt) if b and b.strip()]
    key = "+".join(sorted(b.lower() for b in bases))
    now = time.time()
    if key in HOUSES_CACHE:
        ts, houses = HOUSES_CACHE[key]
        if now - ts < (HOUSES_TTL_FULL if houses else HOUSES_TTL_EMPTY):
            return houses
    rx = "|".join(re.escape(b) for b in bases)
    lows = [b.lower() for b in bases]
    q = (f'[out:json][timeout:15];'
         f'nwr["addr:housenumber"]["addr:street"~"{rx}",i]'
         f'({clat - 0.025:.5f},{clng - 0.04:.5f},{clat + 0.025:.5f},{clng + 0.04:.5f});out center 100;')
    houses = []
    answered = False  # Overpass ответил (пусть и пусто) — иначе сбой сети портит кэш
    for attempt in range(2):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=8)
                r.raise_for_status()
                answered = True
                for e in r.json().get("elements", []):
                    tags = e.get("tags") or {}
                    street = _strip_street_type(tags.get("addr:street") or "").lower()
                    if not any(b in street for b in lows):
                        continue
                    m = re.match(r"\s*(\d+)", str(tags.get("addr:housenumber") or ""))
                    if not m:
                        continue
                    c = e.get("center") or e
                    if c.get("lat") is None or (c.get("lon") is None and c.get("lng") is None):
                        continue
                    houses.append((int(m.group(1)), float(c["lat"]),
                                   float(c.get("lon", c.get("lng")))))
                break
            except Exception:  # noqa: BLE001
                continue
        if answered:
            break
        time.sleep(2)  # обе зеркала заняты: короткая пауза и ещё попытка
    if not answered:
        return []  # сбой сети — не кэшируем его как «домов нет»
    if len(HOUSES_CACHE) >= HOUSES_CACHE_MAX:
        HOUSES_CACHE.clear()  # улиц немного: проще сбросить, чем вести LRU
    HOUSES_CACHE[key] = (now, houses)
    _houses_disk_save()
    return houses


def interp_house(houses, num):
    """(lat, lng, note) для дома num по известным домам улицы."""
    exact = [h for h in houses if h[0] == num]
    if exact:
        return exact[0][1], exact[0][2], ""
    same = sorted(h for h in houses if h[0] % 2 == num % 2)
    lo = [h for h in same if h[0] < num]
    hi = [h for h in same if h[0] > num]
    if lo and hi:
        a, b = max(lo), min(hi)
        t = (num - a[0]) / (b[0] - a[0])
        return (a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t,
                f"точка между домами {a[0]} и {b[0]}")
    if lo or hi:
        n = max(lo) if lo else min(hi)
        return n[1], n[2], f"рядом с домом {n[0]}"
    return None


def _bbox(lat, lng, radius_km):
    """Рамка (viewbox/bbox для Nominatim/Photon) радиусом radius_km вокруг точки."""
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * math.cos(math.radians(lat)) or 1.0)
    return f"{lng - dlng},{lat + dlat},{lng + dlng},{lat - dlat}"


_STREET_TYPES = re.compile(r"(проспект|праспект|площадь|плошча|бульвар|шоссе|тракт|"
                           r"переулок|завулак|набережная|спуск|линия)", re.I)


def _clean_place(p):
    """«Поколюбичский сельский Совет» -> «Поколюбичский»."""
    return re.sub(r"\s*(сельский совет|сельсовет|сельскі савет)$", "", (p or "").strip(), flags=re.I)


def _place_label(street, place, hn="", is_street=True):
    """«Гомель, ул. Тельмана, 19». Микрорайоны (Мельников Луг и пр.) не показываем."""
    name = (street or "").strip()
    if is_street and name and not _STREET_TYPES.search(name):
        name = "ул. " + name
    if hn:
        name = f"{name}, {hn}" if name else str(hn)
    place = _clean_place(place)
    if place and place.lower() != (street or "").strip().lower():
        return f"{place}, {name}" if name else place
    return name or place or "точка"


_NOM_LAST = [0.0]  # темп 1 запрос/сек к Nominatim: спим только остаток, а не вслепую
_GEO_CACHE = OrderedDict()  # готовые ответы геокодера: адреса не двигаются
_GEO_CACHE_MAX = 300
_GEO_TTL = 24 * 3600


def search_nominatim(q, lat, lng, radius_km, bounded=True):
    wait = 1.05 - (time.monotonic() - _NOM_LAST[0])
    if wait > 0:
        time.sleep(wait)
    _NOM_LAST[0] = time.monotonic()
    params = {"q": q, "format": "json", "limit": 12, "addressdetails": 1,
              "accept-language": "ru", "countrycodes": "by"}
    if bounded:
        params["viewbox"] = _bbox(lat, lng, radius_km)
        params["bounded"] = 1
    resp = requests.get("https://nominatim.openstreetmap.org/search",
                        params=params, headers=UA, timeout=7)
    resp.raise_for_status()
    out = []
    for it in resp.json():
        addr = it.get("address") or {}
        road = _strip_street_type(addr.get("road") or addr.get("pedestrian") or "")
        hn = addr.get("house_number") or ""
        # Nominatim кладёт сельсовет в city, а сам посёлок — в village: деревня важнее
        place = _clean_place(addr.get("village") or addr.get("hamlet") or addr.get("town")
                             or addr.get("city") or addr.get("municipality") or "")
        state = addr.get("state") or ""
        label = _place_label(road or _clean_place(it.get("name") or ""), place, hn, is_street=bool(road))
        plat, plng = float(it["lat"]), float(it.get("lng") or it.get("lon"))
        out.append({"label": label,
                    "lat": plat, "lng": plng, "hn": hn, "road": road,
                    "place": place, "kind": it.get("type") or "",
                    "state": state,
                    "osm_type": it.get("osm_type") or "", "osm_id": it.get("osm_id") or ""})
    return out


def search_photon(q, lat, lng, radius_km=None):
    params = {"q": q, "lat": lat, "lon": lng, "limit": 12, "lang": "default"}
    if radius_km:
        params["bbox"] = _bbox(lat, lng, radius_km)
    resp = requests.get("https://photon.komoot.io/api",
                        params=params, headers=UA, timeout=6)
    resp.raise_for_status()
    out = []
    for f in resp.json().get("features", []):
        p = f.get("properties") or {}
        lon, plat = f.get("geometry", {}).get("coordinates", [0, 0])
        street = _strip_street_type(p.get("street") or (p.get("osm_value") in ("street", "residential", "house") and p.get("name")) or "")
        hn = p.get("housenumber") or ""
        if street:
            place = p.get("city") or p.get("town") or ""
        else:  # населённый пункт: деревня/местность важнее района и сельсовета
            place = p.get("locality") or p.get("village") or p.get("city") or p.get("town") or ""
        place = _clean_place(place)
        state = p.get("state") or ""
        label = _place_label(street or _clean_place(p.get("name") or ""), place, hn, is_street=bool(street))
        out.append({"label": label,
                    "lat": plat, "lng": lon, "hn": hn, "street": street,
                    "place": place, "kind": p.get("osm_value") or "",
                    "state": state})
    return out


V1_IMP = {"house": 0.40, "building": 0.35, "entrance": 0.30, "street": 0.15,
          "residential": 0.15, "suburb": 0.10, "neighbourhood": 0.10, "quarter": 0.10,
          "administrative": 0.30, "village": 0.30, "hamlet": 0.28, "town": 0.30}
V2_IMP = {"house": 0.42, "house-number": 0.42, "building": 0.35, "street": 0.20,
          "residential": 0.18, "suburb": 0.10, "district": 0.10}


def reverse_geocode(lat, lng):
    """Адрес по координате (для кликов по карте): «Телегина 9, Гомель» или ''."""
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/reverse",
                            params={"lat": lat, "lon": lng, "format": "json",
                                    "zoom": 18, "addressdetails": 1, "accept-language": "ru"},
                            headers=UA, timeout=8)
        resp.raise_for_status()
        addr = (resp.json() or {}).get("address") or {}
        road = _strip_street_type(addr.get("road") or addr.get("pedestrian") or "")
        hn = addr.get("house_number") or ""
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        label = _place_label(road or "", city, hn, is_street=bool(road)) if road or hn else ""
        return label
    except Exception:  # noqa: BLE001
        pass
    return ""


GOMEL_BBOX = (52.325, 30.78, 52.61, 31.14)  # S, W, N, E — город с Новобелицей
STREET_IDX = {"ts": 0.0, "names": {}, "ways": {}, "alt": {}}  # names: lower(name)->(shown,lat,lng); ways: osm_id -> (local, ru); alt: lower -> другое написание


def _rebuild_alt():
    """Пары написаний (рус <-> бел) из ways: для интерполяции домов по опечаткам."""
    alt = {}
    for loc, ru in STREET_IDX["ways"].values():
        if loc and ru:
            alt[ru.lower()] = loc
            alt[loc.lower()] = ru
    STREET_IDX["alt"] = alt
STREET_IDX_TTL = 24 * 3600
STREET_IDX_LOCK = threading.Lock()


def _fill_street_index(network=True):
    """Тело загрузки индекса улиц; вызывать только под STREET_IDX_LOCK.
    network=False — только дисковый кэш (для запросов геокода, без задержек на сеть)."""
    if STREET_IDX["names"] and time.time() - STREET_IDX["ts"] < STREET_IDX_TTL:
        return STREET_IDX["names"]
    path = os.path.join(DATA_DIR, "streets_cache.json")
    try:  # дисковый кэш переживает рестарты (формат v2: names + ways)
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < STREET_IDX_TTL:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("v") == 3:
                STREET_IDX["names"] = {k: tuple(v) for k, v in data["names"].items()}
                STREET_IDX["ways"] = {int(k): tuple(v) for k, v in data["ways"].items()}
                _rebuild_alt()
                STREET_IDX["ts"] = os.path.getmtime(path)
                return STREET_IDX["names"]
    except Exception:  # noqa: BLE001
        pass
    s, w, n, e = GOMEL_BBOX
    q = (f'[out:json][timeout:25];'
         f'way["highway"~"^(residential|tertiary|secondary|primary|unclassified|living_street|pedestrian|service)$"]["name"]'
         f'({s},{w},{n},{e});out tags center 8000;')
    acc = {}  # lower -> [shown, sum_lat, sum_lng, cnt]
    for url in (OVERPASS_URLS if network else []):
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=15)
            r.raise_for_status()
            for el in r.json().get("elements", []):
                tags = el.get("tags") or {}
                c = el.get("center") or {}
                if not c.get("lat"):
                    continue
                loc = _strip_street_type(tags.get("name") or "")
                ru = _strip_street_type(tags.get("name:ru") or "")
                if el.get("id") and (loc or ru):
                    STREET_IDX["ways"][el["id"]] = (loc, ru)
                for nm in (tags.get("name:ru"), tags.get("name")):
                    nm = _strip_street_type(nm or "")
                    if len(nm) < 3:
                        continue
                    k = nm.lower()
                    a = acc.setdefault(k, [nm, 0.0, 0.0, 0])
                    a[1] += float(c["lat"]); a[2] += float(c.get("lon", 0)); a[3] += 1
            break
        except Exception:  # noqa: BLE001
            continue
    if acc:  # пустой ответ не затирает то, что уже есть
        STREET_IDX["names"] = {k: (v[0], v[1] / v[3], v[2] / v[3]) for k, v in acc.items()}
        STREET_IDX["ts"] = time.time()
        _rebuild_alt()
        app.logger.info("street index: %d улиц", len(STREET_IDX["names"]))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"v": 3, "names": STREET_IDX["names"], "ways": STREET_IDX["ways"]},
                          fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass
    return STREET_IDX["names"]


def _load_street_index():
    """Именованные улицы Гомеля одним Overpass-запросом; кэш в памяти, на диске и на сутки.
    Photon не индексирует name:ru, Nominatim не умеет префиксы — этот индекс закрывает both."""
    with STREET_IDX_LOCK:
        return _fill_street_index()


def _lev(a, b, maxd=2):
    """Левенштейн с отсечкой: >maxd — сразу maxd+1 (без полной матрицы)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > maxd:
        return maxd + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
            if cur[j] < best:
                best = cur[j]
        if best > maxd:
            return maxd + 1
        prev = cur
    return prev[lb]


def _typo_max(word):
    """Допуск опечаток: 1 для слов >=6 букв, 2 для >=9; короткие — строго."""
    n = len(word)
    return 2 if n >= 9 else (1 if n >= 6 else 0)


def _word_like(t, h):
    """Слово запроса t против слова адреса h: точное/префикс или опечатка."""
    if t == h or h.startswith(t) or t.startswith(h):
        return True
    m = _typo_max(t)
    return m > 0 and _lev(t, h, m) <= m


def _search_local_streets(token, limit=6):
    """Улицы города по token: точное/префикс/подстрока, затем опечатки (левенштейн).
    Индекс грузится в фоне? Не ждём — просто без локальных подсказок."""
    t = (token or "").lower().strip()
    if len(t) < 3:
        return []
    if not (STREET_IDX["names"] and time.time() - STREET_IDX["ts"] < STREET_IDX_TTL):
        if not STREET_IDX_LOCK.acquire(blocking=False):
            return []
        try:
            _fill_street_index(network=False)  # диск-кэш мгновенно; сеть — только фоновый поток
        finally:
            STREET_IDX_LOCK.release()
    hits = []
    fuzzy = []
    tm = _typo_max(t)
    for k, (shown, lat, lng) in STREET_IDX["names"].items():
        if k.startswith(t):
            hits.append((0 if k == t else 1, 0, len(k), shown, lat, lng))
        elif len(t) >= 4 and t in k:
            hits.append((2, 0, len(k), shown, lat, lng))
        elif tm and _lev(t, k, tm) <= tm:
            fuzzy.append((3, _lev(t, k, tm), len(k), shown, lat, lng))
    hits.sort(key=lambda x: (x[0], x[2]))
    res = hits[:limit]
    if len(res) < limit and fuzzy:
        fuzzy.sort(key=lambda x: (x[1], x[2]))
        res += fuzzy[:limit - len(res)]
    return [(h[3], h[4], h[5]) for h in res]


def _tok(s):
    # ё -> е: «еремино» должен находить «Ерёмино»
    return [t.replace("ё", "е") for t in re.split(r"[^а-яёa-z0-9]+", (s or "").lower()) if len(t) >= 3]


@app.get("/api/geocode")
def geocode():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    lat, lng = _geocode_center()
    gkey = (q.lower(), round(lat, 3), round(lng, 3))
    hit = _GEO_CACHE.get(gkey)
    if hit and time.time() - hit[0] < _GEO_TTL:
        return jsonify(hit[1])
    qnum = _extract_house(q)
    q_words = _tok(re.sub(r"\d+[а-яa-z]*", " ", q))  # слова запроса без номера дома
    try:
        # два геокодера — параллельно; для запроса с домом параллельно же
        # прогреваем кэш домов вероятной улицы (Overpass медленный, но теперь
        # он не блокирует: к моменту интерполяции кэш уже горячий)
        with ThreadPoolExecutor(3) as ex:
            f_nom = ex.submit(search_nominatim, q, lat, lng, 25.0)
            f_ph = ex.submit(search_photon, q, lat, lng)
            f_houses = (ex.submit(overpass_street_houses, _strip_street_type(q_words[-1]), lat, lng)
                        if qnum and q_words else None)
            v1 = f_nom.result()
            try:
                v2 = f_ph.result()
            except Exception:  # noqa: BLE001
                v2 = []
            if f_houses is not None:
                try:
                    f_houses.result()
                except Exception:  # noqa: BLE001
                    pass
        # «еремино, школьная 13» Nominatim в таком порядке не находит —
        # пробуем «школьная 13, еремино» (улица+дом вперёд)
        if qnum and len(q_words) >= 2 and not v1:
            v1 = search_nominatim(f"{q_words[-1]} {qnum}, {' '.join(q_words[:-1])}", lat, lng, 25.0)

        def dist_of(it):
            return haversine_km({"lat": lat, "lng": lng}, {"lat": it["lat"], "lng": it["lng"]})

        # в городе мало совпадений — ищем по всей Гомельской области
        if sum(1 for it in v1 + v2 if dist_of(it) <= 25) < 4:
            v3 = search_nominatim(q, lat, lng, 0, bounded=False)
        else:
            v3 = []

        def _ok(it, dist):
            if dist <= 25:        # город и ближние пригороды — всегда подходят
                return True
            if dist > 170:        # дальше края области не нужно
                return False
            return "гомельск" in (it.get("state") or "").lower()

        scored = []
        seen = {}
        seen_lbl = {}
        exact_house = False
        for src, items, imp in (("v1", v1, V1_IMP), ("v2", v2, V2_IMP), ("v3", v3, V1_IMP)):
            for it in items:
                dist = dist_of(it)
                if not _ok(it, dist):
                    continue
                # ищут конкретный дом — районы и области не предлагать
                if qnum and it["kind"] in ("administrative", "county", "state", "region"):
                    continue
                # близкие весят дёшево, за городом — вполцены
                w = dist * 0.3 if dist <= 25 else 7.5 + (dist - 25) * 0.12
                importance = imp.get(it["kind"], 0.05)
                score = w + (1.0 - importance)
                # релевантность: слова запроса против адреса (точное слово весит сильнее префикса)
                if q_words:
                    hay = set(_tok(f"{it['label']} {it.get('road') or ''} {it.get('place') or ''}"))
                    for t in q_words:
                        if t in hay:
                            score -= 1.5
                        elif any(h.startswith(t) for h in hay):
                            score -= 0.8
                        elif any(_word_like(t, h) for h in hay):
                            score -= 0.5   # опечатка — тоже релевантно, но слабее
                if qnum:
                    if _same_house(qnum, it["hn"]):
                        score -= 0.6          # точное совпадение номера дома
                        exact_house = True
                    elif not it["hn"]:
                        score += 0.35         # ищут дом, а это просто улица
                    else:
                        score += 0.15         # чужой номер — шум
                key = (round(it["lat"], 5), round(it["lng"], 5))
                if key in seen and seen[key] <= score:
                    continue
                lbl = it["label"].split("(")[0].strip().lower()  # один адрес — одна строка
                if lbl in seen_lbl:
                    continue
                seen[key] = score
                seen_lbl[lbl] = score
                scored.append({"label": it["label"], "lat": it["lat"], "lng": it["lng"],
                               "km": round(dist), "_s": score})
        # все слова запроса обязаны найтись в адресе (с допуском опечаток):
        # «еремино, школьная 13» не должно давать «Улукаўскі, ул. Школьная, 13»
        # ведущая цифра запроса («3-я авиационная») — часть имени улицы:
        # фильтр должен требовать её в адресе, иначе сыплются 1-я/2-я
        lead_ord = re.match(r"\s*(\d{1,2})\s*[-–—]?\s*([а-яё]{0,2})\s", q.lower() + " ")
        ord_num = int(lead_ord.group(1)) if lead_ord and 1 <= int(lead_ord.group(1)) <= 30 else None
        fw = q_words + ([f"{ord_num}-я"] if ord_num else [])
        if fw and scored:
            def _hit_all(x):
                hay = set(_tok(x["label"].split("(")[0]))
                return all(any(_word_like(t, h) for h in hay) for t in fw)
            survived = [x for x in scored if _hit_all(x)]
            if survived:
                scored = survived
        # префиксы («телег…») геокодеры не умеют — локальный индекс улиц города
        if q_words and not qnum:
            for shown, slat, slng in _search_local_streets(q_words[0]):
                lbl_txt = _place_label(shown, "Гомель", "", True)
                lbl = lbl_txt.split("(")[0].strip().lower()
                if seen_lbl.get(lbl, 99) <= 2.5:
                    continue
                d = haversine_km({"lat": lat, "lng": lng}, {"lat": slat, "lng": slng})
                if d > 30:
                    continue
                seen_lbl[lbl] = 2.5
                scored.append({"label": lbl_txt, "lat": slat, "lng": slng,
                               "km": round(d), "_s": 2.5})
        # ведущая цифра или хвостовой номер («авиационная 3») —
        # в семье нумерованных улиц это улица, а не дом
        ordinal_hit = None
        if (qnum or ord_num) and q_words:
            mq = re.match(r"\d+", qnum or "")
            n_ord = ord_num or (int(mq.group(0)) if mq and 1 <= int(mq.group(0)) <= 30 else None)
            if n_ord:
                for shown, slat, slng in _search_local_streets(q_words[0], 12):
                    if shown.lower().startswith(f"{n_ord}-"):
                        ordinal_hit = (shown, slat, slng)
                        break
        if ordinal_hit:
            shown, slat, slng = ordinal_hit
            lbl_txt = _place_label(shown, "Гомель", "", True)
            lbl = lbl_txt.split("(")[0].strip().lower()
            if seen_lbl.get(lbl, 99) > -1:
                d_ord = haversine_km({"lat": lat, "lng": lng}, {"lat": slat, "lng": slng})
                if d_ord <= 30:
                    seen_lbl[lbl] = -1
                    scored.insert(0, {"label": lbl_txt, "lat": slat, "lng": slng,
                                      "km": round(d_ord), "_s": -1})
        # Номер дома не найден геокодерами — интерполируем по соседним домам улицы
        if qnum and not exact_house and not ordinal_hit:
            def _word_hit(it):
                hay = set(_tok(f"{it.get('road') or ''} {it.get('place') or ''}"))
                return sum(1 for t in q_words if t in hay or any(h.startswith(t) for h in hay))
            cands = [it for it in v1 if it["kind"] in ("street", "residential") and it.get("road")]
            cands.sort(key=lambda it: -_word_hit(it))
            street_hit = cands[0] if cands else None
            if not street_hit and q_words:
                # геокодеры промахнулись (опечатка/префикс) — локальный индекс улиц
                loc = _search_local_streets(q_words[0], 1)
                if loc:
                    shown, slat, slng = loc[0]
                    street_hit = {"road": shown, "place": "Гомель",
                                  "lat": slat, "lng": slng, "osm_type": "", "osm_id": "",
                                  "_local": True}
            if street_hit:

                def _interp():
                    mnum = re.match(r"\d+", qnum)
                    if not mnum:
                        return None
                    base_ru = _strip_street_type(street_hit["road"])
                    base_osm = (osm_local_street_name(street_hit.get("osm_type"), street_hit.get("osm_id"))
                                or STREET_IDX["alt"].get(base_ru.lower()) or "")
                    houses = overpass_street_houses(base_osm or base_ru, street_hit["lat"], street_hit["lng"],
                                                    alt=base_ru if base_osm else None)
                    return interp_house(houses, int(mnum.group(0)))

                pt = None
                ex2 = ThreadPoolExecutor(1)  # бюджет на сетевые походы за домами
                try:
                    pt = ex2.submit(_interp).result(timeout=9)
                except FuturesTimeout:
                    pt = None
                finally:
                    ex2.shutdown(wait=False)
                if pt:
                    hlat, hlng, note = pt
                    base = _strip_street_type(street_hit["road"])
                    city = street_hit.get("place") or "Гомель"
                    suffix = f" ({note})" if note else ""
                    scored.insert(0, {"label": _place_label(base, city, qnum, True) + suffix,
                                      "lat": hlat, "lng": hlng, "_s": -1})
                else:
                    # дома не добыли за бюджет — хотя бы сама улица,
                    # но только если геокодеры её сами не дали (нет дубля)
                    if street_hit.get("_local"):
                        base = _strip_street_type(street_hit["road"])
                        city = street_hit.get("place") or "Гомель"
                        d = haversine_km({"lat": lat, "lng": lng},
                                         {"lat": street_hit["lat"], "lng": street_hit["lng"]})
                        scored.append({"label": _place_label(base, city, "", False),
                                       "lat": street_hit["lat"], "lng": street_hit["lng"],
                                       "km": round(d), "_s": 3.0 + d * 0.1})
        if not scored and len(q_words) > 1:  # «бобовичи советская» -> «бобовичи»
            try:
                v4 = search_nominatim(" ".join(q_words[:-1]), lat, lng, 25.0, bounded=False)
            except Exception:  # noqa: BLE001
                v4 = []
            for it in v4[:5]:
                d = dist_of(it)
                if d > 60:
                    continue
                lbl = it["label"].split("(")[0].strip().lower()
                if lbl in seen_lbl:
                    continue
                seen_lbl[lbl] = 99
                scored.append({"label": it["label"], "lat": it["lat"], "lng": it["lng"],
                               "km": round(d), "_s": 3.0 + d * 0.1})
        if not scored:  # запасной вариант без рамки — только Беларусь
            scored = [{"label": it["label"], "lat": it["lat"], "lng": it["lng"], "_s": 99}
                      for it in search_nominatim(q, lat, lng, 25.0, bounded=False)[:5]]
        scored.sort(key=lambda x: x["_s"])
        payload = [{k: v for k, v in x.items() if k != "_s"} for x in scored[:7]]
        if payload:  # пустой ответ не кэшируем: часто это «индекс ещё не прогрелся»
            _GEO_CACHE[gkey] = (time.time(), payload)
            _GEO_CACHE.move_to_end(gkey)
            while len(_GEO_CACHE) > _GEO_CACHE_MAX:
                _GEO_CACHE.popitem(last=False)
        return jsonify(payload)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Геокодер недоступен: {e}"}), 502


def _warm_street_index():
    # Облачный инстанс: Overpass может отвергнуть первый запрос (rate-limit) —
    # повторяем с нарастающей паузой, пока индекс не соберётся.
    for attempt, delay in enumerate((5, 60, 300, 900), start=1):
        _load_street_index()
        if STREET_IDX["names"]:
            return
        log.warning("street index empty (attempt %d), retry in %ss", attempt, delay)
        time.sleep(delay)
    log.error("street index failed — prefix search degraded")


if __name__ == "__main__":
    load_state()
    ensure_default_admin()
    _houses_disk_load()
    threading.Thread(target=_warm_street_index, daemon=True).start()  # прогрев индекса улиц
    _tg_start_polling()  # бот: геолокации курьеров
    log.info("starting on %s:%s (auth=email, db=%s)", CFG["host"], CFG["port"], _db_path)
    try:
        from waitress import serve
        serve(app, host=CFG["host"], port=CFG["port"], threads=24)
    except ImportError:
        log.warning("waitress not installed — falling back to Flask dev server")
        app.run(host=CFG["host"], port=CFG["port"], debug=False, threaded=True)
