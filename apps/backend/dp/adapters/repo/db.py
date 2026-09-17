import os
import sqlite3
import threading
from contextlib import contextmanager

from ...config import CFG

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
