# -*- coding: utf-8 -*-
"""Статистика курьеров за день: наличные/карта — СУММЫ денег, не количество
(регрессия 03.10: показывалось число оплат вместо собранных денег)."""
import contextlib

from dp.adapters.repo import courier_stats as cs
from dp.state import STATE

import copy
import pytest


@pytest.fixture
def fake_db(monkeypatch):
    snap = copy.deepcopy(STATE)
    rows = [
        {"courier": "Иван", "outcome": "delivered",
         "closed_at": "2026-10-03T12:00:00", "out_at": "2026-10-03T11:00:00",
         "payment": "cash", "pay_amount": 10.5},
        {"courier": "Иван", "outcome": "delivered",
         "closed_at": "2026-10-03T12:30:00", "out_at": "2026-10-03T11:30:00",
         "payment": "cash", "pay_amount": None},   # «сумму не знаю»
        {"courier": "Иван", "outcome": "delivered",
         "closed_at": "2026-10-03T13:00:00", "out_at": "2026-10-03T12:00:00",
         "payment": "card", "pay_amount": 5.25},
    ]

    @contextlib.contextmanager
    def _db():
        class _Res:
            def __init__(self, items):
                self._items = items

            def fetchall(self):
                return self._items

        class Cx:
            def execute(self, q, args=()):
                return _Res(rows if "FROM history" in q else [])

        yield Cx()

    monkeypatch.setattr(cs, "_db", _db)
    STATE["couriers"] = [{"id": "c1", "name": "Иван", "status": "base",
                          "point_id": "p1"}]
    STATE["orders"] = []
    yield
    STATE.clear()
    STATE.update(snap)


def test_pay_columns_are_money_sums(fake_db):
    out = cs._courier_day_stats(point_id=None, day="2026-10-03")
    r = out["rows"][0]
    assert r["courier"] == "Иван"
    assert r["taken"] == 3
    assert r["pay_cash"] == 10.5   # 10.5 + 0 (сумма неизвестна)
    assert r["pay_card"] == 5.25
    assert r["revenue"] == 15.75
