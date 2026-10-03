# -*- coding: utf-8 -*-
"""Сценарий solve: оркестрация расчёта плана развозки (OR-Tools).

solve_plan — точка входа: собирает контекст (solve_ctx), решает
модель (solve_run), собирает план (solve_build) и, если включён
почасовой трафик, делает контрольный второй проход с обновлёнными
коэффициентами часов. Принимает только по честной метрике _quality.
"""
import os
from datetime import timedelta

from ..planstate import _plans_lock
from ..state import STATE
from .solve_ctx import _build_context
from .solve_geom import _attach_geometry
from .solve_proc import solve_isolated


def solve_plan(include_away=True, with_geometry=True, helpers=None, force=None,
               point_id=None):
    """Развозка ОДНОГО депо (point_id; None = точка вызывающего).

    include_away=False — сценарий «не ждать»: только курьеры на базе.
    helpers: {courier_id: point_id} — разовая «помощь»: курьер в этом
    расчёте стартует с чужой точки выдачи и берёт максимум один заказ.
    force — id курьеров, перетащенных в план вручную (первый заезд
    обязан взять заказ; если везти нечего — ограничения нет, иначе
    модель несовместима). Полная документация модели — в solve_ctx.
    """
    ctx = _build_context(include_away=include_away, helpers=helpers,
                         force=force, point_id=point_id)
    solved_dt = ctx.solved_dt
    hourly_on = ctx.hourly_on

    # Бюджет оптимизации масштабируем от размера задачи: одна задача на все
    # заезды (раньше — до трёх моделей по раундам), поэтому берём бюджет
    # крупнее раундового, но меньше старой суммы. Недетерминизм GLS (бюджет
    # в стенных часах, состав пачек немного плавает между прогонами) принят
    # осознанно: детерминированные альтернативы (solution_limit) дают менее
    # предсказуемое от размера задачи качество.
    big = len(ctx.orders) > 12
    # DP_TIME_MS — override бюджета прохода (тесты/диагностика/слабое железо):
    # одно число — бюджет для любого размера, два через запятую — «малый,большой»
    # (до 12 заказов / свыше). Мусорное значение молча игнорируется.
    try:
        _ov = [int(x) for x in
               os.environ.get("DP_TIME_MS", "").replace(";", ",").split(",") if x.strip()]
    except ValueError:
        _ov = []
    if big:
        per_pass_ms = (_ov[1] if len(_ov) > 1 else _ov[0]) if _ov else 5000
    else:
        per_pass_ms = _ov[0] if _ov else 1500

    # Оба прохода — в отдельном процессе: SWIG-биндинг OR-Tools держит
    # GIL всё время C++-поиска и замораживает потоки сервера (детали в
    # solve_proc). Второй проход (обновление почасовых коэффициентов)
    # выполняется там же и принимается только по честной метрике.
    plan, start_s = solve_isolated(ctx, per_pass_ms, hourly_on, solved_dt)

    warnings = list(ctx.pin_warnings)
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
