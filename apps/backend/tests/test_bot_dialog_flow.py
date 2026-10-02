# -*- coding: utf-8 -*-
"""Полный диалог бота: «доставил?» -> подтверждение -> оплата -> сумма.

Регрессия инцидента 03.10: после выбора «Наличными/Картой» бот отвечал
«Уже неактуально». Guard должен пропускать стадии pay/pay_amount (заказ
уже закрыт и убран из STATE) — прогоняем весь флоу целиком и фиксируем,
что кнопка оплаты работает и сумма числом принимается.
"""
import copy

import pytest

from dp.bot_updates import _tg_handle_update
from dp.state import STATE


@pytest.fixture
def dialog_state(monkeypatch):
    """Изолированный STATE, патчи БД/WS/TG-отправок; захват сообщений."""
    snap = copy.deepcopy(STATE)
    STATE["points"] = [{"id": "p1", "name": "Депо", "address": "а",
                        "lat": 52.0, "lng": 31.0}]
    STATE["couriers"] = [{"id": "c1", "name": "Иван", "status": "out",
                          "point_id": "p1", "tg_chat_id": "777",
                          "color": "#111"}]
    STATE["orders"] = [{"id": "o1", "address": "ул. Песина, 52к1",
                        "lat": 52.4, "lng": 31.0, "point_id": "p1",
                        "assigned": "c1", "status": "out",
                        "created_at": "2026-10-03T01:00:00"}]
    STATE["tg_ask"] = {"777": {"o1": {"msg": 42, "stage": "ask"}}}
    STATE["tg_pay"] = {}
    STATE["plans"] = {}
    captured = {"edits": [], "answers": [], "sends": [], "pays": []}
    monkeypatch.setattr("dp.bot_flow._archive_order", lambda *a, **k: None)
    monkeypatch.setattr("dp.bot_flow._persist_orders", lambda *a, **k: None)
    monkeypatch.setattr("dp.bot_flow._bump", lambda geo=False: None)
    monkeypatch.setattr("dp.bot_flow._ev", lambda *a, **k: None)
    monkeypatch.setattr("dp.bot_dialog._ev", lambda *a, **k: None)
    monkeypatch.setattr("dp.bot_updates._bump", lambda geo=False: None)
    monkeypatch.setattr("dp.bot_updates._ev", lambda *a, **k: None)

    def _pay(oid, method=None, amount=None):
        captured["pays"].append((oid, method, amount))

    monkeypatch.setattr("dp.bot_dialog._pay_set", _pay)
    monkeypatch.setattr("dp.bot_updates._pay_set", _pay)
    monkeypatch.setattr("dp.bot_dialog._tg_edit_msg",
                        lambda chat, mid, text, kb=None:
                        captured["edits"].append(text))
    monkeypatch.setattr("dp.bot_dialog._tg_answer_cb",
                        lambda cid, text="": captured["answers"].append(text))
    monkeypatch.setattr("dp.bot_updates._tg_edit_msg",
                        lambda chat, mid, text, kb=None:
                        captured["edits"].append(text))
    monkeypatch.setattr("dp.bot_updates._tg_send",
                        lambda chat, text: captured["sends"].append(text))
    # гео-путь: побочные эффекты трекеров в тестах не нужны
    monkeypatch.setattr("dp.bot_updates._speed_geo_sample", lambda *a: None)
    monkeypatch.setattr("dp.bot_updates._load_track", lambda *a: None)
    monkeypatch.setattr("dp.bot_updates._deliver_track", lambda *a: None)
    monkeypatch.setattr("dp.bot_updates._auto_status_track", lambda *a: None)
    yield captured
    STATE.clear()
    STATE.update(snap)


def _cb(oid, act):
    return {"callback_query": {"id": f"cb-{act}", "data": f"dlv:{oid}:{act}",
                               "message": {"chat": {"id": 777}}}}


def test_full_delivery_pay_flow(dialog_state):
    cap = dialog_state
    _tg_handle_update(_cb("o1", "y"))    # «Доставил» -> подтверждение
    _tg_handle_update(_cb("o1", "ok"))   # подтверждение -> «Как оплатил?»
    assert STATE["orders"] == []
    assert "Как оплатил клиент?" in cap["edits"][-1]

    # инцидент 03.10: здесь бот отвечал «Уже неактуально»
    _tg_handle_update(_cb("o1", "pay:cash"))
    assert cap["answers"][-1] != "Уже неактуально"
    assert STATE["tg_ask"]["777"]["o1"]["stage"] == "pay_amount"
    assert ("o1", "cash", None) in cap["pays"]
    assert "Напишите сумму" in cap["edits"][-1]

    _tg_handle_update({"message": {"chat": {"id": 777}, "text": "24.50"}})
    # оплата пишется двумя частичными UPDATE в одну строку history:
    # сначала метод, потом сумма
    assert cap["pays"] == [("o1", "cash", None), ("o1", None, 24.5)]
    assert STATE["tg_pay"] == {}
    assert "o1" not in STATE["tg_ask"].get("777", {})


def test_stale_button_says_not_actual(dialog_state):
    """Клик по кнопке без живого диалога (рестарт/старый экран) —
    вежливый отказ, данные не меняются."""
    cap = dialog_state
    STATE["tg_ask"] = {}
    _tg_handle_update(_cb("o1", "pay:cash"))
    assert cap["answers"] == ["Уже неактуально"]
    assert cap["pays"] == []


def test_pay_dialog_ignores_geo_ticks(dialog_state):
    """Live-geo тик при открытом диалоге оплаты не спамит «не понял сумму»
    и не роняет диалог (регрессия: спам «неверная сумма», 03.10)."""
    import time as _time

    cap = dialog_state
    STATE["_tg_persist_ts"] = _time.time()  # не пишем БД из теста
    _tg_handle_update(_cb("o1", "y"))
    _tg_handle_update(_cb("o1", "ok"))
    _tg_handle_update(_cb("o1", "pay:cash"))
    assert STATE["tg_pay"]["777"]["oid"] == "o1"

    _tg_handle_update({"edited_message": {
        "chat": {"id": 777}, "from": {"username": "ivan"},
        "location": {"latitude": 52.4, "longitude": 31.0,
                     "live_period": 3600}}})
    assert cap["sends"] == []                      # спама «не понял сумму» нет
    assert STATE["tg_pay"]["777"]["oid"] == "o1"   # диалог жив
    assert STATE["tg_pos"]["777"]["lat"] == 52.4   # гео обработано

    _tg_handle_update({"message": {"chat": {"id": 777}, "text": "24.50"}})
    assert ("o1", None, 24.5) in cap["pays"]       # сумма по-прежнему принята
