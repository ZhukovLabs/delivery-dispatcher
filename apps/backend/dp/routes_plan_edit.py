"""Ручные правки плана: перенос стопа другому курьеру, снятие, ретайминг."""
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
from .routes_retiming import _retiming_matrix
from .routes_plan_helpers import _retime_route

r = APIRouter()
_solving_lock = threading.Lock()

# ---------- ручные правки плана: перенести остановку другому курьеру ----------

@r.post("/api/plan/move")
@flaskish
def plan_move():
    """Перенос заказа к другому курьеру в готовом плане (без полного пересчёта).

    Остановка снимается со своего места и вставляется в лучшую позицию лучшего
    заезда целевого курьера (минимальное удлинение маршрута). ETA обоих курьеров
    пересчитываются от текущего момента.
    """
    data = _json()
    oid, target = data.get("order_id"), data.get("to_courier")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    plan = _plan_for(_my_point())
    if not plan or not plan.get("routes"):
        return jsonify({"error": "Сначала рассчитайте план"}), 400
    dst = next((r for r in plan["routes"] if r["courier_id"] == target), None)
    if not dst:
        return jsonify({"error": "Курьер отсутствует в плане"}), 400
    dst_courier = next((c for c in STATE["couriers"] if c["id"] == target), None)
    opid = _obj_point(order)
    if dst_courier and opid and opid != _home_point(dst_courier)["id"]:
        pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == opid),
                  "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — перенести можно только "
                                 f"курьеру этой точки"}), 400

    stop, src_id = None, None
    with _plans_lock:  # атомарно с выдачами: снятый стоп не теряется
        for r in plan["routes"]:
            for tr in r.get("trips", []):
                hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
                if hit:
                    stop, src_id = hit, r["courier_id"]
                    tr["stops"].remove(hit)
        if stop is None:
            return jsonify({"error": "Заказа нет в текущем плане"}), 400

        touched_routes = [r for r in plan["routes"]
                          if r["courier_id"] in {src_id, dst["courier_id"]}]
        matrix, node, appr_home, home_of = _retiming_matrix(plan, _my_point(),
                                                            touched_routes)
        h_dst = home_of[dst["courier_id"]]
        if oid not in node:
            return jsonify({"error": "Заказа нет в наборе точек плана — "
                                     "пересчитайте план"}), 400
        g_x = node[oid]

        best = None  # (удлинение, индекс заезда, позиция вставки)
        now = _now()
        hour_on = bool(int(STATE["settings"].get("hour_traffic", 1)))
        base_traffic = max(1.0, float(STATE["settings"].get("traffic", 1.3)))
        handover_s = max(0, int(STATE["settings"]["handover_min"])) * 60
        n_homes = len(appr_home)

        def _travel(u, w, f):
            """Дорожная часть дуги (сек) в масштабе часа f: базовый traffic
            из матрицы заменяется коэффициентом часа подъезда — вставка в
            разные позиции попадает в разные часы пик. Вручение/подъезд —
            константа вставки, на РАНГ позиций не влияют, не добавляем."""
            t = matrix[u][w]
            if w >= n_homes:
                t -= handover_s
            return t / base_traffic * f

        for ti, tr in enumerate(dst.get("trips", [])):
            # лимит max_orders соблюдает только решатель; ручной перенос из
            # «Готовых адресов» разрешён и сверх лимита — диспетчер видит,
            # что делает (ограничение вернётся при следующем пересчёте)
            seq = [h_dst] + [node[s["order_id"]] for s in tr["stops"]] + [h_dst]
            for pos in range(1, len(seq)):
                a, b = seq[pos - 1], seq[pos]
                if hour_on:
                    # час подъезда к месту вставки — по ETA предыдущей остановки;
                    # ETA плана привязаны к моменту своего последнего пересчёта
                    # (eta_at) — добавляем возраст, иначе час пик занижается
                    # на возраст плана
                    if pos == 1:
                        t_prev = tr.get("start_delay_min") or 0
                    else:
                        t_prev = tr["stops"][pos - 2].get("eta_min") or 0
                    try:
                        eta_at = datetime.fromisoformat(
                            tr.get("eta_at") or plan.get("anchored_at")
                            or plan["solved_at"])
                        age_min = (now - eta_at).total_seconds() / 60.0
                    except (ValueError, TypeError):
                        age_min = 0.0
                    hour = (now + timedelta(minutes=t_prev + age_min)).hour
                    f = _HOURLY_TRAFFIC.get(hour, 1.0)
            else:
                f = 1.0
            delta = _travel(a, g_x, f) + _travel(g_x, b, f) - _travel(a, b, f)
            if best is None or delta < best[0]:
                best = (delta, ti, pos - 1)
        if best is None and not dst.get("trips"):
            # у цели не было заездов — создаём первый
            dst["trips"] = [{"stops": [], "total_min": 0, "start_delay_min": 0,
                             "start_clock": "", "end_clock": "", "distance_km": None}]
            best = (0, 0, 0)
        if best is None:  # недостижимо: заезды без лимита всегда принимают вставку
            return jsonify({"error": f"У «{dst['courier_name']}» нет заездов — "
                                     "перенесите другому или пересчитайте план"}), 400
        dst["trips"][best[1]]["stops"].insert(best[2], stop)
        order["pin"] = target  # закрепляем и на будущие пересчёты плана
        _persist_orders()  # pin — часть заказа, без этого перенос терялся бы
                           # при рестарте (persist_meta хранит только планы)

        # пересчёт ETA затронутых курьеров от текущего момента
        touched = {src_id, dst["courier_id"]}
        try:
            for r in touched_routes:
                if r["courier_id"] in touched:
                    h = home_of[r["courier_id"]]
                    _retime_route(r, matrix, node, STATE["settings"], now,
                                  appr_home[h], h)
        except Exception:
            log.warning("plan move retime failed, ETA оставлены как были")
        else:
            plan["anchored_at"] = now.isoformat(timespec="seconds")
        all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
        plan["last_delivery_min"] = max(all_etas, default=0)
        plan["last_delivery_clock"] = ((now + timedelta(
            minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
        plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
        plan["advice"] = None  # сценарии «ждать/не ждать» после переноса не актуальны
        plan["moved"] = True
        log.info("plan move: %s -> %s (удлинение +%.0f мин)",
                 oid, dst["courier_name"], best[0] / 60)
        _persist_meta()
        _bump()
    return _payload()


@r.post("/api/plan/unassign")
@flaskish
def plan_unassign():
    """Снять заказ с маршрута (drag остановки наружу, не к другому курьеру).

    Остановка убирается из заезда, pin заказа сбрасывается (иначе пересчёт
    вернул бы его тому же курьеру), ETA оставшихся курьеров пересчитываются
    от текущего момента. Заказ возвращается в очередь готовых.
    """
    data = _json()
    oid = data.get("order_id")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if (order.get("status") or "ready") != "ready":
        return jsonify({"error": "Заказ уже в развозке — сначала верните его в очередь"}), 400
    pid = _my_point()
    plan = _plan_for(pid)
    if not plan or not plan.get("routes"):
        return jsonify({"error": "Сначала рассчитайте план"}), 400
    src_id = None
    with _plans_lock:  # атомарно с выдачами: снятие не теряется
        for r in plan["routes"]:
            for tr in r.get("trips", []):
                hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
                if hit:
                    tr["stops"].remove(hit)
                    src_id = r["courier_id"]
            r["trips"] = [tr for tr in r.get("trips", []) if tr["stops"]]
        if src_id is None:
            return jsonify({"error": "Заказа нет в текущем плане"}), 400
        plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
        order["pin"] = ""  # заказ снова свободен: без этого пересчёт вернул бы его
        _persist_orders()
        now = _now()
        if not plan["routes"]:
            # весь план состоял из этого заказа — пустой план не нужен
            STATE["plans"].pop(pid, None)
            _persist_meta()
            _invalidate_plan(pid=pid)
            _ev("disp", f"снял «{order.get('address') or oid}» с маршрута")
            _bump()
            return _payload()
        try:
            matrix, node, appr_home, home_of = _retiming_matrix(plan, pid,
                                                                plan["routes"])
            for r in plan["routes"]:
                h = home_of[r["courier_id"]]
                _retime_route(r, matrix, node, STATE["settings"], now,
                              appr_home[h], h)
            plan["anchored_at"] = now.isoformat(timespec="seconds")
        except Exception:
            log.warning("plan unassign retime failed, ETA оставлены как были")
        all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
        plan["last_delivery_min"] = max(all_etas, default=0)
        plan["last_delivery_clock"] = ((now + timedelta(
            minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
        plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
        plan["advice"] = None  # сценарии «ждать/не ждать» после снятия не актуальны
        plan["moved"] = True
        log.info("plan unassign: %s снят с маршрута %s", oid, src_id)
        _persist_meta()
        _ev("disp", f"снял «{order.get('address') or oid}» с маршрута")
        _bump()
    return _payload()


