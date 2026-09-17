"""Хелперы расчёта: захват «решаем», ядро _compute_plan, параметр days."""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from .core import (STATE, _attach_geometry, _bump, _my_point, _now,
                   _obj_point, _plans_lock, log, solve_plan)
from .shims import request

_solving_lock = threading.Lock()

def _begin_solving(pid):
    """Атомарный захват «расчёт идёт» для депо. True — успели, False — уже идёт.

    Check-then-set без лока был гонкой потоков FastAPI (два запроса могли
    пройти проверку одновременно и запустить два solve одного депо).
    """
    with _solving_lock:
        if STATE.setdefault("solving", {}).get(pid):
            return False
        STATE["solving"][pid] = True
        return True

# Пороги «стоит ли ждать возвращающегося курьера»: ожидание выгодно при
# выигрыше ≥5 мин до последней доставки или ≥2 мин к среднему времени доставки.
_GAIN_LAST_MIN = 5
_GAIN_AVG_MIN = 2


def _advice_side(plan):
    counts = ", ".join(f'{r["courier_name"]}: {r["count"]}' for r in plan["routes"])
    return {"counts": counts or "–",
            "last_clock": plan["last_delivery_clock"],
            "last_min": plan["last_delivery_min"],
            "avg_min": plan["avg_delivery_min"]}


def _compute_plan(mode="auto", advice=True, force=None, point_id=None):
    """Ядро расчёта (без HTTP): план + совет «ждать/не ждать» своего депо.

    Вызывается ТОЛЬКО вручную: кнопка «Рассчитать» и выбор сценария совета.
    Ручной выбор («Не ждать»/«Ждать») помнится до смены обстановки.
    """
    point_id = point_id or _my_point()
    if mode in ("now", "split"):
        STATE["advice_modes"][point_id] = mode
    mode = STATE["advice_modes"].get(point_id) or mode
    mine = [c for c in STATE["couriers"] if _obj_point(c) == point_id]
    away = [c for c in mine
            if c["status"] == "away" and int(c.get("back_min", 15) or 0) > 0]
    has_base = any(c["status"] == "base" for c in mine)
    ready_n = sum(1 for o in STATE["orders"]
                  if (o.get("status") or "ready") == "ready" and _obj_point(o) == point_id)
    scenario = away and has_base and ready_n >= 2
    if not scenario:
        STATE["advice_modes"].pop(point_id, None)  # выбирать больше не из чего
    advice_obj = None
    plan_now = None
    if (advice or mode == "now") and scenario:
        # сценарии независимы — считаем параллельно (замер на прод-ноуте:
        # 22 заказа, 3.0с → 1.5с; OR-Tools отпускает GIL)
        with ThreadPoolExecutor(2) as ex:
            f_split = ex.submit(solve_plan, force=force, point_id=point_id)
            f_now = ex.submit(solve_plan, include_away=False,
                              with_geometry=False, force=force,
                              point_id=point_id)
        plan_split = f_split.result()
        try:
            plan_now = f_now.result()
        except (ValueError, RuntimeError):
            plan_now = None
    else:
        plan_split = solve_plan(force=force, point_id=point_id)
    if plan_now is not None:
        g_last = plan_now["last_delivery_min"] - plan_split["last_delivery_min"]
        g_avg = plan_now["avg_delivery_min"] - plan_split["avg_delivery_min"]
        recommend = ("split" if (g_last >= _GAIN_LAST_MIN or
                                 g_avg >= _GAIN_AVG_MIN) else "now")
        chosen = {"split": plan_split, "now": plan_now}[
            mode if mode != "auto" else recommend]
        if chosen is plan_now:
            _attach_geometry(plan_now)
        with _plans_lock:  # установка сценарного плана атомарна с выдачами
            STATE["plans"][point_id] = chosen
        if advice:
            advice_obj = {
                "recommend": recommend, "mode": mode,
                "chosen": "split" if chosen is plan_split else "now",
                "wait_couriers": [{"name": c["name"],
                                   "back_min": int(c.get("back_min", 15) or 0),
                                   "back_clock": (_now() + timedelta(
                                       minutes=int(c.get("back_min", 15) or 0))
                                                  ).strftime("%H:%M")} for c in away],
                "now": _advice_side(plan_now),
                "split": _advice_side(plan_split),
                "gain_last_min": g_last, "gain_avg_min": g_avg,
                "held": [{"courier": r["courier_name"], "address": s["address"]}
                         for r in plan_split["routes"] if r["status"] == "away"
                         for s in r["stops"]],
            }
            chosen["advice"] = advice_obj
    _bump()  # план готов — сообщаем всем открытым консолям
    return STATE["plans"].get(point_id)


# ---------- инвалидация плана (расчёт — только вручную, по кнопке) ----------


def _days_param():
    """?days=1..31 из запроса (по умолчанию 1)."""
    try:
        return min(31, max(1, int(request.args.get("days", 1))))
    except ValueError:
        return 1
