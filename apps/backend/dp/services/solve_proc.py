# -*- coding: utf-8 -*-
"""Изоляция решателя OR-Tools в отдельном процессе.

SWIG-биндинг pywrapcp держит GIL ВСЁ время SolveWithParameters: чистый
C++-поиск на матрицах вообще не возвращается в Python, и четырёхсекундный
проход полностью замораживает потоки сервера (замер: джиттер sleep(20мс)
до 3.9с; прежние Python-колбэки дуги это только маскировали постоянной
перекачкой GIL ценой миллионов вызовов). Поэтому считаем в отдельном
процессе: тяжёлые импорты платятся один раз, а на два ядра ноутбука
приходится максимум два поиска одновременно (сценарий «ждать/не ждать»
считает оба плана параллельно). ctx и план — пикабельные словари/списки.
"""
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from concurrent.futures.process import BrokenProcessPool

from ..config import log

_pool = None


def _solve_in_proc(ctx, per_pass_ms, hourly_on, solved_dt):
    """Оба прохода решателя — исполняется в дочернем процессе."""
    from datetime import timedelta
    import time as _t

    from .solve_build import _make_plan
    from .solve_run import _solve_once
    from ..domain.model import _HOURLY_TRAFFIC

    _t0 = _t.perf_counter()
    solution, routing, manager, time_dim = _solve_once(ctx, per_pass_ms)
    _t1 = _t.perf_counter()
    plan, start_s = _make_plan(ctx, solution, routing, manager, time_dim)
    log.info("solve proc: pass1=%.2fs plan=%.2fs", _t1 - _t0,
             _t.perf_counter() - _t1)

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
                    plan = plan2
            except RuntimeError:
                log.warning("solve: второй проход не нашёл решение, "
                            "оставлен первый")
    return plan, start_s


def _quality(p):
    """Честная метрика плана (unassigned, опоздания, среднее, хвост)."""
    return (p.get("unassigned") or 0,
            round(sum(r.get("late_min") or 0 for r in p["routes"]), 2),
            round(p.get("avg_min") or 0, 2),
            round(p.get("last_delivery_min") or 0, 2))


def _get_pool():
    global _pool
    if _pool is None:
        # два воркера: сценарий «ждать/не ждать» считает оба плана
        # параллельно (замер на прод-ноуте: 22 заказа, 3.0с → 1.5с), как и
        # раньше в потоках; на два ядра ноутбука приходится максимум два
        # поиска одновременно
        _pool = ProcessPoolExecutor(max_workers=2)
    return _pool


def _reset_pool(reason: str) -> None:
    """Пересоздать пул: умерший ИЛИ зависший воркер держит слот — без
    пересоздания следующий расчёт встанет в очередь за ним навсегда."""
    global _pool
    log.warning("solve: пул решателя пересоздан (%s)", reason)
    try:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001 — повторное убийство пула не должно ронять вызов
        pass
    _pool = None


def solve_isolated(ctx, per_pass_ms, hourly_on, solved_dt):
    """Прогон обоих проходов в дочернем процессе (с одним ретраем).

    Таймаут и смерть процесса решателя одинаково лечатся пересозданием
    пула. Окружение DP_SOLVE_INPROC=1 заставляет считать в текущем
    процессе (отладка; сервер при этом замрёт на время поиска).
    """
    if os.environ.get("DP_SOLVE_INPROC", "").strip() not in ("", "0", "no"):
        return _solve_in_proc(ctx, per_pass_ms, hourly_on, solved_dt)
    for attempt in (1, 2):
        try:
            fut = _get_pool().submit(_solve_in_proc, ctx, per_pass_ms,
                                     hourly_on, solved_dt)
            # бюджет ×2 прохода + запас на старт/пиклинг/геометрию плана
            return fut.result(timeout=per_pass_ms / 1000 * 3 + 20)
        except (BrokenProcessPool, _FutureTimeout):
            _reset_pool(f"попытка %d не удалась" % attempt)
            if attempt == 2:
                raise RuntimeError("Процесс решателя не отвечает, попробуйте ещё раз")
