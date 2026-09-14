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
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

import requests
from flask import (Flask, after_this_request, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------- конфигурация (config.ini рядом с app.py) ----------

CFG = {"ors_key": "", "host": "127.0.0.1", "port": 5050,
        "admin_email": "admin@local", "admin_password": "admin",
        "tg_bot_token": "",
        "db_path": os.path.join(BASE_DIR, "dispatcher.db")}

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
    _db = _s.get("db_path", "").strip()
    if _db:
        CFG["db_path"] = _db if os.path.isabs(_db) else os.path.join(BASE_DIR, _db)

# Окружение перекрывает config.ini (деплой в облако: Render и т.п.)
CFG["ors_key"] = os.environ.get("ORS_KEY", "").strip() or CFG["ors_key"]
CFG["tg_bot_token"] = os.environ.get("TG_BOT_TOKEN", "").strip() or CFG["tg_bot_token"]
CFG["admin_email"] = (os.environ.get("ADMIN_EMAIL", "").strip() or CFG["admin_email"]).lower()
CFG["admin_password"] = os.environ.get("ADMIN_PASSWORD", "").strip() or CFG["admin_password"]
if os.environ.get("PORT", "").strip():  # PaaS выдаёт порт через PORT
    try:
        CFG["port"] = int(os.environ["PORT"])
        CFG["host"] = "0.0.0.0"
    except ValueError:
        pass

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

# ---------- логирование ----------

_log_path = os.path.join(BASE_DIR, "dispatcher.log")
logging.basicConfig(
    handlers=[RotatingFileHandler(_log_path, maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")],
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("waitress").setLevel(logging.WARNING)
log = logging.getLogger("dispatcher")

DEFAULT_DEPOT = {"address": "ул. Подгорная 12/1, Гомель", "lat": 52.44146, "lng": 31.01476}

STATE = {
    "depot": dict(DEFAULT_DEPOT),  # {"address", "lat", "lng"}
    "couriers": [],      # {"id", "name", "status": base|away|off, "color", "back_min"}
    "orders": [],        # {"id", "address", "lat", "lng"}
    "settings": {"speed_kmh": 60, "handover_min": 5, "max_orders": 5, "traffic": 1.25,
                 "lights_sec_per_km": 15, "auto_prio_min": 0, "reload_min": 10,
                 "hour_traffic": 1, "approach_center_min": 4, "approach_far_min": 2},
    "plan": None,
    "advice_mode": None,  # ручной выбор «ждать/не ждать» (now|split) до смены обстановки
    "color_seq": 0,      # монотонный счётчик: цвета не перемешиваются при удалениях
}

ROAD_FACTOR = 1.4  # запасной расчёт (если OSRM недоступен): прямая -> дорога
_PRIO_WEIGHT = 60  # вес минуты доставки приоритетного заказа (против 1 у обычного)
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
"""

_DB_MIGRATIONS = [
    # (таблица, колонка, DDL) — выполняется, если колонки ещё нет
    ("couriers", "back_min", "ALTER TABLE couriers ADD COLUMN back_min INTEGER DEFAULT 15"),
    ("history", "courier", "ALTER TABLE history ADD COLUMN courier TEXT DEFAULT ''"),
    ("orders", "prio", "ALTER TABLE orders ADD COLUMN prio INTEGER DEFAULT 0"),
    ("orders", "deadline", "ALTER TABLE orders ADD COLUMN deadline TEXT DEFAULT ''"),
    ("history", "deadline", "ALTER TABLE history ADD COLUMN deadline TEXT DEFAULT ''"),
    ("couriers", "tg_chat_id", "ALTER TABLE couriers ADD COLUMN tg_chat_id TEXT DEFAULT ''"),
    ("orders", "status", "ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'"),
    ("orders", "assigned", "ALTER TABLE orders ADD COLUMN assigned TEXT NOT NULL DEFAULT ''"),
    ("orders", "out_at", "ALTER TABLE orders ADD COLUMN out_at TEXT NOT NULL DEFAULT ''"),
]


def _db():
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DB_SCHEMA)  # идемпотентно; переживает удаление файла на ходу
    for table, column, ddl in _DB_MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(ddl)
    return conn


def _persist_meta():
    with _db_lock, _db() as c:
        c.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", [
            ("depot", json.dumps(STATE["depot"], ensure_ascii=False)),
            ("settings", json.dumps(STATE["settings"], ensure_ascii=False)),
            ("color_seq", str(STATE["color_seq"])),
            ("plan", json.dumps(STATE["plan"], ensure_ascii=False)
             if STATE.get("plan") else "")])


def _persist_couriers():
    with _db_lock, _db() as c:
        c.execute("DELETE FROM couriers")
        c.executemany(
            "INSERT INTO couriers(id, name, status, color, back_min, tg_chat_id) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            [(x["id"], x["name"], x["status"], x.get("color") or "",
              int(x.get("back_min", 15)), x.get("tg_chat_id") or "")
             for x in STATE["couriers"]])


def _persist_orders():
    with _db_lock, _db() as c:
        c.execute("DELETE FROM orders")
        c.executemany(
            "INSERT INTO orders(id, address, lat, lng, created_at, prio, deadline, "
            "status, assigned, out_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(x["id"], x["address"], x["lat"], x["lng"],
              x.get("created_at") or datetime.now().isoformat(timespec="seconds"),
              int(x.get("prio") or 0), x.get("deadline") or "",
              x.get("status") or "ready", x.get("assigned") or "",
              x.get("out_at") or "")
             for x in STATE["orders"]])


def _archive_order(order, outcome, courier=""):
    with _db_lock, _db() as c:
        c.execute("INSERT OR REPLACE INTO history VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (order["id"], order["address"], order["lat"], order["lng"],
                   order.get("created_at"),
                   datetime.now().isoformat(timespec="seconds"), outcome, courier,
                   order.get("deadline") or ""))


def _history_period(days=1):
    """Строки истории за последние `days` дней + сводка (новые — первыми)."""
    since = (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    try:
        with _db_lock, _db() as c:
            rows = c.execute(
                "SELECT * FROM history WHERE substr(closed_at, 1, 10) >= ? "
                "ORDER BY closed_at DESC LIMIT 500", (since,)).fetchall()
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


def _history_today():
    return _history_period(1)["summary"]


def load_state():
    """Загрузка сохранённого состояния при старте (данные переживают рестарт)."""
    with _db_lock, _db() as c:
        c.executescript(_DB_SCHEMA)
        meta = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")}
        couriers = [dict(r) for r in c.execute(
            "SELECT id, name, status, color, back_min, tg_chat_id FROM couriers ORDER BY rowid")]
        orders = [dict(r) for r in c.execute(
            "SELECT id, address, lat, lng, created_at, prio, deadline, "
            "status, assigned, out_at FROM orders ORDER BY rowid")]
    if meta.get("depot"):
        try:
            STATE["depot"] = json.loads(meta["depot"])
        except ValueError:
            pass
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
                          "tg_chat_id": r["tg_chat_id"] or ""}
                         for r in couriers]
    STATE["orders"] = [{**r, "prio": int(r.get("prio") or 0),
                        "deadline": r.get("deadline") or "",
                        "status": r.get("status") or "ready",
                        "assigned": r.get("assigned") or "",
                        "out_at": r.get("out_at") or ""} for r in orders]
    if meta.get("plan"):
        try:
            p = json.loads(meta["plan"])
            if isinstance(p, dict) and p.get("routes"):
                STATE["plan"] = p
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


def _approach_for(points, depot, settings):
    """Добавка на парковку/подъём по узлам: [0] = 0 (депо), дальше центр/пригород."""
    near = max(0, int(settings.get("approach_center_min", 4)))
    far = max(0, int(settings.get("approach_far_min", 2)))
    out = [0]
    for p in points[1:]:
        km = haversine_km(depot, p)
        out.append(near if km <= _APPROACH_RADIUS_KM else far)
    return out


def _eta_pass(stop_nodes, delay, matrix, settings, solved_dt, appr=None):
    """ETA остановок поездки (минуты от solved_dt) с почасовыми коэффициентами.

    Матрица построена с базовым коэффициентом traffic: дуга очищается от него
    и домножается на коэффициент часа фактического выезда на дугу.
    Возвращает (список ETA остановок, полная длительность поездки).
    """
    hourly = int(settings.get("hour_traffic", 1))
    base_traffic = max(1.0, float(settings.get("traffic", 1.3)))
    handover = max(0, int(settings["handover_min"]))
    t = float(delay)
    node = 0
    etas = []
    for g in stop_nodes:
        hour = (solved_dt + timedelta(minutes=t)).hour
        factor = _HOURLY_TRAFFIC.get(hour, 1.0) if hourly else 1.0
        travel = (matrix[node][g] - handover) / base_traffic * factor
        t += travel + handover + (appr[g] if appr else 0)
        etas.append(int(round(t)))
        node = g
    hour = (solved_dt + timedelta(minutes=t)).hour
    factor = _HOURLY_TRAFFIC.get(hour, 1.0) if hourly else 1.0
    t += matrix[node][0] / base_traffic * factor
    return etas, int(round(t))


def solve_plan(include_away=True, with_geometry=True):
    """Развозка. include_away=False — сценарий «не ждать»: только курьеры на базе.

    Заказов больше, чем влезает в один заезд (вместимость x курьеры), решается
    несколькими раундами: курьер вернётся на базу и поедет вторым заездом
    (задержка старта = длительность первого заезда + перезагрузка).
    """
    depot, settings = STATE["depot"], STATE["settings"]
    orders = [o for o in STATE["orders"] if (o.get("status") or "ready") == "ready"]
    couriers = [c for c in STATE["couriers"]
                if c["status"] == "base" or (include_away and c["status"] == "away")]
    if not depot:
        raise ValueError("Сначала задайте точку доставки (депо) на карте")
    if not orders:
        raise ValueError("Нет готовых заказов, добавьте хотя бы один")
    if not couriers:
        raise ValueError("Нет активных курьеров, добавьте курьера")

    points = [depot] + orders
    matrix, by_roads, distances, provider = build_time_matrix(points, settings)
    appr = _approach_for(points, depot, settings)
    solved_dt = datetime.now()
    now_hm = solved_dt.hour * 60 + solved_dt.minute
    handover = max(0, int(settings["handover_min"]))
    auto_prio = int(settings.get("auto_prio_min", 0) or 0)
    max_orders = int(settings["max_orders"])
    reload_min = max(0, int(settings.get("reload_min", 10)))

    deadline_rel, eff_prio, auto_flag = {}, {}, {}
    for i, o in enumerate(orders, start=1):
        deadline_rel[i] = _deadline_rel_min(o.get("deadline"), now_hm)
        age_min = 0
        try:
            age_min = int((solved_dt - datetime.fromisoformat(o["created_at"])
                           ).total_seconds() // 60)
        except (KeyError, ValueError, TypeError):
            pass
        auto_flag[i] = bool(auto_prio > 0 and age_min >= auto_prio)
        eff_prio[i] = bool(o.get("prio") or auto_flag[i])

    avail = {c["id"]: (max(0, int(c.get("back_min", 15))) if c["status"] == "away" else 0)
             for c in couriers}
    trips_by_cid = {}
    remaining = list(range(1, len(points)))

    for round_no in range(_MAX_ROUNDS):
        if not remaining:
            break
        sub = [0] + remaining            # узлы раунда: 0 = депо
        n_veh = min(len(couriers), len(remaining))
        round_couriers = couriers[:n_veh]
        manager = pywrapcp.RoutingIndexManager(len(sub), n_veh, 0)
        routing = pywrapcp.RoutingModel(manager)

        def make_cb(delay):
            def cb(from_index, to_index):
                i, j = sub[manager.IndexToNode(from_index)], sub[manager.IndexToNode(to_index)]
                return matrix[i][j] + (delay if i == 0 else 0) + (appr[j] if j else 0)
            return cb

        cb_idxs = [routing.RegisterTransitCallback(make_cb(avail[c["id"]]))
                   for c in round_couriers]
        for v, cb_idx in enumerate(cb_idxs):
            routing.SetArcCostEvaluatorOfVehicle(cb_idx, v)
        routing.AddConstantDimension(1, max_orders + 1, True, "Orders")
        routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 60, True, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.SetGlobalSpanCostCoefficient(200)

        # Штраф за ожидание доставки: обычный заказ 1 мин, приоритетный 60,
        # просрочка дедлайна 25 (дедлайн сильнее приоритета).
        for ln in range(1, len(sub)):
            g = sub[ln]
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
        for ln in range(1, len(sub)):
            routing.AddDisjunction([manager.NodeToIndex(ln)], 1_000_000)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
        params.time_limit.FromSeconds(4 if round_no == 0 else 2)
        solution = routing.SolveWithParameters(params)
        if solution is None:
            if round_no == 0:
                raise RuntimeError("OR-Tools не нашёл решение, попробуйте ещё раз")
            break
        round_stops = set()
        for v in range(n_veh):
            idx, stops = routing.Start(v), []
            while not routing.IsEnd(idx):
                node = manager.IndexToNode(idx)
                if node != 0:
                    stops.append(sub[node])
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
        trips, flat = [], []
        for tr in trips_raw:
            etas, total = _eta_pass(tr["stops"], tr["delay"], matrix, settings,
                                    solved_dt, appr)
            stops = []
            for g, eta in zip(tr["stops"], etas):
                o = orders[g - 1]
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
                seq = [0] + tr["stops"] + [0]
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
            "tg_chat_id": c.get("tg_chat_id") or ""})

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
    STATE["plan"] = plan
    if with_geometry:
        _attach_geometry(plan)
    return plan


def _attach_geometry(plan):
    """Геометрия маршрутов по дорогам (для отрисовки на карте), по заездам."""
    depot = STATE["depot"]
    for r in plan["routes"]:
        for t in r.get("trips", []):
            seq = [depot] + [{"lat": s["lat"], "lng": s["lng"]}
                             for s in t["stops"]] + [depot]
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


def _create_user(email, password, is_admin=0):
    email = email.strip().lower()
    if not re.match(r"^[^@\s]{1,64}@[^@\s]{1,190}$", email):
        raise ValueError("Некорректный email")
    if len(password or "") < 4:
        raise ValueError("Пароль: минимум 4 символа")
    uid = uuid.uuid4().hex[:8]
    with _db_lock, _db() as c:
        try:
            c.execute("INSERT INTO users(id, email, pwd_hash, is_admin, created_at) "
                      "VALUES(?, ?, ?, ?, ?)",
                      (uid, email, _hash_pwd(password), int(bool(is_admin)),
                       datetime.now().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            raise ValueError(f"Пользователь {email} уже существует") from None
    return uid


def ensure_default_admin():
    """Первый запуск: создаём администратора из config.ini (по умолчанию admin@local/admin)."""
    with _db_lock, _db() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if not n:
        _create_user(CFG["admin_email"], CFG["admin_password"], is_admin=1)
        log.info("created default admin %s — смените пароль после входа", CFG["admin_email"])


def _me():
    uid = session.get("uid")
    if not uid:
        return None
    with _db_lock, _db() as c:
        r = c.execute("SELECT id, email, is_admin FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(r) if r else None


def _admin_users():
    with _db_lock, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT id, email, is_admin, created_at FROM users ORDER BY created_at")]


_LOGIN_FAILS = {}  # ip -> [число ошибок, залочено_до_epoch]
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SEC = 60


@app.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (request.form.get("email") or data.get("email") or "").strip().lower()
    pwd = request.form.get("password") or data.get("password") or ""
    ip = request.remote_addr or "?"
    now = time.time()
    fails = _LOGIN_FAILS.get(ip)
    if len(_LOGIN_FAILS) > 1000:  # защита от роста в долгоживущем процессе
        _LOGIN_FAILS.clear()
    if fails and fails[1] > now:
        wait = int(fails[1] - now) + 1
        log.warning("login locked: %s (%ds left)", ip, wait)
        return render_template("login.html",
                               error=f"Слишком много попыток входа. Подождите {wait} с"), 429
    with _db_lock, _db() as c:
        r = c.execute("SELECT id, pwd_hash FROM users WHERE email = ?", (email,)).fetchone()
    if r and _verify_pwd(pwd, r["pwd_hash"]):
        session["uid"] = r["id"]
        session.permanent = True  # сессия живёт 12 ч, а не до закрытия браузера
        _LOGIN_FAILS.pop(ip, None)
        log.info("login ok: %s", email)
        return redirect(url_for("index"))
    time.sleep(0.3)  # тормозим перебор паролей
    n = (fails[0] + 1) if fails else 1
    _LOGIN_FAILS[ip] = [n, now + _LOGIN_LOCK_SEC] if n >= _LOGIN_MAX_FAILS else [n, 0]
    log.warning("login failed: %s (attempt %d from %s)", email or "?", n, ip)
    return render_template("login.html", error="Неверный email или пароль"), 401


@app.get("/login")
def login_page():
    if _me():
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.before_request
def _guard():
    if request.path in ("/login", "/health", "/favicon.ico") \
            or request.path.startswith("/static/"):
        return None
    if _me():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Требуется вход"}), 401
    return redirect(url_for("login_page"))


# ---------- управление пользователями (только админ) ----------

@app.post("/api/users")
def api_add_user():
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может добавлять пользователей"}), 403
    data = _json()
    try:
        _create_user(data.get("email") or "", data.get("password") or "",
                     is_admin=data.get("is_admin"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    log.info("user added by %s: %s", me["email"], data.get("email"))
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
    return _payload()


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
    return jsonify({"status": "ok", "time": datetime.now().isoformat(timespec="seconds")})


@app.errorhandler(Exception)
def _unhandled(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e  # 404/405 и прочие — как есть, без заворачивания в 500
    log.exception("unhandled error on %s %s", request.method, request.path)
    return jsonify({"error": f"Внутренняя ошибка: {e}"}), 500


# ---------- страницы ----------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           '<circle cx="16" cy="16" r="15" fill="#e8482b"/>'
           '<text x="16" y="22" font-size="17" text-anchor="middle">🛵</text></svg>')
    return svg, 200, {"Content-Type": "image/svg+xml"}


# ---------- api ----------

def _json():
    """Тело запроса как dict. Битый/пустой JSON => {} (валидацию делают ручки)."""
    return request.get_json(silent=True) or {}


def _payload():
    """Ответ после мутации: состояние + квота ORS + счётчики дня + текущий пользователь."""
    me = _me()
    return jsonify({**STATE, "ors": ors_status(), "today": _history_today(),
                    "cfg": {"tg": bool(CFG["tg_bot_token"])},
                    "me": me,
                    "users": _admin_users() if me and me["is_admin"] else []})


@app.get("/api/state")
def get_state():
    return _payload()


def _valid_latlng(lat, lng):
    return (-90 <= lat <= 90) and (-180 <= lng <= 180) and (lat != 0 or lng != 0)


@app.post("/api/depot")
def set_depot():
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны координаты депо (lat, lng)"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    address = (data.get("address") or "").strip() or reverse_geocode(lat, lng) or "Точка доставки"
    STATE["depot"] = {"address": address, "lat": lat, "lng": lng}
    _persist_meta()
    _schedule_resolve(drop_plan=True)
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
                              "tg_chat_id": ""})
    _persist_couriers(), _persist_meta()
    _schedule_resolve()
    return _payload()


@app.patch("/api/couriers/<cid>")
def upd_courier(cid):
    data = _json()
    for c in STATE["couriers"]:
        if c["id"] == cid:
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
                c["tg_chat_id"] = str(data["tg_chat_id"]).strip()[:64]
            _persist_couriers()
            _schedule_resolve(drop_plan=True)
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@app.delete("/api/couriers/<cid>")
def del_courier(cid):
    # его развозимые заказы возвращаются в очередь, чтобы не зависли
    for o in STATE["orders"]:
        if o.get("assigned") == cid and o.get("status") == "out":
            o["status"] = "ready"
            o["assigned"] = ""
            o["out_at"] = ""
    STATE["couriers"] = [c for c in STATE["couriers"] if c["id"] != cid]
    _persist_orders()
    _persist_couriers()
    _schedule_resolve(drop_plan=True)
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
    STATE["orders"].append({
        "id": oid,
        "address": (data.get("address") or "").strip() or reverse_geocode(lat, lng) or f"Заказ {oid[:4]}",
        "lat": lat, "lng": lng,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "prio": 1 if data.get("prio") else 0,
        "deadline": deadline if _deadline_rel_min(deadline, 0) is not None else "",
    })
    _persist_orders()
    _schedule_resolve()
    return _payload()


@app.patch("/api/orders/<oid>")
def patch_order(oid):
    """Переключение приоритета или дедлайна заказа."""
    data = _json()
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if "prio" in data:
        order["prio"] = 1 if data.get("prio") else 0
    if "deadline" in data:
        deadline = (data.get("deadline") or "").strip()
        if deadline and _deadline_rel_min(deadline, 0) is None:
            return jsonify({"error": "Дедлайн должен быть в формате ЧЧ:ММ"}), 400
        order["deadline"] = deadline
    _persist_orders()
    _schedule_resolve()
    return _payload()


@app.delete("/api/orders/<oid>")
def del_order(oid):
    """Отмена заказа (из очереди или из развозки) с записью в историю."""
    body = _json()
    outcome = body.get("outcome") if body.get("outcome") in ("delivered", "cancelled") \
        else "cancelled"
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if order:
        courier_name = ""
        if order.get("assigned"):
            c = next((c for c in STATE["couriers"] if c["id"] == order["assigned"]), None)
            courier_name = c["name"] if c else order["assigned"]
        elif outcome == "delivered" and STATE.get("plan"):
            for r in STATE["plan"].get("routes", []):
                if any(s["order_id"] == oid for s in r["stops"]):
                    courier_name = r["courier_name"]
                    break
        _archive_order(order, outcome, courier_name)
    STATE["orders"] = [o for o in STATE["orders"] if o["id"] != oid]
    _persist_orders()
    _schedule_resolve(drop_plan=True)
    return _payload()


def _patch_plan_after_assign(oids):
    """Убрать выданные заказы из плана и пересчитать ETA оставшихся курьеров.

    План остаётся рабочим: диспетчер сразу выдаёт маршруты следующим курьерам,
    не дожидаясь полного пересчёта. Матрица берётся из кэша (набор точек тот же,
    что при расчёте), ETA пересчитываются от текущего момента.
    """
    plan = STATE.get("plan")
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
    now = datetime.now()
    ready = [o for o in STATE["orders"] if (o.get("status") or "ready") == "ready"]
    points = [STATE["depot"]] + ready
    try:
        matrix, _, _, _ = build_time_matrix(points, STATE["settings"])
        node = {o["id"]: i + 1 for i, o in enumerate(ready)}
        appr = _approach_for(points, STATE["depot"], STATE["settings"])
        for r in plan["routes"]:
            if r["courier_id"] in touched:
                _retime_route(r, matrix, node, STATE["settings"], now, appr)
    except Exception:
        log.warning("plan retime after assign failed, агрегаты пересобраны без матрицы")
        for r in plan["routes"]:
            r["stops"] = [s for tr in r.get("trips", []) for s in tr["stops"]]
            r["count"] = len(r["stops"])
    plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
    if not plan["routes"]:
        # всё выдано: мёртвый пустой план никому не нужен, а фоновый
        # пересчёт подхватит заказы, которые могли остаться вне маршрутов
        STATE["plan"] = None
        _persist_meta()
        _schedule_resolve()
        return True
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» больше не соответствуют плану
    plan.pop("moved", None)
    _persist_meta()
    return True


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
    now = datetime.now().isoformat(timespec="seconds")
    given = 0
    for o in STATE["orders"]:
        if o["id"] in oids and (o.get("status") or "ready") == "ready":
            o["status"] = "out"
            o["assigned"] = cid
            o["out_at"] = now
            given += 1
    if not given:
        return jsonify({"error": "Заказы уже выданы или не найдены"}), 400
    _persist_orders()
    if not _patch_plan_after_assign(oids):
        _schedule_resolve(drop_plan=True)
    log.info("assign: %d заказ(ов) -> %s", given, courier["name"])
    return _payload()


@app.post("/api/orders/<oid>/return")
def return_order(oid):
    """Вернуть заказ из развозки в очередь готовых."""
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if (order.get("status") or "ready") != "out":
        return jsonify({"error": "Заказ не в развозке"}), 400
    order["status"] = "ready"
    order["assigned"] = ""
    order["out_at"] = ""
    _persist_orders()
    _schedule_resolve()
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
            _archive_order(o, "delivered", courier["name"])
            delivered += 1
    STATE["orders"] = [o for o in STATE["orders"]
                       if not (o.get("assigned") == cid and o.get("status") == "out")]
    courier["status"] = "base"
    courier["back_min"] = 0
    _persist_orders()
    _persist_couriers()
    _schedule_resolve(drop_plan=True)
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
    _schedule_resolve(drop_plan=True)
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
    try:
        t0 = time.time()
        plan = _compute_plan(mode)
        log.info("solve: %d routes, provider=%s, scenario=%s, %.1fs",
                 len(plan["routes"]), plan.get("provider"),
                 (plan.get("advice") or {}).get("chosen", "no-away"), time.time() - t0)
    except (ValueError, RuntimeError) as e:
        log.warning("solve failed: %s", e)
        return jsonify({"error": str(e)}), 400
    _persist_meta()
    return _payload()


def _compute_plan(mode="auto", advice=True):
    """Ядро расчёта (без HTTP): план + совет «ждать/не ждать».

    Вызывается кнопкой расчёта и фоновым авто-пересчётом (advice=False,
    чтобы не тратить квоту внешних сервисов на двойной расчёт).
    Ручной выбор сценария («Не ждать»/«Ждать») помнится до смены обстановки:
    авто-пересчёт его уважает и молча не сбрасывает.
    """
    global _resolve_timer
    with _resolve_lock:
        if _resolve_timer:  # ручной расчёт отменяет ожидающий авто-пересчёт:
            _resolve_timer.cancel()  # иначе фон перезапишет свежий план
            _resolve_timer = None
    if mode in ("now", "split"):
        STATE["advice_mode"] = mode
    mode = STATE.get("advice_mode") or mode
    plan_split = solve_plan()
    advice_obj = None
    away = [c for c in STATE["couriers"]
            if c["status"] == "away" and int(c.get("back_min", 15) or 0) > 0]
    has_base = any(c["status"] == "base" for c in STATE["couriers"])
    ready_n = sum(1 for o in STATE["orders"] if (o.get("status") or "ready") == "ready")
    scenario = away and has_base and ready_n >= 2
    if not scenario:
        STATE["advice_mode"] = None  # выбирать больше не из чего
    plan_now = None
    if (advice or mode == "now") and scenario:
        try:
            plan_now = solve_plan(include_away=False, with_geometry=False)
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
        STATE["plan"] = chosen
        if advice:
            advice_obj = {
                "recommend": recommend, "mode": mode,
                "chosen": "split" if chosen is plan_split else "now",
                "wait_couriers": [{"name": c["name"],
                                   "back_min": int(c.get("back_min", 15) or 0),
                                   "back_clock": (datetime.now() + timedelta(
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
    return STATE["plan"]


# ---------- авто-пересчёт плана (фон, с антидребезгом) ----------

_resolve_lock = threading.Lock()
_resolve_timer = None


def _schedule_resolve(delay=2.0, drop_plan=False):
    """Пересчитать план через `delay` секунд после последнего изменения.

    drop_plan=True — старый план точно невалиден (удаление заказа/курьера,
    смена депо): сбрасываем сразу. Иначе план показывается с пометкой
    «устарел» до готовности нового.
    """
    global _resolve_timer
    if drop_plan:
        STATE["plan"] = None
    elif STATE.get("plan"):
        STATE["plan"]["stale"] = True
    with _resolve_lock:
        if _resolve_timer:
            _resolve_timer.cancel()
        t = threading.Timer(delay, _auto_resolve)
        t.daemon = True
        t.start()
        _resolve_timer = t


def _auto_resolve():
    global _resolve_timer
    with _resolve_lock:
        _resolve_timer = None
    plan = STATE.get("plan")
    if plan and not plan.get("stale"):
        return  # свежий (нестейл) план трогать нельзя: ручной расчёт с советом
    try:
        _compute_plan(advice=False)
    except (ValueError, RuntimeError) as e:
        log.info("auto-resolve skipped: %s", e)
    except Exception:  # noqa: BLE001 — фон не должен ронять процесс
        log.exception("auto-resolve failed")
    try:
        _persist_meta()
    except sqlite3.Error:
        pass


def _days_param():
    """?days=1..31 из запроса (по умолчанию 1)."""
    try:
        return min(31, max(1, int(request.args.get("days", 1))))
    except ValueError:
        return 1


@app.get("/api/history")
def api_history():
    return jsonify(_history_period(_days_param()))


@app.get("/api/history/export")
def api_history_export():
    """CSV за период (BOM — чтобы Excel сразу открыл кириллицу)."""
    days = _days_param()
    rows = _history_period(days)["rows"]
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
                                   f"{datetime.now().strftime('%Y%m%d-%H%M')}.db")


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
    plan = STATE.get("plan")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not plan or not plan.get("routes"):
        return jsonify({"error": "Сначала рассчитайте план"}), 400
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    dst = next((r for r in plan["routes"] if r["courier_id"] == target), None)
    if not dst:
        return jsonify({"error": "Курьер отсутствует в плане"}), 400

    stop, src_id = None, None
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
            if hit:
                stop, src_id = hit, r["courier_id"]
                tr["stops"].remove(hit)
    if stop is None:
        return jsonify({"error": "Заказа нет в текущем плане"}), 400

    points = [STATE["depot"]] + STATE["orders"]
    matrix, _, _, _ = build_time_matrix(points, STATE["settings"])
    appr = _approach_for(points, STATE["depot"], STATE["settings"])
    node = {o["id"]: i + 1 for i, o in enumerate(STATE["orders"])}
    g_x = node[oid]

    best = None  # (удлинение, индекс заезда, позиция вставки)
    for ti, tr in enumerate(dst.get("trips", [])):
        seq = [0] + [node[s["order_id"]] for s in tr["stops"]] + [0]
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

    # пересчёт ETA затронутых курьеров от текущего момента
    now = datetime.now()
    touched = {src_id, dst["courier_id"]}
    for r in plan["routes"]:
        if r["courier_id"] in touched:
            _retime_route(r, matrix, node, STATE["settings"], now, appr)
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["moved"] = True
    log.info("plan move: %s -> %s (удлинение +%d мин)", oid, dst["courier_name"], best[0])
    _persist_meta()
    return _payload()


def _retime_route(route, matrix, node, settings, now_dt, appr=None):
    """Пересчёт ETA всех заездов курьера от now_dt. Меняет route на месте."""
    now_hm = now_dt.hour * 60 + now_dt.minute
    for tr in route.get("trips", []):
        seq = [node[s["order_id"]] for s in tr["stops"]]
        etas, total = _eta_pass(seq, tr["start_delay_min"], matrix, settings,
                                now_dt, appr)
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
    if not CFG["tg_bot_token"]:
        return jsonify({"error": "tg_bot_token не задан в config.ini"}), 400
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    route = next((r for r in (STATE.get("plan") or {}).get("routes", [])
                  if r["courier_id"] == cid), None)
    if not courier or not route:
        return jsonify({"error": "Курьер или маршрут не найден"}), 404
    chat = (courier.get("tg_chat_id") or "").strip()
    if not chat:
        return jsonify({"error": f"У курьера {courier['name']} не указан Telegram chat_id"}), 400
    lines = [f"🛵 Маршрут: {route['courier_name']} ({route['count']} заказ.)"]
    for ti, tr in enumerate(route["trips"], start=1):
        if len(route["trips"]) > 1:
            lines.append(f"Заезд {ti} (старт ≈{tr['start_clock']})")
        for i, s in enumerate(tr["stops"], start=1):
            lines.append(f"{i}. {s['address']} · ≈{s['eta_clock']}")
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines)}, timeout=10)
        data = resp.json()
    except requests.RequestException as e:
        return jsonify({"error": f"Telegram недоступен: {e}"}), 502
    if not data.get("ok"):
        return jsonify({"error": f"Telegram: {data.get('description', 'ошибка')}"}), 400
    log.info("telegram sent: %s (%s)", courier["name"], cid)
    return jsonify({"ok": True})


# ---------- статистика недели ----------

@app.get("/api/stats/week")
def stats_week():
    """Динамика за 7 дней: по дням и по курьерам + доля вовремя."""
    hist = _history_period(7)
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


@app.get("/report/day")
def report_day():
    """Отчёт дня для печати (Ctrl+P -> сохранить в PDF)."""
    hist = _history_period(1)
    return render_template("report_day.html", hist=hist,
                           plan=STATE.get("plan") or {},
                           today=datetime.now().strftime("%d.%m.%Y"),
                           now=datetime.now().strftime("%H:%M"))


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
                r = requests.post(url, data={"data": q}, headers=UA, timeout=25)
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


def search_nominatim(q, lat, lng, radius_km, bounded=True):
    params = {"q": q, "format": "json", "limit": 12, "addressdetails": 1,
              "accept-language": "ru", "countrycodes": "by"}
    if bounded:
        params["viewbox"] = _bbox(lat, lng, radius_km)
        params["bounded"] = 1
    resp = requests.get("https://nominatim.openstreetmap.org/search",
                        params=params, headers=UA, timeout=10)
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
                        params=params, headers=UA, timeout=10)
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
STREET_IDX = {"ts": 0.0, "names": {}, "ways": {}}  # names: lower(name)->(shown,lat,lng); ways: osm_id -> (local, ru)
STREET_IDX_TTL = 24 * 3600
STREET_IDX_LOCK = threading.Lock()


def _fill_street_index(network=True):
    """Тело загрузки индекса улиц; вызывать только под STREET_IDX_LOCK.
    network=False — только дисковый кэш (для запросов геокода, без задержек на сеть)."""
    if STREET_IDX["names"] and time.time() - STREET_IDX["ts"] < STREET_IDX_TTL:
        return STREET_IDX["names"]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "streets_cache.json")
    try:  # дисковый кэш переживает рестарты (формат v2: names + ways)
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < STREET_IDX_TTL:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("v") == 2:
                STREET_IDX["names"] = {k: tuple(v) for k, v in data["names"].items()}
                STREET_IDX["ways"] = {int(k): tuple(v) for k, v in data["ways"].items()}
                STREET_IDX["ts"] = os.path.getmtime(path)
                return STREET_IDX["names"]
    except Exception:  # noqa: BLE001
        pass
    s, w, n, e = GOMEL_BBOX
    q = (f'[out:json][timeout:25];'
         f'way["highway"~"^(residential|tertiary|secondary|primary|unclassified|living_street|pedestrian)$"]["name"]'
         f'({s},{w},{n},{e});out tags center 8000;')
    acc = {}  # lower -> [shown, sum_lat, sum_lng, cnt]
    for url in (OVERPASS_URLS if network else []):
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=60)
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
        app.logger.info("street index: %d улиц", len(STREET_IDX["names"]))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"v": 2, "names": STREET_IDX["names"], "ways": STREET_IDX["ways"]},
                          fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass
    return STREET_IDX["names"]


def _load_street_index():
    """Именованные улицы Гомеля одним Overpass-запросом; кэш в памяти, на диске и на сутки.
    Photon не индексирует name:ru, Nominatim не умеет префиксы — этот индекс закрывает both."""
    with STREET_IDX_LOCK:
        return _fill_street_index()


def _search_local_streets(token, limit=6):
    """Улицы города, начинающиеся на token (или содержащие его).
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
    for k, (shown, lat, lng) in STREET_IDX["names"].items():
        if k.startswith(t):
            rank = 0 if k == t else 1
        elif len(t) >= 4 and t in k:
            rank = 2
        else:
            continue
        hits.append((rank, len(k), shown, lat, lng))
    hits.sort(key=lambda x: (x[0], x[1]))
    return [(h[2], h[3], h[4]) for h in hits[:limit]]


def _tok(s):
    # ё -> е: «еремино» должен находить «Ерёмино»
    return [t.replace("ё", "е") for t in re.split(r"[^а-яёa-z0-9]+", (s or "").lower()) if len(t) >= 3]


@app.get("/api/geocode")
def geocode():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    lat, lng = _geocode_center()
    qnum = _extract_house(q)
    q_words = _tok(re.sub(r"\d+[а-яa-z]*", " ", q))  # слова запроса без номера дома
    try:
        v1, v2 = search_nominatim(q, lat, lng, 25.0), []
        # «еремино, школьная 13» Nominatim в таком порядке не находит —
        # пробуем «школьная 13, еремино» (улица+дом вперёд)
        if qnum and len(q_words) >= 2 and not v1:
            time.sleep(1.1)
            v1 = search_nominatim(f"{q_words[-1]} {qnum}, {' '.join(q_words[:-1])}", lat, lng, 25.0)
        try:
            v2 = search_photon(q, lat, lng)
        except Exception:  # noqa: BLE001
            pass

        def dist_of(it):
            return haversine_km({"lat": lat, "lng": lng}, {"lat": it["lat"], "lng": it["lng"]})

        # в городе мало совпадений — ищем по всей Гомельской области
        if sum(1 for it in v1 + v2 if dist_of(it) <= 25) < 4:
            time.sleep(1.1)  # политика Nominatim: не чаще запроса в секунду
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
        # все слова запроса обязаны найтись в адресе: «еремино, школьная 13»
        # не должно давать «Улукаўскі, ул. Школьная, 13» из запасного геокодера
        if q_words and scored:
            def _hit_all(x):
                hay = set(_tok(x["label"].split("(")[0]))
                return all(any(t == h or h.startswith(t) or t.startswith(h) for h in hay) for t in q_words)
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
        # Номер дома не найден геокодерами — интерполируем по соседним домам улицы
        if qnum and not exact_house:
            def _word_hit(it):
                hay = set(_tok(f"{it.get('road') or ''} {it.get('place') or ''}"))
                return sum(1 for t in q_words if t in hay or any(h.startswith(t) for h in hay))
            cands = [it for it in v1 if it["kind"] in ("street", "residential") and it.get("road")]
            cands.sort(key=lambda it: -_word_hit(it))
            street_hit = cands[0] if cands else None
            if street_hit:
                mnum = re.match(r"\d+", qnum)
                if mnum:
                    base_ru = _strip_street_type(street_hit["road"])
                    base_osm = osm_local_street_name(street_hit["osm_type"], street_hit["osm_id"]) or ""
                    houses = overpass_street_houses(base_osm or base_ru, street_hit["lat"], street_hit["lng"],
                                                    alt=base_ru if base_osm else None)
                    pt = interp_house(houses, int(mnum.group(0)))
                    if pt:
                        hlat, hlng, note = pt
                        base = _strip_street_type(street_hit["road"])
                        city = street_hit.get("place") or "Гомель"
                        suffix = f" ({note})" if note else ""
                        scored.insert(0, {"label": _place_label(base, city, qnum, True) + suffix,
                                          "lat": hlat, "lng": hlng, "_s": -1})
        if not scored and len(q_words) > 1:  # «бобовичи советская» -> «бобовичи»
            time.sleep(1.1)
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
        return jsonify([{k: v for k, v in x.items() if k != "_s"} for x in scored[:7]])
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Геокодер недоступен: {e}"}), 502


if __name__ == "__main__":
    load_state()
    ensure_default_admin()
    threading.Thread(target=_load_street_index, daemon=True).start()  # прогрев индекса улиц
    # после рестарта: заказы есть, плана нет -> пересчитать в фоне
    if STATE["orders"] and STATE["plan"] is None and any(
            c["status"] != "off" for c in STATE["couriers"]):
        _schedule_resolve()
    log.info("starting on %s:%s (auth=email, db=%s)", CFG["host"], CFG["port"], _db_path)
    try:
        from waitress import serve
        serve(app, host=CFG["host"], port=CFG["port"], threads=8)
    except ImportError:
        log.warning("waitress not installed — falling back to Flask dev server")
        app.run(host=CFG["host"], port=CFG["port"], debug=False, threaded=True)
