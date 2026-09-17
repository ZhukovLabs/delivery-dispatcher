"""Персистентность: SQLite (WAL), схема/миграции, история, дневные замеры."""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from .config import CFG, _now, log
from .state import STATE, _obj_point

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
    ("history", "payment", "ALTER TABLE history ADD COLUMN payment TEXT NOT NULL DEFAULT ''"),
    ("history", "pay_amount", "ALTER TABLE history ADD COLUMN pay_amount REAL"),
    ("history", "out_at", "ALTER TABLE history ADD COLUMN out_at TEXT NOT NULL DEFAULT ''"),
]


_db_conn: sqlite3.Connection | None = None


def _db_connect():
    """Одно соединение на процесс: WAL + synchronous=NORMAL.

    Раньше каждый вызов _db() открывал файл заново, прогонял 7 CREATE TABLE
    и 19 PRAGMA и делал commit с полным fsync (journal=DELETE) — на слабом
    CPU под вечерней нагрузкой это складывалось в секунды на мутацию.
    WAL пишет без fsync на каждый коммит, а чтения больше не ждут писателей.
    """
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    conn = sqlite3.connect(_db_path, timeout=10, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_DB_SCHEMA)  # идемпотентно
        for table, column, ddl in _DB_MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(ddl)
        conn.commit()
    except BaseException:
        conn.close()
        raise
    _db_conn = conn
    return conn


@contextmanager
def _db():
    global _db_conn
    if _db_conn is not None and not os.path.exists(_db_path):
        # файл удалили на ходу (открытый fd на Linux продолжал бы писать в
        # безымянный inode молча): закрываемся — следующий вызов пересоздаст
        # файл и схему, как это делал executescript на каждый вызов раньше
        try:
            _db_conn.close()
        finally:
            _db_conn = None
    conn = _db_connect()
    try:
        yield conn
        conn.commit()
    except sqlite3.DatabaseError:
        # база сломалась — закрываем, следующий вызов откроет заново
        # и пересоздаст схему
        try:
            conn.close()
        finally:
            _db_conn = None
        raise
    except Exception:
        conn.rollback()
        raise


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
             if STATE.get("tg_deliv") else ""),
            ("tg_pay", json.dumps(STATE["tg_pay"], ensure_ascii=False)
             if STATE.get("tg_pay") else "")])
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
                  " courier, deadline, point_id, reason, out_at) "
                  "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (order["id"], order["address"], order["lat"], order["lng"],
                   order.get("created_at"),
                   closed, outcome, courier,
                   order.get("deadline") or "", _obj_point(order), reason,
                   order.get("out_at") or ""))


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
                    "reason": r["reason"] or "",
                    "created_at": r["created_at"] or "",
                    "payment": r["payment"] or "",
                    "pay_amount": r["pay_amount"]})
    pays = [(r["payment"], r["pay_amount"] or 0) for r in out if r["payment"]]
    summary = {"delivered": sum(1 for r in out if r["outcome"] == "delivered"),
               "cancelled": sum(1 for r in out if r["outcome"] == "cancelled"),
               "avg_cycle_min": round(sum(cycles) / len(cycles)) if cycles else None,
               # аналитика оплат (собирается после закрытия заказа курьером)
               "pay_cash": sum(1 for m, _ in pays if m == "cash"),
               "pay_cash_sum": round(sum(a for m, a in pays if m == "cash"), 2),
               "pay_card": sum(1 for m, _ in pays if m == "card"),
               "pay_card_sum": round(sum(a for m, a in pays if m == "card"), 2)}
    return {"rows": out, "summary": summary}


def _history_today(point_id=None):
    return _history_period(1, point_id=point_id)["summary"]


def _courier_day_stats(point_id=None, day=None):
    """Статистика курьеров за день (по умолчанию сегодня, своё депо).

    Строки истории группируются по имени курьера (пустое имя — заказ
    отменён из очереди, не считается «взятым»). Километраж — из живого
    гео (speed_day), время на работе — от первой выдачи до последнего
    закрытия (или текущего момента, пока заказы ещё в развозке).
    Оплаты — сколько закрытых заказов курьера оплачено наличными/картой.
    """
    day = day or _now().strftime("%Y-%m-%d")
    now_s = _now().isoformat(timespec="seconds")
    where, args = "substr(closed_at, 1, 10) = ?", [day]
    if point_id:
        first = (STATE.get("points") or [{}])[0].get("id") or ""
        where += " AND (point_id = ? OR (point_id = '' AND ? = ?))"
        args += [point_id, first, point_id]
    try:
        with _db_lock, _db() as c:
            rows = c.execute(
                f"SELECT courier, outcome, closed_at, out_at, payment, pay_amount "
                f"FROM history WHERE {where}", args).fetchall()
            km_by_cid = {r["courier_id"]: r["geo_m"] or 0 for r in c.execute(
                "SELECT courier_id, geo_m FROM speed_day WHERE day = ?",
                (day,)).fetchall()}
    except sqlite3.Error:
        return {"day": day, "rows": []}
    st = {}

    def rec(name):
        return st.setdefault(name, {"taken": 0, "delivered": 0, "cancelled": 0,
                                    "pay_cash": 0, "pay_card": 0, "revenue": 0.0,
                                    "first_out": "", "last_close": ""})

    for r in rows:
        name = (r["courier"] or "").strip()
        if not name:
            continue
        d = rec(name)
        d["taken"] += 1
        if r["outcome"] == "delivered":
            d["delivered"] += 1
        elif r["outcome"] == "cancelled":
            d["cancelled"] += 1
        if r["payment"] == "cash":
            d["pay_cash"] += 1
        elif r["payment"] == "card":
            d["pay_card"] += 1
        if r["payment"] in ("cash", "card"):
            try:
                d["revenue"] += float(r["pay_amount"] or 0)
            except (TypeError, ValueError):
                pass
        if r["out_at"] and (not d["first_out"] or r["out_at"] < d["first_out"]):
            d["first_out"] = r["out_at"]
        if r["closed_at"] > d["last_close"]:
            d["last_close"] = r["closed_at"]
    # живые развозки: взяты сегодня, ещё не закрыты
    by_cid = {x["id"]: x for x in STATE["couriers"]}
    for o in STATE["orders"]:
        if (o.get("status") or "ready") != "out" or not o.get("assigned"):
            continue
        courier = by_cid.get(o["assigned"])
        if not courier or (point_id and _obj_point(o) != point_id):
            continue
        d = rec(courier["name"])
        d["taken"] += 1
        oa = o.get("out_at") or ""
        if oa and (not d["first_out"] or oa < d["first_out"]):
            d["first_out"] = oa
        d["last_close"] = max(d["last_close"], now_s)
    out = []
    for name, d in st.items():
        cid = next((x["id"] for x in STATE["couriers"] if x["name"] == name), "")
        work_min = None
        if d["first_out"] and d["last_close"] > d["first_out"]:
            try:
                work_min = int((datetime.fromisoformat(d["last_close"])
                                - datetime.fromisoformat(d["first_out"])
                                ).total_seconds() // 60)
            except ValueError:
                pass
        out.append({"courier": name,
                    "km": round((km_by_cid.get(cid, 0)) / 1000, 1),
                    "taken": d["taken"], "delivered": d["delivered"],
                    "cancelled": d["cancelled"],
                    "pay_cash": d["pay_cash"], "pay_card": d["pay_card"],
                    "revenue": round(d["revenue"], 2),
                    "work_min": work_min})
    out.sort(key=lambda x: (-x["delivered"], -x["taken"], x["courier"]))
    return {"day": day, "rows": out}
def _speed_add(courier_id, day=None, geo_m=0.0, geo_s=0.0, del_n=0, del_min=0.0):
    day = day or _now().strftime("%Y-%m-%d")
    with _db_lock, _db() as c:
        c.execute(
            "INSERT INTO speed_day(courier_id, day, geo_m, geo_s, del_n, del_min) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(courier_id, day) DO UPDATE SET "
            "geo_m = geo_m + excluded.geo_m, geo_s = geo_s + excluded.geo_s, "
            "del_n = del_n + excluded.del_n, del_min = del_min + excluded.del_min",
            (courier_id, day, geo_m, geo_s, del_n, del_min))
