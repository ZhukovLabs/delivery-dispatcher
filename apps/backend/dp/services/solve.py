# -*- coding: utf-8 -*-
"""Сценарий solve: оркестрация расчёта плана развозки (OR-Tools).

solve_plan — точка входа: собирает контекст (solve_ctx), решает
модель (solve_run), собирает план (solve_build) и, если включён
почасовой трафик, делает контрольный второй проход с обновлёнными
коэффициентами часов. Принимает только по честной метрике _quality.
"""
import os
from datetime import timedelta

from ..config import log
from ..domain.model import _HOURLY_TRAFFIC
from ..planstate import _plans_lock
from ..state import STATE
from .solve_build import _make_plan
from .solve_ctx import _build_context
from .solve_run import _solve_once
from .solve_geom import _attach_geometry


def _quality(p):
    """Честная метрика плана: (неразвезено, опоздания, средняя, последняя)."""
    return (p["unassigned"],
            sum(st["late_min"] for r in p["routes"] for st in r["stops"]),
            p["avg_delivery_min"], p["last_delivery_min"])


def solve_plan(include_away=True, with_geometry=True, helpers=None, force=None,
               point_id=None):
    """Развозка ОДНОГО депо (point_id; None = точка вызывающего).

    include_away=False — сценарий «не ждать»: только курьеры на базе.
    helpers: {courier_id: point_id} — разовая «помощь»: курьер в этом
    расчёте стартует с чужой точки выдачи и берёт максимум один заказ.
    force — id курьеров, перетащенных в план вручную (первый заезд
    обязан взять заказ). Полная документация модели — в solve_ctx.
    """
    ctx = _build_context(include_away=include_away, helpers=helpers,
                         force=force, point_id=point_id)
    solved_dt = ctx.solved_dt
    hourly_on = ctx.hourly_on

    # Бюджет оптимизации масштабируем от размера задачи: одна задача на все
    # заезды (раньше — до трёх моделей по раундам), поэтому берём бюджет
    # крупнее раундового, но меньше старой суммы. DP_TIME_MS — override для
    # тестов/диагностики (задаёт бюджет ПРОХОДА); мусорное значение молча
    # игнорируем. Недетерминизм GLS (бюджет в стенных часах, состав пачек
    # немного плавает между прогонами) принят осознанно: детерминированные
    # альтернативы (solution_limit) дают менее предсказуемое от размера
    # задачи качество.
    big = len(ctx.orders) > 12
    try:
        budget_ms = int(os.environ.get("DP_TIME_MS", ""))
    except ValueError:
        budget_ms = 0
    per_pass_ms = budget_ms or (5000 if big else 1500)

    solution, routing, manager, time_dim = _solve_once(ctx, per_pass_ms)
    plan, start_s = _make_plan(ctx, solution, routing, manager, time_dim)

    # Второй проход решателя: hour_f копии закреплён по нижней границе
    # старта, а фактический старт (цепочка заездов, удлинение первых
    # заездов) может попасть в другой час — дуги поздних копий оценены не
    # тем часом (пик/межпик различаются до ×1.5). Обновляем hour_f по
    # фактическим стартам и решаем ещё раз с вдвое меньшим бюджетом;
    # принимаем только при лучшей ЧЕСТНОЙ метрике.
    if hourly_on:
        refreshed = {}
        for vi, v in enumerate(ctx.veh):
            s = start_s.get(vi)
            if s is None:
                continue
            hour = (solved_dt + timedelta(seconds=round(s))).hour
            f = _HOURLY_TRAFFIC.get(hour, 1.0)
            if abs(f - v["hour_f"]) > 1e-6:
                refreshed[vi] = f
        if refreshed:
            for vi, f in refreshed.items():
                ctx.veh[vi]["hour_f"] = f
            try:
                sol2, rout2, man2, td2 = _solve_once(ctx, max(1, per_pass_ms // 2))
                plan2, _ = _make_plan(ctx, sol2, rout2, man2, td2)
                if _quality(plan2) < _quality(plan):
                    log.debug("solve: второй проход принят "
                              "(%d копий сменили почасовой коэффициент)",
                              len(refreshed))
                    plan = plan2
                else:
                    log.debug("solve: второй проход отклонён по честной метрике")
            except RuntimeError:
                log.warning("solve: второй проход не нашёл решение, "
                            "оставлен первый")

    warnings = []
    if plan["unassigned"]:
        warnings.append(f"Не поместились в маршруты: {plan['unassigned']} "
                        "заказ(ов) — лимит заездов на курьера исчерпан")
    if not ctx.by_roads:
        warnings.append("Роутеры недоступны — время и километры оценены по прямой")
    if warnings:
        plan["warnings"] = warnings
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    if all_etas:
        plan["last_delivery_clock"] = (solved_dt + timedelta(
            minutes=plan["last_delivery_min"])).strftime("%H:%M")
    with _plans_lock:  # установка плана атомарна с выдачами/возвратами
        STATE["plans"][ctx.point_id] = plan
    if with_geometry:
        _attach_geometry(plan)
    return plan
