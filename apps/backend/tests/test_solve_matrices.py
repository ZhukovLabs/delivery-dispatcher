# -*- coding: utf-8 -*-
"""Матрицы транзитов решателя обязаны совпадать с исходной формулой колбэка.

_solve_once переведён с Python-колбэков дуги на C-side матрицы (Register-
TransitMatrix): живой колбэк держал GIL и замораживал сервер на время
поиска. Тест фиксирует числовую эквивалентность нового пути со старой
формулой (пере-реализация здесь, изменение формулы = сигнал ревью).
"""
from types import SimpleNamespace

from dp.services.solve_run import _veh_transit_matrix


def _mk_ctx():
    # 2 дома (K=2) + 3 заказа; значения подобраны чтоб задеть все ветки
    M = [[0, 900, 420, 200, 700],
         [800, 0, 300, 950, 400],
         [500, 600, 0, 150, 800],
         [700, 300, 120, 0, 650],
         [900, 950, 700, 500, 0]]
    return SimpleNamespace(matrix=M, K=2, handover_s=300, base_traffic=1.25,
                           points=list(range(5)))


def _reference(cb_ctx, v, i, j):
    """Исходная формула make_cb из solve_run (до перевода на матрицы)."""
    arc = cb_ctx.matrix[i][j]
    if j >= cb_ctx.K and arc > cb_ctx.handover_s:
        arc = int(round((arc - cb_ctx.handover_s) / cb_ctx.base_traffic
                        * v["factor"] * v["hour_f"])) + cb_ctx.handover_s
    elif j < cb_ctx.K and arc > 0:
        arc = int(round(arc / cb_ctx.base_traffic * v["factor"] * v["hour_f"]))
    cost = arc
    if j >= cb_ctx.K:
        cost += v["appr"].get(j, 0) * 60
        if j not in v["allowed"]:
            cost += 1_000_000_000
    return cost


def test_transit_matrix_matches_callback_formula():
    ctx = _mk_ctx()
    v = {"factor": 0.83, "hour_f": 1.35, "appr": {2: 4, 4: 2},
         "allowed": {2, 3}, "k": 1, "home": 0}
    m = _veh_transit_matrix(ctx, v)
    for i in range(5):
        for j in range(5):
            assert m[i][j] == _reference(ctx, v, i, j), (i, j, m[i][j])


def test_transit_matrix_branches():
    """Возвратная дуга (j<K), граница вручения, чужой заказ, подъезд."""
    ctx = _mk_ctx()
    v = {"factor": 1.0, "hour_f": 1.0, "appr": {3: 5},
         "allowed": {2, 3, 4}, "k": 0, "home": 1}
    m = _veh_transit_matrix(ctx, v)
    # возвратная дуга заказ(3) -> дом(1): 300/1.25 без вручения
    assert m[3][1] == int(round(300 / 1.25))
    # дуга в заказ ровно на границе вручения (300) не пересчитывается
    assert m[1][2] == 300
    # подъезд к заказу 3: +5 мин
    assert m[0][3] == 200 + 5 * 60
    # чужой заказ: миллиардная дуга
    v2 = dict(v, allowed={4})
    m2 = _veh_transit_matrix(ctx, v2)
    assert m2[0][3] == 200 + 300 + 1_000_000_000
