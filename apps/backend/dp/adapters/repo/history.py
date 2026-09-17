import sqlite3
from datetime import datetime, timedelta

from ...config import _now
from ...state import STATE, _obj_point
from .db import _db, _db_lock
from .speed_day import _speed_add


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
