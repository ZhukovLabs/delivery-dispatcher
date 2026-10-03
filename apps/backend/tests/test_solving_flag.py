# -*- coding: utf-8 -*-
"""Регрессия синхронизации «идёт расчёт» между диспетчерами депо.

Успешный расчёт снимал флаг solving БЕЗ _bump: рассылка solving=false
полагалась на бамп из _ev, который стрелял ДО снятия флага — гонка могла
отправить (и закэшировать по rev) payload с solving=true, после чего
событий больше не было и оверлей «Идёт расчёт» у второго диспетчера
висел до перезагрузки страницы.
"""
import copy
import types

import dp.routes_solve as rs
from dp.planstate import _bump as _real_bump
from dp.state import STATE
from dp.shims import set_request_ctx


def _req():
    return types.SimpleNamespace(query_params={}, url=types.SimpleNamespace(
        path="/api/solve"), method="POST", client=None)


def test_solve_clears_flag_before_last_bump(monkeypatch):
    snap = copy.deepcopy(STATE)
    try:
        STATE["points"] = [{"id": "p1", "name": "База", "lat": 52.4, "lng": 31.0}]
        STATE["couriers"] = [{"id": "c1", "name": "Курьер", "point_id": "p1",
                              "status": "base", "tg_chat_id": ""}]
        STATE["orders"] = [{"id": "o1", "address": "Ленина 1", "lat": 52.4,
                            "lng": 31.0, "status": "ready", "point_id": "p1",
                            "created_at": "2026-01-01T10:00:00"}]
        STATE["solving"] = {}

        calls = []

        def bump_spy():
            # что видит рассылка в момент бампа: флаг уже снят или ещё нет
            calls.append(bool(STATE["solving"].get("p1")))
            _real_bump()

        monkeypatch.setattr(rs, "_bump", bump_spy)
        monkeypatch.setattr(rs, "_ev", lambda *a, **k: None)
        monkeypatch.setattr(rs, "_persist_meta", lambda: None)
        monkeypatch.setattr(rs, "_payload",
                            lambda me=None, myp=None: {"ok": True})
        monkeypatch.setattr(rs, "_compute_plan",
                            lambda mode="auto", advice=True, force=None,
                            point_id=None: {"routes": [{"courier_name": "Курьер",
                                                        "count": 1}]})

        set_request_ctx(_req(), {"mode": "auto"},
                        {"uid": "u1", "is_admin": True, "point": "p1"})
        resp = rs.solve()

        assert resp == {"ok": True}
        # бампов было >= 2 и ПОСЛЕДНИЙ видит флаг уже снятым: именно его
        #payload получат диспетчеры депо через WS
        assert len(calls) >= 2
        assert calls[-1] is False
        assert STATE["solving"]["p1"] is False
    finally:
        STATE.clear()
        STATE.update(snap)
