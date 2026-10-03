# -*- coding: utf-8 -*-
"""Заказ, закреплённый за курьером другого депо, не должен молча
выпадать из расчёта.

Прод-инцидент: на «Точке 3» три заказа, «Рассчитать развозку» строил
маршруты только по двум — заказ с pin=за-курьером-Барыкины не входил
ни в одно allowed и отваливался в unassigned. Теперь невыполнимый pin
игнорируется решателем с предупреждением, а перевод курьера в другое
депо снимает его закрепления за заказами старой точки.
"""
import pytest

from dp.services import solve_ctx as ctx_mod
from dp.services.solve import solve_plan
from dp.state import STATE

_KEYS = ("couriers", "points", "orders", "tg_pos", "plans", "settings")


@pytest.fixture
def cross_state():
    """Два депо; заказ o2 точки p1 закреплён за курьером точки p2."""
    snap = {k: STATE[k] for k in _KEYS}
    STATE["points"] = [
        {"id": "p1", "name": "Точка 3", "address": "а", "lat": 52.0, "lng": 31.0},
        {"id": "p2", "name": "Барыкина", "address": "б", "lat": 52.3, "lng": 31.3},
    ]
    STATE["couriers"] = [
        {"id": "c1", "name": "Настя", "status": "base", "point_id": "p1",
         "color": "#111"},
        {"id": "cX", "name": "Денис", "status": "base", "point_id": "p2",
         "color": "#222"},
    ]
    STATE["orders"] = [
        {"id": "o1", "address": "ул. Ленина, 1", "lat": 52.1, "lng": 31.1,
         "point_id": "p1", "pin": "", "status": "ready",
         "created_at": "2026-10-03T10:00:00"},
        {"id": "o2", "address": "ул. Песина, 5", "lat": 52.15, "lng": 31.15,
         "point_id": "p1", "pin": "cX", "status": "ready",
         "created_at": "2026-10-03T10:05:00"},
    ]
    STATE["tg_pos"] = {}
    STATE["plans"] = {}
    yield STATE
    STATE.update(snap)


def _offline(monkeypatch):
    m = [[0, 600, 900], [400, 0, 700], [800, 500, 0]]
    monkeypatch.setattr(ctx_mod, "build_time_matrix",
                        lambda points, settings, k_homes=1:
                        (m, True, None, "test"))
    monkeypatch.setattr(ctx_mod, "_courier_speed",
                        lambda c, settings=None: (60.0, "test"))


def test_foreign_pin_order_still_delivered(cross_state, monkeypatch):
    """Заказ с невыполнимым pin едет обычным маршрутом, не выпадает."""
    _offline(monkeypatch)
    plan = solve_plan(point_id="p1", with_geometry=False)
    assert plan["unassigned"] == 0
    delivered = {s["order_id"] for r in plan["routes"] for s in r["stops"]}
    assert delivered == {"o1", "o2"}
    assert any("закрепление пропущено" in w for w in plan.get("warnings", []))


def test_off_courier_pin_ignored_in_now_scenario(cross_state, monkeypatch):
    """Сценарий «не ждать»: pin за away-курьером не роняет заказ."""
    _offline(monkeypatch)
    cross_state["couriers"][0]["status"] = "away"
    cross_state["couriers"][0]["back_min"] = 30
    cross_state["couriers"].append(
        {"id": "c2", "name": "Пётр", "status": "base", "point_id": "p1",
         "color": "#333"})
    cross_state["orders"][1]["pin"] = "c1"  # закреплён за away-курьером
    plan = solve_plan(point_id="p1", include_away=False, with_geometry=False)
    assert plan["unassigned"] == 0
    delivered = {s["order_id"] for r in plan["routes"] for s in r["stops"]}
    assert "o2" in delivered


def test_move_courier_releases_old_depot_pins(cross_state, monkeypatch):
    """Перевод курьера в другое депо снимает его pin-ы заказов старой точки."""
    from types import SimpleNamespace

    from dp import routes_couriers_crud as crud
    from dp.shims_state import reset_request_ctx, set_request_ctx

    monkeypatch.setattr(crud, "_me",
                        lambda: {"id": "u1", "is_admin": True})
    monkeypatch.setattr(crud, "_payload", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(crud, "_persist_couriers", lambda: None)
    monkeypatch.setattr(crud, "_persist_orders", lambda: None)
    monkeypatch.setattr(crud, "_invalidate_plan", lambda **kw: None)

    req = SimpleNamespace(query_params={}, url=SimpleNamespace(
        path="/api/couriers/cX/point"), method="POST", client=None)
    cross_state["couriers"][1]["point_id"] = "p1"  # до перевода — наша точка
    tok = set_request_ctx(req, {"point_id": "p2"}, {"uid": "u1"})
    try:
        resp = crud.set_courier_point("cX")
    finally:
        reset_request_ctx(tok)
    # flaskish без запроса FastAPI возвращает dict как есть
    assert (resp.get("ok") if isinstance(resp, dict) else resp[1] == 200)
    assert cross_state["couriers"][1]["point_id"] == "p2"
    assert cross_state["orders"][1]["pin"] == ""  # невыполнимое снято
