# -*- coding: utf-8 -*-
"""Регрессии свипа 03.10: инварианты планов и права на отвязку TG.

1. Подтверждение «Доставил» курьером не должно выбрасывать план депо
   (drop_plan) — заказ уже в развозке, его стопы в плане давно нет.
2. Отвязка Telegram-аккаунта курьера — только своё депо или админ
   (раньше guard был только на привязке).
3. plan_move чистит пустые трипы/маршруты источника (как plan_unassign).
"""
import copy
from types import SimpleNamespace

import pytest

import dp.bot_flow as bf
import dp.planstate as ps
import dp.routes_couriers_tg as tg
import dp.routes_plan_edit as pe
from dp.shims_state import reset_request_ctx, set_request_ctx
from dp.state import STATE


@pytest.fixture
def snap():
    saved = copy.deepcopy(STATE)
    yield
    STATE.clear()
    STATE.update(saved)


def test_delivered_keeps_plan(snap, monkeypatch):
    """«Доставил» помечает план устаревшим, но не выбрасывает его."""
    monkeypatch.setattr(bf, "_archive_order", lambda *a, **k: None)
    monkeypatch.setattr(bf, "_persist_orders", lambda: None)
    monkeypatch.setattr(bf, "_flip_return_route", lambda cid: None)
    monkeypatch.setattr(bf, "_bump", lambda *a, **k: None)
    monkeypatch.setattr(bf, "_ev", lambda *a, **k: None)
    monkeypatch.setattr(ps, "_persist_meta", lambda: None)
    monkeypatch.setattr(ps, "_bump", lambda *a, **k: None)

    STATE["points"] = [{"id": "p1", "name": "A", "lat": 52.4, "lng": 31.0}]
    STATE["couriers"] = [{"id": "c1", "name": "Иван", "status": "out",
                          "point_id": "p1"}]
    STATE["orders"] = [{"id": "o1", "address": "Ленина 1", "status": "out",
                        "assigned": "c1", "point_id": "p1"}]
    STATE["plans"] = {"p1": {"pid": "p1", "routes": [
        {"courier_id": "c1", "courier_name": "Иван", "trips": [
            {"stops": [{"order_id": "o2", "address": "Другая 2",
                        "lat": 52.41, "lng": 31.01, "eta_min": 5}]}]}]}}

    ok, who = bf._bot_close_delivered("o1")
    assert ok and who == "Иван"
    assert STATE["orders"] == []                    # заказ закрыт и заархивирован
    assert "p1" in STATE["plans"]                   # план НЕ выброшен
    plan = STATE["plans"]["p1"]
    assert plan["stale"] is True                    # только помечен устаревшим
    stops = plan["routes"][0]["trips"][0]["stops"]
    assert [s["order_id"] for s in stops] == ["o2"]  # чужие стопы целы


def _unbind(cid, is_admin, my_point, monkeypatch):
    req = SimpleNamespace(query_params={}, url=SimpleNamespace(
        path="/api/couriers/x/tg"), method="POST", client=None)
    monkeypatch.setattr(tg, "_me", lambda: {"id": "u1", "is_admin": is_admin})
    monkeypatch.setattr(tg, "_my_point", lambda: my_point)
    monkeypatch.setattr(tg, "_home_point",
                        lambda c: {"id": c.get("point_id") or "p1"})
    monkeypatch.setattr(tg, "_persist_couriers", lambda: None)
    monkeypatch.setattr(tg, "_bump", lambda *a, **k: None)
    monkeypatch.setattr(tg, "_payload", lambda *a, **k: ({"ok": 1}, 200))
    tok = set_request_ctx(req, {}, {})
    try:
        return tg.unbind_courier(cid)
    finally:
        reset_request_ctx(tok)


def test_unbind_foreign_depot_forbidden(snap, monkeypatch):
    """Диспетчер чужого депо не может отвязать Telegram курьера."""
    STATE["points"] = [{"id": "p1"}, {"id": "p2"}]
    STATE["couriers"] = [{"id": "c1", "name": "X", "point_id": "p2",
                          "tg_chat_id": "42"}]
    STATE["tg_pos"] = {}

    resp = _unbind("c1", 0, "p1", monkeypatch)          # не админ, чужое депо
    assert resp.status_code == 403
    assert STATE["couriers"][0]["tg_chat_id"] == "42"   # не отвязал

    resp = _unbind("c1", 1, "p1", monkeypatch)          # админ — можно
    assert resp.status_code == 200
    assert STATE["couriers"][0]["tg_chat_id"] == ""


def test_plan_move_cleans_empty_routes(snap, monkeypatch):
    """После переноса стопа маршрут-донор без стопов исчезает из плана."""
    monkeypatch.setattr(pe, "_json", lambda: {"order_id": "o1",
                                              "to_courier": "c2"})
    monkeypatch.setattr(pe, "_retiming_matrix",
                        lambda *a, **k: ({}, {"o1": (52.4, 31.0)}, {},
                                         {"c2": 0}))
    monkeypatch.setattr(pe, "_best_insert", lambda *a, **k: (60.0, 0, 0))
    monkeypatch.setattr(pe, "_retime_route", lambda *a, **k: None)
    monkeypatch.setattr(pe, "_recalc_plan_stats", lambda *a, **k: None)
    for name in ("_persist_meta", "_persist_orders", "_bump"):
        monkeypatch.setattr(pe, name, lambda *a, **k: None)
    monkeypatch.setattr(pe, "_payload", lambda *a, **k: ({"ok": 1}, 200))

    stop = {"order_id": "o1", "address": "Ленина 1", "lat": 52.4,
            "lng": 31.0, "eta_min": 5}
    STATE["points"] = [{"id": "p1", "name": "A", "lat": 52.4, "lng": 31.0}]
    STATE["couriers"] = [
        {"id": "c1", "name": "Иван", "status": "base", "point_id": "p1"},
        {"id": "c2", "name": "Пётр", "status": "base", "point_id": "p1"}]
    STATE["orders"] = [{"id": "o1", "address": "Ленина 1", "status": "ready",
                        "assigned": "", "point_id": "p1"}]
    STATE["plans"] = {"p1": {"pid": "p1", "routes": [
        {"courier_id": "c1", "courier_name": "Иван",
         "trips": [{"stops": [dict(stop)]}]},
        {"courier_id": "c2", "courier_name": "Пётр",
         "trips": [{"stops": [dict(stop, order_id="o2", address="Другая 2")]}]}]}}

    resp = pe.plan_move()
    assert resp.status_code == 200
    routes = STATE["plans"]["p1"]["routes"]
    assert [r["courier_id"] for r in routes] == ["c2"]   # пустой c1 убран
    moved = [s for tr in routes[0]["trips"] for s in tr["stops"]]
    assert {s["order_id"] for s in moved} == {"o1", "o2"}
