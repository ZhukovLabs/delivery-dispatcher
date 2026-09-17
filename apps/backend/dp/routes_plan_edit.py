"""Ручные правки плана: перенос стопа другому курьеру, снятие, ретайминг."""
import threading

from fastapi import APIRouter

from .core import (STATE, _bump, _ev, _home_point, _invalidate_plan, log,
                   _my_point, _now, _obj_point, _payload, _persist_meta,
                   _persist_orders, _plan_for, _plans_lock)
from .shims import _json, flaskish, jsonify
from .routes_retiming import _retiming_matrix
from .routes_plan_helpers import _retime_route
from .routes_plan_edit_helpers import (_best_insert, _drop_order_stops,
                                       _pop_stop, _recalc_plan_stats)

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

    with _plans_lock:  # атомарно с выдачами: снятый стоп не теряется
        stop, src_id = _pop_stop(plan, oid)
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
        now = _now()
        best = _best_insert(plan, dst, matrix, node, appr_home, h_dst, oid,
                            now, STATE["settings"])
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
        _recalc_plan_stats(plan, now)
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
    with _plans_lock:  # атомарно с выдачами: снятие не теряется
        src_id = _drop_order_stops(plan, oid)
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
        _recalc_plan_stats(plan, now)
        plan["advice"] = None  # сценарии «ждать/не ждать» после снятия не актуальны
        plan["moved"] = True
        log.info("plan unassign: %s снят с маршрута %s", oid, src_id)
        _persist_meta()
        _ev("disp", f"снял «{order.get('address') or oid}» с маршрута")
        _bump()
    return _payload()


