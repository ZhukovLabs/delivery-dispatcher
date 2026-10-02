# -*- coding: utf-8 -*-
"""Регрессия создания курьера: привязка к РАБОЧЕЙ точке диспетчера.

Баг 03.10: add_courier вешал курьера всегда на points[0] — при создании
из другой точки («Место работы» в шапке) курьер «пропадал»: заказы его
точки ему недоступны, решатель точки его не видит.
"""
import copy
from types import SimpleNamespace

import pytest

from dp import routes_couriers_crud as crud
from dp.shims_state import reset_request_ctx, set_request_ctx
from dp.state import STATE


@pytest.fixture
def two_points(monkeypatch):
    snap = copy.deepcopy(STATE)
    STATE["points"] = [
        {"id": "p1", "name": "Подгорная", "address": "а", "lat": 52.4,
         "lng": 31.0, "pos": 0},
        {"id": "p2", "name": "Точка 3", "address": "б", "lat": 52.5,
         "lng": 31.1, "pos": 1},
    ]
    STATE["couriers"] = []
    for name in ("_persist_couriers", "_persist_meta", "_invalidate_plan",
                 "_payload"):
        monkeypatch.setattr(crud, name, lambda *a, **k: None)
    yield
    STATE.clear()
    STATE.update(snap)


def _add_courier(name, sess):
    req = SimpleNamespace(query_params={}, url=SimpleNamespace(
        path="/api/couriers"), method="POST", client=None)
    tok = set_request_ctx(req, {"name": name}, sess)
    try:
        return crud.add_courier()
    finally:
        reset_request_ctx(tok)


def test_courier_attaches_to_workpoint(two_points):
    """Диспетчер работает на второй точке — курьер должен сесть на неё,
    а не на первую из списка (регрессия «привязаны криво»)."""
    _add_courier("иван", {"point": "p2"})
    assert len(STATE["couriers"]) == 1
    assert STATE["couriers"][0]["point_id"] == "p2"


def test_courier_without_workpoint_falls_back_to_first(two_points):
    _add_courier("пётр", {})
    assert STATE["couriers"][0]["point_id"] == "p1"


def test_empty_name_rejected(two_points):
    resp = _add_courier("   ", {})
    assert STATE["couriers"] == []
    assert resp.status_code == 400
