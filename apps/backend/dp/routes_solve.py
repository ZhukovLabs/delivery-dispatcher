"""Расчёт развозки: настройки, совет «ждать/не ждать», запуск solve."""
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
from .routes_solve_helpers import (_begin_solving, _compute_plan,
                                   _days_param)

r = APIRouter()

@r.post("/api/settings")
@flaskish
def set_settings():
    data = _json()
    s = STATE["settings"]
    try:
        s["speed_kmh"] = min(120, max(5, int(data.get("speed_kmh", s["speed_kmh"]))))
        s["handover_min"] = min(60, max(0, int(data.get("handover_min", s["handover_min"]))))
        s["max_orders"] = min(50, max(1, int(data.get("max_orders", s["max_orders"]))))
        s["traffic"] = round(min(3.0, max(1.0, float(data.get("traffic", s["traffic"])))), 2)
        s["lights_sec_per_km"] = round(min(60.0, max(0.0, float(data.get("lights_sec_per_km", s["lights_sec_per_km"])))), 0)
        s["auto_prio_min"] = min(240, max(0, int(data.get("auto_prio_min", s.get("auto_prio_min", 0)))))
        s["reload_min"] = min(120, max(0, int(data.get("reload_min", s.get("reload_min", 10)))))
        s["hour_traffic"] = 1 if data.get("hour_traffic", s.get("hour_traffic", 1)) else 0
        s["approach_center_min"] = min(15, max(0, int(data.get("approach_center_min", s.get("approach_center_min", 4)))))
        s["approach_far_min"] = min(15, max(0, int(data.get("approach_far_min", s.get("approach_far_min", 2)))))
    except (TypeError, ValueError):
        return jsonify({"error": "Параметры должны быть числами"}), 400
    _persist_meta()
    # настройки влияют на будущие расчёты: показанный план помечаем
    # «устарел», но не выбрасываем с экрана
    _invalidate_plan()
    return _payload()


@r.post("/api/solve")
@flaskish
def solve():
    body = _json()
    mode = body.get("mode") if body.get("mode") in ("auto", "split", "now") else "auto"
    force = [x for x in (body.get("force") or []) if isinstance(x, str)]
    myp = _my_point()
    # один расчёт на депо: второй диспетчер получает отлуп, все клиенты депо
    # видят блокирующий оверлей, пока расчёт идёт
    if not _begin_solving(myp):
        return jsonify({"error": "Расчёт развозки уже идёт — подождите окончания"}), 409
    _bump()
    try:
        if force:
            for cid in force:
                c = next((c for c in STATE["couriers"] if c["id"] == cid), None)
                if not c:
                    return jsonify({"error": "Курьер не найден"}), 404
                pid = _home_point(c)["id"]
                if pid != myp:
                    pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == pid),
                              "другой точки")
                    return jsonify({"error": f"«{c['name']}» работает с точкой «{pt}» — "
                                             f"он не может участвовать в плане вашего депо"}), 400
                if not any((o.get("status") or "ready") == "ready"
                           and _obj_point(o) == pid
                           for o in STATE["orders"]):
                    pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == pid),
                              "его точки")
                    return jsonify({"error": f"У точки «{pt}» нет готовых заказов — "
                                             f"«{c['name']}» не сможет участвовать в плане"}), 400
        try:
            t0 = time.time()
            plan = _compute_plan(mode, force=force, point_id=myp)
            log.info("solve[%s]: %d routes, provider=%s, scenario=%s, %.1fs",
                     myp[:6], len(plan["routes"]), plan.get("provider"),
                     (plan.get("advice") or {}).get("chosen", "no-away"), time.time() - t0)
        except (ValueError, RuntimeError) as e:
            log.warning("solve failed: %s", e)
            return jsonify({"error": str(e)}), 400
        _persist_meta()
        counts = ", ".join(f'{r["courier_name"]}: {r["count"]}'
                           for r in plan["routes"]) or "нечего везти"
        _ev("disp", f"рассчитал развозку — {counts}")
        # снимаем флаг ДО сборки ответа: он несёт solving=false и сам
        # разблокирует UI запустившего расчёт (WS-бродкаст — резервный путь)
        STATE["solving"][myp] = False
        return _payload()
    finally:
        if STATE["solving"].get(myp):
            STATE["solving"][myp] = False
            _bump()
