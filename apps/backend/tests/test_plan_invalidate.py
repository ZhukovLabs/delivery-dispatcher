# -*- coding: utf-8 -*-
"""Регрессия инвалидации планов: смена статуса курьера (база<->в пути)
не должна вырезать его маршруты из плана — план помечается «устарел».

Инцидент 03.10: администратор рассчитал маршрут, курьер вернулся на
базу (авто-статус «вернулся на базу») — маршрут вырезало из плана,
расчёт пропал из консоли.
"""
import copy

import pytest

from dp.bot_status import _auto_status_apply
from dp.planstate import _invalidate_plan
from dp.state import STATE


@pytest.fixture
def plan_state(monkeypatch):
    snap = copy.deepcopy(STATE)
    for mod, name in (("dp.planstate", "_bump"), ("dp.planstate", "_persist_meta"),
                      ("dp.bot_status", "_bump"), ("dp.bot_status", "_ev"),
                      ("dp.bot_status", "_persist_couriers")):
        monkeypatch.setattr(mod + "." + name, lambda *a, **k: None)
    STATE["plans"] = {
        "p1": {"routes": [{"courier_id": "c1", "stops": [{"oid": "o1"},
                                                         {"oid": "o2"}]},
                          {"courier_id": "c2", "stops": []}],
               "stale": False},
    }
    STATE["couriers"] = [
        {"id": "c1", "name": "Иван", "status": "away", "point_id": "p1"},
        {"id": "c2", "name": "Пётр", "status": "base", "point_id": "p1"},
    ]
    yield
    STATE.clear()
    STATE.update(snap)


def test_status_flip_keeps_route(plan_state):
    """«Вернулся на базу» не рвёт расчёт: маршрут остаётся, план устарел."""
    _auto_status_apply(STATE["couriers"][0], "base")
    assert STATE["couriers"][0]["status"] == "base"
    plan = STATE["plans"]["p1"]
    assert plan["stale"] is True
    ids = [r["courier_id"] for r in plan["routes"]]
    assert ids == ["c1", "c2"]  # маршруты не вырезаны


def test_status_flip_without_route_no_stale(plan_state):
    """Курьера нет ни в одном плане — планы не трогаем."""
    STATE["plans"]["p1"]["routes"] = [{"courier_id": "c2", "stops": []}]
    _auto_status_apply(STATE["couriers"][0], "base")
    assert STATE["plans"]["p1"]["stale"] is False


def test_hard_invalidation_still_removes_routes(plan_state):
    """off/удаление/перевод в депо — маршруты по-прежнему вырезаются."""
    _invalidate_plan(courier_id="c1")
    assert [r["courier_id"] for r in STATE["plans"]["p1"]["routes"]] == ["c2"]
    _invalidate_plan(courier_id="c2")  # план опустел — убираем целиком
    assert "p1" not in STATE["plans"]
