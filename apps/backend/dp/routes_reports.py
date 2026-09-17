"""История, экспорт, бэкап, статистика недели и дня."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta

import requests
from fastapi import APIRouter

from .core import (CFG, MAX_POINTS, PALETTE, STATE, STATUSES, _approach_map,
                   _archive_order, _attach_geometry, _bump, _courier_plan,
                   _courier_speed, _db, _db_path, _db_lock, _deadline_rel_min,
                   _check_user_contact, _depot_view, _esc, _eta_pass, _ev,
                   _flip_return_route, _history_period, _courier_day_stats,
                   _home_point, _HOURLY_TRAFFIC, _invalidate_plan, _me,
                   _my_point, _now, _obj_point, _payload, _persist_couriers,
                   _persist_meta, _persist_orders, _plan_for, _plural,
                   _plans_lock, _tg_callback, _tg_handle_update, _tg_send,
                   _valid_latlng, build_time_matrix, haversine_km, log,
                   routing_geometry, solve_plan, _simplify_poly)
from .geocode import reverse_geocode
from .shims import _json, flaskish, jsonify, request, send_file, session
from .routes_solve import _days_param

r = APIRouter()
_solving_lock = threading.Lock()

@r.get("/api/history")
@flaskish
def api_history():
    return jsonify(_history_period(_days_param(), point_id=_my_point()))


@r.get("/api/history/export")
@flaskish
def api_history_export():
    """CSV за период (BOM — чтобы Excel сразу открыл кириллицу)."""
    days = _days_param()
    rows = _history_period(days, point_id=_my_point())["rows"]
    csv = ["Время закрытия;Адрес;Курьер;Исход;Цикл, мин;Оплата;Сумма"]
    for r in rows:
        csv.append(";".join(str(x if x is not None else "")
                            for x in (r["closed_at"].replace("T", " "), r["address"],
                                      r["courier"],
                                      "выдан" if r["outcome"] == "delivered" else "отменён",
                                      r["cycle_min"],
                                      {"cash": "наличные", "card": "карта"}
                                      .get(r.get("payment"), ""),
                                      r.get("pay_amount"))))
    body = "\ufeff" + "\n".join(csv) + "\n"
    return (body, 200, {"Content-Type": "text/csv; charset=utf-8",
                        "Content-Disposition": f"attachment; filename=history-{days}d.csv"})


@r.get("/api/backup")
@flaskish
def api_backup():
    """Снимок базы данных (только админ) — скачивание файла."""
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может делать резервную копию"}), 403
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        with sqlite3.connect(_db_path, timeout=10) as src, \
                sqlite3.connect(tmp.name) as dst:
            src.backup(dst)
    except Exception:
        os.unlink(tmp.name)
        raise
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    return FileResponse(tmp.name, filename=f"dispatcher-backup-"
                       f"{_now().strftime('%Y%m%d-%H%M')}.db",
                       background=BackgroundTask(_unlink_quiet, tmp.name))


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass




@r.get("/api/stats/week")
@flaskish
def stats_week():
    """Динамика за 7 дней: по дням и по курьерам + доля вовремя."""
    hist = _history_period(7, point_id=_my_point())
    days, couriers = {}, {}
    on_time = on_time_total = 0
    for r in hist["rows"]:
        day = (r["closed_at"] or "")[:10]
        d = days.setdefault(day, {"day": day, "delivered": 0, "cancelled": 0,
                                  "cycles": []})
        if r["outcome"] == "delivered":
            d["delivered"] += 1
            if r["cycle_min"] is not None:
                d["cycles"].append(r["cycle_min"])
            name = r["courier"] or "–"
            c = couriers.setdefault(name, {"courier": name, "delivered": 0,
                                           "cycles": []})
            c["delivered"] += 1
            if r["cycle_min"] is not None:
                c["cycles"].append(r["cycle_min"])
            dl = r.get("deadline")
            if dl:
                # дедлайн принадлежит дню создания заказа: закрытие после
                # полуночи против вечернего дедлайна — это вовремя,
                # а не «00:10 < 23:50» по голым часам
                try:
                    dl_dt = datetime.fromisoformat(
                        (r.get("created_at") or "")[:10] + "T" + dl)
                    closed_dt = datetime.fromisoformat(r["closed_at"])
                    on_time_total += 1
                    if closed_dt <= dl_dt:
                        on_time += 1
                except (ValueError, TypeError):
                    pass
        else:
            d["cancelled"] += 1
    out_days = [{"day": d["day"], "delivered": d["delivered"],
                 "cancelled": d["cancelled"],
                 "avg_cycle_min": round(sum(d["cycles"]) / len(d["cycles"]))
                 if d["cycles"] else None}
                for d in sorted(days.values(), key=lambda x: x["day"])]
    out_cour = [{"courier": c["courier"], "delivered": c["delivered"],
                 "avg_cycle_min": round(sum(c["cycles"]) / len(c["cycles"]))
                 if c["cycles"] else None}
                for c in sorted(couriers.values(), key=lambda x: -x["delivered"])]
    return jsonify({"days": out_days, "couriers": out_cour,
                    "on_time": on_time, "on_time_total": on_time_total})


@r.get("/api/stats/couriers")
@flaskish
def stats_couriers_day():
    """Статистика за день по курьерам (своё депо): км, взятые/доставленные/
    отказы, время на работе."""
    return jsonify(_courier_day_stats(point_id=_my_point()))


@r.get("/api/report/day")
@flaskish
def api_report_day():
    """Данные отчёта дня (печатную версию рендерит фронт)."""
    hist = _history_period(1, point_id=_my_point())
    plan = _plan_for(_my_point()) or {}
    routes = [{"courier_name": r.get("courier_name"), "status": r.get("status"),
               "stops": [s.get("address") for s in (r.get("stops") or [])]}
              for r in (plan.get("routes") or [])]
    return jsonify({"rows": hist["rows"], "summary": hist.get("summary") or {},
                    "routes": routes,
                    "courier_stats": _courier_day_stats(point_id=_my_point())["rows"],
                    "today": _now().strftime("%d.%m.%Y"),
                    "now": _now().strftime("%H:%M")})

