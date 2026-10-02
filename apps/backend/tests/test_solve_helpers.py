# -*- coding: utf-8 -*-
"""Помощник или перетащенный курьер без доступных заказов не ломают расчёт.

Единственный заказ закреплён (pin) за c1 -> у c2 пустое allowed.
Раньше helper требовал «ровно один заказ» (SetRange(2, 2)), а force -
«минимум один» (SetMin(2)): при пустом allowed модель становилась
несовместимой и весь расчёт падал «OR-Tools не нашёл решение».
"""
import pytest

from dp.services import solve_ctx as ctx_mod
from dp.services.solve import solve_plan
from dp.state import STATE

_KEYS = ("couriers", "points", "orders", "tg_pos", "plans", "settings")


@pytest.fixture
def pinned_state():
    """Подмена STATE изолированными данными с полным откатом после теста."""
    snap = {k: STATE[k] for k in _KEYS}
    STATE["points"] = [{"id": "p1", "name": "Депо", "address": "а",
                        "lat": 52.0, "lng": 31.0}]
    STATE["couriers"] = [
        {"id": "c1", "name": "Иван", "status": "base", "point_id": "p1",
         "color": "#111"},
        {"id": "c2", "name": "Пётр", "status": "away", "back_min": 30,
         "point_id": "p1", "color": "#222"},
    ]
    STATE["orders"] = [{
        "id": "o1", "address": "ул. Ленина, 1", "lat": 52.1, "lng": 31.1,
        "point_id": "p1", "pin": "c1", "status": "ready",
        "created_at": "2026-10-03T10:00:00",
    }]
    STATE["tg_pos"] = {}
    STATE["plans"] = {}
    yield STATE
    STATE.update(snap)


def _offline(monkeypatch):
    """Синтетическая матрица времени и скорость — без сети и адаптеров."""
    m = [[0, 900], [600, 0]]
    monkeypatch.setattr(ctx_mod, "build_time_matrix",
                        lambda points, settings, k_homes=1:
                        (m, True, None, "test"))
    monkeypatch.setattr(ctx_mod, "_courier_speed",
                        lambda c, settings=None: (60.0, "test"))


def test_helper_without_allowed_orders_keeps_solve_feasible(
        pinned_state, monkeypatch):
    _offline(monkeypatch)
    plan = solve_plan(point_id="p1", with_geometry=False,
                      helpers={"c2": "p1"})
    assert plan["unassigned"] == 0
    assert [r["courier_id"] for r in plan["routes"]] == ["c1"]


def test_forced_courier_without_allowed_orders_keeps_solve_feasible(
        pinned_state, monkeypatch):
    _offline(monkeypatch)
    plan = solve_plan(point_id="p1", with_geometry=False, force=["c2"])
    assert plan["unassigned"] == 0
    assert [r["courier_id"] for r in plan["routes"]] == ["c1"]
