# -*- coding: utf-8 -*-
"""Мостик километража на обрыве гео (03.10): туннель/лифт/батарея не должны
съедать пробег — гэп до 20 минут засчитывается прямой A->B, если средняя
скорость на нём правдоподобна (3..80 км/ч). Телепорты и парковочный дрейф
отбрасываются."""
import pytest

from dp.adapters import speed


def _pt(ts, dlat):
    # 1 градус широты ~ 111.2 км (радиус Земли 6371); dlat задаёт «сырую» длину
    return {"ts": ts, "lat": 52.0 + dlat, "lng": 31.0, "acc": 10}


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(speed, "_speed_add",
                        lambda cid, **kw: calls.append(kw))
    return calls


def _run(calls, prev, cur):
    speed._speed_geo_sample("c1", prev, cur)
    return calls


def test_bridge_gap_counted(captured):
    # 11.7 минуты гэпа, ~7.8 км дороги -> средняя ~40 км/ч: засчитать
    dlat = 7780 / 1.4 / 111_195.0
    calls = _run(captured, _pt(0, 0), _pt(700, dlat))
    assert len(calls) == 1
    assert abs(calls[0]["geo_m"] - 7780) < 15
    assert calls[0]["geo_s"] == 700


def test_bridge_teleport_dropped(captured):
    # 25 км за 11 минут -> ~128 км/ч: телепорт, не путь
    dlat = 25_000 / 1.4 / 111_320.0
    assert _run(captured, _pt(0, 0), _pt(700, dlat)) == []


def test_bridge_too_long_dropped(captured):
    # гэп больше 20 минут — данные слишком старые, не верим прямой
    assert _run(captured, _pt(0, 0), _pt(1300, 0.05)) == []


def test_bridge_parking_drift_dropped(captured):
    # 400 м за 15 минут -> ~1.8 км/ч: дрейф стоянки, не движение
    assert _run(captured, _pt(0, 0), _pt(900, 400 / 111_320.0)) == []


def test_normal_segment_still_counted(captured):
    # регрессия: обычный отрезок 60 с проходит как раньше
    calls = _run(captured, _pt(0, 0), _pt(60, 500 / 111_320.0))
    assert len(calls) == 1
    assert calls[0]["geo_s"] == 60


def test_bad_accuracy_dropped(captured):
    a, b = _pt(0, 0), _pt(60, 500 / 111_320.0)
    b["acc"] = 150
    assert _run(captured, a, b) == []
