"""Аутентификация: управление пользователями (только админ)."""
from fastapi import APIRouter

from .core import (CFG, ONLINE, STATE, _LOGIN_FAILS, _LOGIN_LOCK_SEC,
                   _LOGIN_MAX_FAILS, _ONLINE_LOCK, _admin_users, _bump,
                   _check_user_contact, _check_user_email, _create_user, _db,
                   _db_lock, _drop_online, _hash_pwd, _me, _now, _payload,
                   _touch_online, _verify_pwd, log)
from .shims import _json, flaskish, jsonify, request, session
import sqlite3
import time
import uuid

r = APIRouter()

# ---------- управление пользователями (только админ) ----------

@r.post("/api/users")
@flaskish
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


@r.delete("/api/users/{uid}")
@flaskish
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


@r.put("/api/users/{uid}")
@flaskish
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


@r.put("/api/users/{uid}/password")
@flaskish
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


@r.post("/api/password")
@flaskish
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
