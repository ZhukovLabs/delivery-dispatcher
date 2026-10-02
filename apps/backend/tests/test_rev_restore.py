# -*- coding: utf-8 -*-
"""Регрессия: rev переживает рестарт (инцидент «после деплоя карта мертва»).

После рестарта бэкенда счётчик rev начинался с нуля. Клиенты же дропают
снимки с rev <= уже увиденного (защита от отставших бродкастов) — консоль
без перезагрузки страницы не получала ни WS-снапшоты, ни /api/state
(queryFn считал свежий ответ «отставшим» и возвращал старый кэш).
"""
import copy

import pytest

import dp.bootstrap as bs
from dp.state import STATE


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Cx:
    """Минимальная подделка соединения: отвечает по тексту запроса."""
    def __init__(self):
        self.meta = [{"key": "rev", "value": "42"}]

    def executescript(self, schema):
        pass

    def execute(self, q, p=()):
        if q.startswith("SELECT key"):
            return _Res(list(self.meta))
        return _Res([])


@pytest.fixture
def fake_db(monkeypatch):
    saved = copy.deepcopy(STATE)
    cx = _Cx()

    class _Ctx:
        def __enter__(self):
            return cx

        def __exit__(self, *a):
            return False

    import threading
    monkeypatch.setattr(bs, "_DB_SCHEMA", "")
    monkeypatch.setattr(bs, "_db", lambda: _Ctx())
    monkeypatch.setattr(bs, "_db_lock", threading.RLock())
    monkeypatch.setattr(bs, "_persist_meta", lambda: None)
    yield cx
    STATE.clear()
    STATE.update(saved)


def test_rev_continues_after_restart(fake_db):
    """Счётчик rev продолжается с запасом, а не сбрасывается в ноль."""
    bs.load_state()
    assert STATE["rev"] == 1_000_042  # 42 (персист) + запас 1M


def test_rev_without_saved_value(fake_db):
    """Первый старт после фикса: сохранённого rev нет — стартуем с 1M."""
    fake_db.meta = []
    bs.load_state()
    assert STATE["rev"] == 1_000_000


def test_rev_broken_value(fake_db):
    """Мусор в meta['rev'] не роняет загрузку."""
    fake_db.meta = [{"key": "rev", "value": "не число"}]
    bs.load_state()
    assert STATE["rev"] == 1_000_000
