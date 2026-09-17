"""WS-хаб: сервер socket.io, состояние коалесера, запуск и дебаунс."""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import socketio

from ..core import STATE, _db, _db_lock, _geo_payload, _payload, _points_ids

log = logging.getLogger("dispatcher")

# in-memory broker: один процесс — STATE всё равно в памяти.
# ping 15/60 (вместо дефолтных 25/20): сервер за Tailscale Funnel (релей),
# короткие затыки сети не должны рвать сокет — восстанавливать дороже.
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*",
                           ping_interval=15, ping_timeout=60)

_loop: asyncio.AbstractEventLoop | None = None
_notify_lock = threading.Lock()
_dirty = False
_geo = False  # грязь только от гео-тика движения (не событие)
_last_flush = 0.0
_last_full_flush = 0.0  # последнее ПОЛНОЕ состояние (гео-тики его не обновляют)
_flusher_started = False
_sessions: dict = {}  # sid → {"uid", "point"} — AsyncServer не хранит environ

_DEBOUNCE_S = 0.15
_GEO_MIN_INTERVAL_S = 1.0  # чистое движение шлём не чаще раза в секунду
_GEO_FULL_RESYNC_S = 60.0  # …но раз в минуту движение догоняется полным состоянием
_SESSION_RECHECK_S = 60.0   # раз в минуту сверяем пользователей открытых сокетов
_next_recheck = 0.0

from .broadcast import _broadcast, _broadcast_geo
from .sessions import _recheck_sessions


def start(loop: asyncio.AbstractEventLoop) -> None:
    """Зафиксировать event loop и запустить коалесер (вызов из lifespan)."""
    global _loop, _flusher_started
    _loop = loop
    if not _flusher_started:
        _flusher_started = True
        asyncio.run_coroutine_threadsafe(_flusher(), loop)
        log.info("ws hub: flusher started")


def notify_changed(geo: bool = False) -> None:
    """Пометить состояние грязным (безопасно из любого потока).

    geo=True — изменение только в отслеживании движения курьера: хаб
    доставляет такие обновления не чаще раза в секунду (позиция на карте
    не требует большей частоты). Любое другое событие — без ограничений.
    """
    global _dirty, _geo
    with _notify_lock:
        # классификация накопленной грязи: hard-событие не понижается до
        # гео-тика последним пришедшим (_geo = geo ронял флаг solving=false
        # конца расчёта в лёгкий тик — UI зависал на «Идёт расчёт»).
        # полный флеш будет только если ВСЁ накопленное — гео.
        if not _dirty:
            _geo = geo
        else:
            _geo = _geo and geo
        _dirty = True


async def _flusher() -> None:
    """Коалесер: события — после дебаунса, чистое движение — не чаще 1/с.

    Тик движения не собирает полное состояние (~100 КБ на депо каждую
    секунду — тяжело и для ноутбука, и для релея Funnel): подписчикам летит
    лёгкое событие «geo» (позиции/скорости/оценки), а раз в минуту
    движение всё равно догоняется полным снапшотом — самовосстановление,
    если лёгкий тик потерялся.
    """
    global _dirty, _geo, _last_flush, _last_full_flush, _next_recheck
    while True:
        await asyncio.sleep(_DEBOUNCE_S)
        now = time.monotonic()
        if now >= _next_recheck:
            _next_recheck = now + _SESSION_RECHECK_S
            try:
                await _recheck_sessions()
            except Exception:  # noqa: BLE001 — хаб не должен умирать
                log.exception("ws hub session recheck failed")
        with _notify_lock:
            if not _dirty:
                continue
            geo_only = _geo
            if geo_only and now - _last_flush < _GEO_MIN_INTERVAL_S:
                continue  # движение уже отправляли менее секунды назад — ждём
            _dirty = False
            _geo = False
            _last_flush = now
            force_full = geo_only and now - _last_full_flush >= _GEO_FULL_RESYNC_S
        try:
            if geo_only and not force_full:
                await _broadcast_geo()
            else:
                _last_full_flush = time.monotonic()
                await _broadcast()
        except Exception:  # noqa: BLE001 — хаб не должен умирать
            log.exception("ws hub broadcast failed")


def socketio_app():
    """ASGI-приложение socket.io (монтируется в FastAPI на /)."""
    return socketio.ASGIApp(sio)
