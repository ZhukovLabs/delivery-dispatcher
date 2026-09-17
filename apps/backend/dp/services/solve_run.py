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


def _solve_once(ctx, budget_ms):
    veh, matrix, K = ctx.veh, ctx.matrix, ctx.K
    handover_s, base_traffic = ctx.handover_s, ctx.base_traffic
    reload_s, pinned_n = ctx.reload_s, ctx.pinned_n
    max_orders, helper_ids, force_ids = ctx.max_orders, ctx.helper_ids, ctx.force_ids
    deadline_rel, eff_prio, points = ctx.deadline_rel, ctx.eff_prio, ctx.points
    manager = pywrapcp.RoutingIndexManager(len(points), len(veh),
                                           [v["home"] for v in veh],
                                           [v["home"] for v in veh])
    routing = pywrapcp.RoutingModel(manager)

    def make_cb(v, start_index, with_trip_cost):
        def cb(from_index, to_index):
            i, j = (manager.IndexToNode(from_index),
                    manager.IndexToNode(to_index))
            arc = matrix[i][j]
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
            if with_trip_cost and from_index == start_index and v["k"] > 0:
                # фиксированная цена активации заезда k>0 (возврат + перезагрузка):
                # без неё PCI не различает копии одного курьера и может посадить
                # единственный заезд в k1/k2, завышая ETА на полчаса, а одиночные
                # переносы не вытащат (промежуточное расщепление дороже).
                # Только в ЦЕЛЕВУЮ функцию — в размерность времени надбавка
                # не идёт (нижние границы стартов уже учитывают перезагрузку)
                cost += reload_s
            return cost
        return cb

    cb_idxs = []      # транзиты времени (без цены активации)
    for vi, v in enumerate(veh):
        vi_start = routing.Start(vi)
        cb_idx = routing.RegisterTransitCallback(make_cb(v, vi_start, False))
        cb_idxs.append(cb_idx)
        routing.SetArcCostEvaluatorOfVehicle(
            routing.RegisterTransitCallback(make_cb(v, vi_start, True)), vi)

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
            # размерность считает дуги: простой = 1, ровно один заказ = 2
            orders_dim.CumulVar(routing.End(vi)).SetRange(2, 2)
        elif vi == first and cid in force_ids:
            # перетащен в план вручную: первый заезд обязан взять заказ
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

    # Штрафы ожидания доставки: обычный заказ 1 мин, просрочка дедлайна 25 —
    # на размерности Time; приоритет 60/мин «поскорее» — на отдельной
    # размерности Urg, чтобы заказ с приоритетом И дедлайном давился ОБЕИМИ
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
    solution = routing.SolveWithParameters(params)
    if solution is None:
        log.warning("solve: no solution (status=%s, veh=%d, nodes=%d)",
                    routing.status(), len(veh), len(points))
        raise RuntimeError("OR-Tools не нашёл решение, попробуйте ещё раз")
    return solution, routing, manager, time_dim
