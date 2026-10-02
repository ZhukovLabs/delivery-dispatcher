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
    for name in ("_persist_couriers", "_persist_meta", "_invalidate_plan"):
        monkeypatch.setattr(crud, name, lambda *a, **k: None)
    monkeypatch.setattr(crud, "_payload", lambda *a, **k: {"ok": True})
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


def _set_point(cid, pid, is_admin, monkeypatch):
    req = SimpleNamespace(query_params={}, url=SimpleNamespace(
        path="/api/couriers/x/point"), method="POST", client=None)
    monkeypatch.setattr(crud, "_me",
                        lambda: {"id": "u1", "is_admin": is_admin})
    tok = set_request_ctx(req, {"point_id": pid}, {})
    try:
        return crud.set_courier_point(cid)
    finally:
        reset_request_ctx(tok)


def test_move_courier_admin_only(two_points, monkeypatch):
    """Перевод курьера между точками — только администратор, независимо
    от «места работы» (регрессия 03.10: рядовой диспетчер мог двигать)."""
    _add_courier("иван", {"point": "p1"})
    cid = STATE["couriers"][0]["id"]

    resp = _set_point(cid, "p2", 0, monkeypatch)   # диспетчер — нельзя
    assert resp.status_code == 403
    assert STATE["couriers"][0]["point_id"] == "p1"

    resp = _set_point(cid, "p2", 1, monkeypatch)   # админ — любое место работы
    assert resp == {"ok": True}                    # dict => HTTP 200
    assert STATE["couriers"][0]["point_id"] == "p2"
