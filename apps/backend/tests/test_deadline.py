# -*- coding: utf-8 -*-
"""Дедлайн «к HH:MM» -> минуты от сейчас: границы и переход через полночь."""
from dp.domain.model import _deadline_rel_min


def test_future_same_day():
    assert _deadline_rel_min("14:30", 13 * 60) == 90


def test_past_same_day_stays_negative():
    # утренний дедлайн днём остаётся просроченным (без переноса на завтра)
    assert _deadline_rel_min("09:00", 15 * 60) == -360


def test_midnight_wrap_near_2400():
    # «00:07» в 23:42 — это завтра через 25 минут, а не -1413
    assert _deadline_rel_min("00:07", 23 * 60 + 42) == 25


def test_just_past_midnight_no_wrap():
    # «23:00» в 00:30 — это СЕГОДНЯ вечером (+1350), перенос не нужен
    assert _deadline_rel_min("23:00", 30) == 1350


def test_invalid_returns_none():
    assert _deadline_rel_min("", 0) is None
    assert _deadline_rel_min(None, 0) is None
    assert _deadline_rel_min("24:00", 0) is None
    assert _deadline_rel_min("9:5", 0) is None
    assert _deadline_rel_min(" 09:05 ", 300) == 245  # пробелы и без ведущего нуля
