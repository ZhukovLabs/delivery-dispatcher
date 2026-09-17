import sqlite3
from datetime import datetime

from ...config import _now
from ...state import STATE, _obj_point
from .db import _db, _db_lock


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
