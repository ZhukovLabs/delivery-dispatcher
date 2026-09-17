import json

from ...config import _now
from ...state import STATE
from .db import _db, _db_lock


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
