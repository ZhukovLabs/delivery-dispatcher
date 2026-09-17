"""Пользователи: пароли (PBKDF2), регистрация, текущий админ, лок-аут."""
import hashlib
import hmac
import os
import re
import sqlite3
import time
import uuid

from .config import CFG, _now, log
from .adapters.sqlite_repo import _db, _db_lock
from .shims import session
from .state import STATE

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
_PBKDF_ROUNDS = 200_000


