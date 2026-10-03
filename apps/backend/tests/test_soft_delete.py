# -*- coding: utf-8 -*-
"""Soft delete курьеров: строка помечается, а не стирается — статистика
уволившихся (имя в history, id в speed_day) не должна обнуляться."""
import contextlib
import copy

from dp.adapters.repo import courier_stats as cs
from dp.adapters.repo import persist
from dp.state import STATE

import pytest


@pytest.fixture
def snap():
    saved = copy.deepcopy(STATE)
    yield
    STATE.clear()
    STATE.update(saved)


def test_persist_marks_missing_couriers_deleted(snap, monkeypatch):
    stmts = []

    @contextlib.contextmanager
    def _db():
        class Cx:
            def execute(self, q, p=()):
                stmts.append((q, tuple(p)))

            def executemany(self, q, rows=()):
                stmts.append((q, list(rows)))

        yield Cx()

    monkeypatch.setattr(persist, "_db", _db)
    STATE["couriers"] = [{"id": "c1", "name": "Аня", "status": "base",
                          "color": "#111", "back_min": 15, "tg_chat_id": "",
                          "tg_login": "", "point_id": "p1"}]
    persist._persist_couriers()

    upd = next(q for q, _ in stmts if "UPDATE couriers SET deleted" in q)
    assert "NOT IN" in upd
    # c2 пропал из STATE — должен попасть в параметры пометки deleted
    upd_args = next(p for q, p in stmts if "UPDATE couriers SET deleted" in q)
    assert upd_args == ("c1",)
    dele = next(p for q, p in stmts if q.startswith("DELETE FROM couriers"))
    assert dele == ("c1",)  # стираются и перезаписываются только активные
    ins = next(p for q, p in stmts if q.startswith("INSERT INTO couriers"))
    assert ins[0][0] == "c1"


def test_deleted_courier_keeps_km(snap, monkeypatch):
    """Уволенный курьер исчез из STATE, но его км из speed_day считаются."""
    hist = [{"courier": "Гоша", "outcome": "delivered",
             "closed_at": "2026-10-03T12:00:00", "out_at": "2026-10-03T11:00:00",
             "payment": "cash", "pay_amount": 7.0}]

    @contextlib.contextmanager
    def _db():
        class _Res:
            def __init__(self, items):
                self._items = items

            def fetchall(self):
                return self._items

        class Cx:
            def execute(self, q, args=()):
                rows_ = (hist if "FROM history" in q
                         else [{"courier_id": "g1", "geo_m": 5200.0, "geo_s": 3600.0}]
                         if "FROM speed_day" in q
                         else [{"id": "g1", "name": "Гоша"}]  # deleted=1, но в таблице
                         if "FROM couriers" in q else [])
                return _Res(rows_)

        yield Cx()

    monkeypatch.setattr(cs, "_db", _db)
    STATE["couriers"] = []  # Гоши больше нет среди активных
    STATE["orders"] = []
    out = cs._courier_day_stats(point_id=None, day="2026-10-03")
    r = out["rows"][0]
    assert r["courier"] == "Гоша"
    assert r["km"] == 5.2
    assert r["geo_h"] == 1.0
