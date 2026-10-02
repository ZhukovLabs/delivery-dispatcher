# -*- coding: utf-8 -*-
"""Регрессия «онлайн неверно показывается»: WS-сокеты поддерживают онлайн.

Консоль на живом WS не шлёт HTTP-запросов (хартбит /health анонимен), и
активный диспетчер выпадал из «онлайн» через ONLINE_WINDOW=90с; состав
онлайна не рассылался — бейдж «Администраторов онлайн» висел устаревшим.
"""
import copy
import time

import dp.ws.sessions as wss
from dp.online import ONLINE


def _restore(onl, sess):
    ONLINE.clear()
    ONLINE.update(onl)
    wss._sessions.clear()
    wss._sessions.update(sess)


def test_touch_creates_and_extends():
    onl, sess = copy.deepcopy(ONLINE), dict(wss._sessions)
    try:
        ONLINE.clear()
        wss._sessions.clear()
        wss._sessions["sock1"] = {"uid": "u1", "point": "p1", "csid": "cs1"}
        wss._touch_ws_online([{"id": "u1", "email": "a@x"}])
        assert ONLINE["cs1"]["email"] == "a@x"
        assert ONLINE["cs1"]["point_id"] == "p1"

        # смена рабочего места: rows без email — продлеваем, email не затираем
        t0 = ONLINE["cs1"]["last"]
        time.sleep(0.01)
        wss._sessions["sock1"]["point"] = "p2"
        wss._touch_ws_online([])
        assert ONLINE["cs1"]["email"] == "a@x"      # email уцелел
        assert ONLINE["cs1"]["point_id"] == "p2"    # зеркало точки догнало
        assert ONLINE["cs1"]["last"] > t0           # сессия продлена
    finally:
        _restore(onl, sess)


def test_drop_only_when_last_socket_gone():
    """Вторая вкладка того же браузера держит онлайн при закрытии первой."""
    onl, sess = copy.deepcopy(ONLINE), dict(wss._sessions)
    try:
        ONLINE.clear()
        ONLINE["cs1"] = {"uid": "u1", "email": "a@x", "point_id": "p1",
                         "last": time.time()}
        wss._sessions.clear()
        wss._sessions.update({
            "sock1": {"uid": "u1", "point": "p1", "csid": "cs1"},
            "sock2": {"uid": "u1", "point": "p1", "csid": "cs1"},
        })
        wss._sessions.pop("sock1", None)          # закрылась первая вкладка
        assert wss._drop_ws_online("cs1") is False
        assert "cs1" in ONLINE                     # вторая вкладка держит

        wss._sessions.pop("sock2", None)          # закрылась и вторая
        assert wss._drop_ws_online("cs1") is True
        assert "cs1" not in ONLINE
    finally:
        _restore(onl, sess)


def test_no_email_no_record():
    """Без email запись не создаём (токен/workpoint не знает email)."""
    onl, sess = copy.deepcopy(ONLINE), dict(wss._sessions)
    try:
        ONLINE.clear()
        wss._sessions.clear()
        wss._sessions["sock1"] = {"uid": "u1", "point": "p1", "csid": "cs1"}
        wss._touch_ws_online([])
        assert ONLINE == {}
    finally:
        _restore(onl, sess)
