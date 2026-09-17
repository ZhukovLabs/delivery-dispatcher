"""Старт системы: восстановление состояния из БД, секрет сессий."""
import json
import os
import sqlite3
import uuid

from itsdangerous import URLSafeTimedSerializer

from .config import CFG, log
from .adapters.sqlite_repo import _DB_SCHEMA, _db, _db_lock, _persist_meta
from .state import STATE, _depot_view

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
    for key in ("tg_ask", "tg_deliv", "tg_pay"):
        # диалоги «доставлен?», «сумма оплаты» и трекеры простоя переживают
        # рестарт: без этого после каждого деплоя бот переспрашивал,
        # а нажатия кнопок на старых сообщениях попадали в «уже неактуально»
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
