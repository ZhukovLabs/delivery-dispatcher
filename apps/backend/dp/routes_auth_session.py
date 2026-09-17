"""Аутентификация: вход/выход, сессии, рабочая точка."""
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

from .routes_auth_helpers import _client_ip

r = APIRouter()

@r.post("/login")
@flaskish
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    pwd = data.get("password") or ""
    ip = _client_ip()
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
        from .shims import sign_session
        return jsonify(ok=True, token=sign_session(dict(session)))
    time.sleep(0.3)  # тормозим перебор паролей
    n = (fails[0] + 1) if fails else 1
    _LOGIN_FAILS[ip] = [n, now + _LOGIN_LOCK_SEC] if n >= _LOGIN_MAX_FAILS else [n, 0]
    log.warning("login failed: %s (attempt %d from %s)", email or "?", n, ip)
    return jsonify(error="Неверный email или пароль"), 401


@r.post("/api/login")
@flaskish
def login_api():
    """Алиас для SPA (Next.js проксирует /api/* сюда)."""
    return login()


@r.get("/api/logout")
@flaskish
def logout_api():
    _drop_online()
    session.clear()
    return jsonify(ok=True)


@r.get("/api/ws-token")
@flaskish
def api_ws_token():
    """Токен для WS-handshake (socket.io auth): подпись текущей сессии.

    Нужен после F5: login-токен из ответа /api/login живёт только в памяти
    страницы, а cookie — HttpOnly и из JS не читается.
    """
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    from .shims import sign_session
    return jsonify(token=sign_session(dict(session)))


@r.post("/api/workpoint")
@flaskish
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
    _touch_online(pid)  # и обновит, и восстановит запись в ONLINE, если стёрлась
    _bump()  # другие админы увидят обновлённые счётчики на карточках точек
    return jsonify(ok=True)
