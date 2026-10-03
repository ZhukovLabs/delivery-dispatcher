# -*- coding: utf-8 -*-
"""Сценарий solve, фаза 2: одна OR-Tools-модель по контексту и её решение.

Между проходами меняются только hour_f копий (обновление почасовых
коэффициентов по фактическим стартам) — модель и ограничения
идентичны, поэтому всё состояние берётся из ctx.
"""
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..config import log
from ..domain.model import (_ASAP_WEIGHT, _DROP_PENALTY, _LATE_WEIGHT,
                            _SPAN_WEIGHT)
from ..state import _PRIO_WEIGHT


def _veh_transit_matrix(ctx, v):
    """Матрица транзитных времён для одной копии курьера (в СЕКУНДАХ).

    Дублирует формулу стоимостей дуг 1:1 (см. докстринги веток ниже), но
    выполняется ЦЕЛИКОМ на этапе сборки: поиск OR-Tools читает готовые
    int-матрицы C++-стороне без единого захода в Python. Живой колбэк
    дуги держал GIL миллионами вызовов и замораживал весь сервер на время
    каждого прохода решателя (замер py-spy: активный поток — единственный,
    остальные ручки стоят по 2-5с).
    """
    matrix, K = ctx.matrix, ctx.K
    handover_s, base_traffic = ctx.handover_s, ctx.base_traffic
    n = len(ctx.points)
    m = [[0] * n for _ in range(n)]
    for i in range(n):
        row = matrix[i]
        out = m[i]
        for j in range(n):
            arc = row[j]
            if j >= K and arc > handover_s:
                # базовый трафик уже зашит в матрицу (traffic=1.3 к среднему);
                # почасовой коэффициент ЗАМЕНЯЕТ его часовую часть, поэтому
                # сначала делим на базу — как в _eta_pass. Иначе факторы
                # перемножаются (1.3×0.9) и дедлайны сравниваются с завышенными
                # кумуляторами: решатель «паникует» у дедлайнов.
                arc = int(round((arc - handover_s) / base_traffic
                                * v["factor"] * v["hour_f"])) + handover_s
            elif j < K and arc > 0:
                # возвратная дуга (заказ -> дом): вручения нет, но трафик,
                # час и скорость курьера действуют так же — раньше дуга шла
                # по базовой матрице без пересчёта (решатель недооценивал
                # возвраты в час пик и переоценивал ночью)
                arc = int(round(arc / base_traffic
                                * v["factor"] * v["hour_f"]))
            cost = arc
            if j >= K:
                cost += v["appr"].get(j, 0) * 60  # парковка/подъезд: мин -> сек
                if j not in v["allowed"]:      # чужая точка/чужой pin — везти нельзя
                    cost += 1_000_000_000
            out[j] = cost
    return m


def _solve_once(ctx, budget_ms):
    import time as _t
    _t_start = _t.perf_counter()
    veh = ctx.veh
    reload_s = ctx.reload_s
    K, pinned_n = ctx.K, ctx.pinned_n
    max_orders, helper_ids, force_ids = ctx.max_orders, ctx.helper_ids, ctx.force_ids
    deadline_rel, eff_prio, points = ctx.deadline_rel, ctx.eff_prio, ctx.points
    manager = pywrapcp.RoutingIndexManager(len(points), len(veh),
                                           [v["home"] for v in veh],
                                           [v["home"] for v in veh])
    routing = pywrapcp.RoutingModel(manager)

    # Транзиты и целевые стоимости — готовыми матрицами на копию курьера:
    # RegisterTransitMatrix оценивает дуги в C++ без Python-колбэков (GIL
    # свободен, сервер отвечает во время расчёта). Матрица транзита —
    # чистое время дуги; целевая добавляет цену активации заезда k>0
    # (возврат + перезагрузка) на СТАРТОВУЮ дугу [home][*]: домашний узел
    # не встречается посередине маршрута, так что строка home однозначно
    # задаёт именно первый шаг копии.
    cb_idxs = []      # транзиты времени (без цены активации)
    for vi, v in enumerate(veh):
        tm = _veh_transit_matrix(ctx, v)
        cb_idxs.append(routing.RegisterTransitMatrix(tm))
        if v["k"] > 0:
            # фиксированная цена активации заезда k>0: без неё PCI не
            # различает копии одного курьера и может посадить единственный
            # заезд в k1/k2, завышая ETА на полчаса, а одиночные переносы
            # не вытащат (промежуточное расщепление дороже). Только в
            # ЦЕЛЕВУЮ функцию — в размерность времени надбавка не идёт
            # (нижние границы стартов уже учитывают перезагрузку).
            cm = [list(r) for r in tm]
            hr = cm[v["home"]]
            for j in range(len(hr)):
                hr[j] += reload_s
            routing.SetArcCostEvaluatorOfVehicle(
                routing.RegisterTransitMatrix(cm), vi)
        else:
            routing.SetArcCostEvaluatorOfVehicle(
                routing.RegisterTransitMatrix(tm), vi)

    # ёмкость размерности — с запасом под расширенные лимиты закреплений
    cap_extra = max(pinned_n.values()) if pinned_n else 0
    routing.AddConstantDimension(1, max_orders + 1 + cap_extra, True, "Orders")
    orders_dim = routing.GetDimensionOrDie("Orders")
    first_copy = {}   # courier_id -> индекс первой копии (заезд k = 0)
    for vi, v in enumerate(veh):
        cid = v["courier"]["id"]
        first = first_copy.setdefault(cid, vi)
        # базовый лимит max_orders; курьеру с закреплениями — плюс их число
        orders_dim.CumulVar(routing.End(vi)).SetMax(
            max_orders + 1 + pinned_n.get(cid, 0))
        if cid in helper_ids:
            # размерность считает дуги: простой = 1, один заказ = 2.
            # НЕ БОЛЕЕ одного (не «ровно»): если все заказы точки
            # закреплены за другими, пустой помощник не должен ломать
            # модель («ровно один» при пустом allowed -> несовместимость
            # -> «OR-Tools не нашёл решение» для всего расчёта)
            orders_dim.CumulVar(routing.End(vi)).SetMax(2)
        elif vi == first and cid in force_ids and v["allowed"]:
            # перетащен в план вручную: первый заезд обязан взять заказ
            # (не применяем при пустом allowed — та же несовместимость)
            orders_dim.CumulVar(routing.End(vi)).SetMin(2)

    routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 3600, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    # Давление на длительность — ПЕР-ВЕХИКЛЬНЫЙ span (конец − старт копии), а не
    # глобальный max(End) − min(Start): в модели с копиями-заездами глобальный
    # span ломает поиск — решатель выравнивает старты копий и прячет заказы в
    # поздние копии (воспроизведено изолированно: глобальный span -> первый
    # заезд пустой, старты +45 мин, просроченный дедлайн в хвосте; пер-
    # вехикльный -> первый заезд загружен, старты на нижних границах). Пустая
    # копия при этом даёт span 0 и не шумит.
    for vi in range(len(veh)):
        time_dim.SetSpanCostCoefficientForVehicle(_SPAN_WEIGHT, vi)

    # Нижние границы стартов копий: заезд k не раньше возврата курьера +
    # (перезагрузка + холостой заезд) × k. Старт ПЕРВОЙ копии фиксируем
    # жёстко на релизе — ETА и почасовой коэффициент считаются от него;
    # для остальных копий достаточно нижней границы: на старт нет давления
    # в цель, локальный поиск кладёт его на границу.
    for vi, v in enumerate(veh):
        start = time_dim.CumulVar(routing.Start(vi))
        if v["k"] == 0:
            start.SetRange(v["start_min_s"], v["start_min_s"])
        else:
            start.SetMin(v["start_min_s"])

    # Штрафы ожидания доставки (кумуляторы в СЕКУНДАХ): обычный заказ
    # 5/сек, просрочка дедлайна 25/сек — на размерности Time; приоритет
    # 60/сек «поскорее» — на отдельной размерности Urg, чтобы заказ с
    # приоритетом И дедлайном давился ОБЕИМИ
    # (раньше elif терял приоритет у заказов с дедлайном).
    has_prio = any(eff_prio.values())
    if has_prio:
        routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 3600, False, "Urg")
        urg_dim = routing.GetDimensionOrDie("Urg")
    for ln in range(K, len(points)):
        idx = manager.NodeToIndex(ln)
        if deadline_rel[ln] is not None:
            time_dim.SetCumulVarSoftUpperBound(idx, max(0, deadline_rel[ln]) * 60,
                                               _LATE_WEIGHT)
        elif not eff_prio[ln]:
            time_dim.SetCumulVarSoftUpperBound(idx, 0, _ASAP_WEIGHT)
        if eff_prio[ln] and has_prio:
            urg_dim.SetCumulVarSoftUpperBound(idx, 0, _PRIO_WEIGHT)

    # Разрешаем оставить заказ вне плана: дроп-визит с подавляющим штрафом
    # (чужой заказ стоит миллиард и к курьеру другой точки не попадёт).
    # Вместимость кончилась — роняем самый «дешёвый» для бизнеса заказ:
    # приоритетный держится до последнего (×10), с дедлайном — предпоследним
    # (×5), иначе решателю выгоднее уронить именно «дорогие минуты».
    for ln in range(K, len(points)):
        pen = _DROP_PENALTY
        if eff_prio[ln]:
            pen *= 10
        elif deadline_rel[ln] is not None:
            pen *= 5
        routing.AddDisjunction([manager.NodeToIndex(ln)], pen)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromMilliseconds(budget_ms)
    _t0 = _t.perf_counter()
    solution = routing.SolveWithParameters(params)
    log.info("solve phase: build=%.2fs search=%.2fs (budget=%dms, veh=%d, nodes=%d)",
             _t0 - _t_start, _t.perf_counter() - _t0,
             budget_ms, len(veh), len(points))
    if solution is None:
        log.warning("solve: no solution (status=%s, veh=%d, nodes=%d)",
                    routing.status(), len(veh), len(points))
        raise RuntimeError("OR-Tools не нашёл решение, попробуйте ещё раз")
    return solution, routing, manager, time_dim
