"""История, экспорт CSV, бэкап базы."""
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
