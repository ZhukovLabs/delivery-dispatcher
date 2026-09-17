"""Аутентификация: служебные ручки."""
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

# ---------- служебные ----------

@r.get("/health")
@flaskish
def health():
    return jsonify({"status": "ok", "time": _now().isoformat(timespec="seconds")})




# ---------- api ----------

@r.get("/")
@flaskish
def root():
    return jsonify(service="dispatcher-api", time=_now().isoformat())
