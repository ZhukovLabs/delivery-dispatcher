"""WS-хаб живых обновлений (socket.io): румы по депо, бродкаст состояния.

Вместо long-poll /api/rev: клиенты подписываются на рум своей рабочей точки;
любое изменение состояния (_bump из любого потока) коалесится (гео летит
каждые 5 с — слать payload чаще раза в ~150 мс нет смысла) и рассылается
подписчикам payload'ом этого депо.

Авторизация на handshake: токен = подпись cookie-сессии (фронт получает его
в теле /api/login) — кросс-доменный WS cookie (SameSite) не полагаемся.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import socketio

from .core import STATE, _payload, _points_ids

log = logging.getLogger("dispatcher")

# in-memory broker: один процесс — STATE всё равно в памяти
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

_loop: asyncio.AbstractEventLoop | None = None
_notify_lock = threading.Lock()
_dirty = False
_geo = False  # грязь только от гео-тика движения (не событие)
_last_flush = 0.0
_flusher_started = False
_sessions: dict = {}  # sid → {"uid", "point"} — AsyncServer не хранит environ

_DEBOUNCE_S = 0.15
_GEO_MIN_INTERVAL_S = 1.0  # чистое движение шлём не чаще раза в секунду


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
        _dirty = True
        # классификация «только гео» живёт до hard-события: оно снимает
        # ограничение — предстоящая отправка и так понесёт всё состояние
        _geo = geo


async def _flusher() -> None:
    """Коалесер: события — после дебаунса, чистое движение — не чаще 1/с."""
    global _dirty, _geo, _last_flush
    while True:
        await asyncio.sleep(_DEBOUNCE_S)
        with _notify_lock:
            if not _dirty:
                continue
            if _geo and time.monotonic() - _last_flush < _GEO_MIN_INTERVAL_S:
                continue  # движение уже отправляли менее секунды назад — ждём
            _dirty = False
            _geo = False
            _last_flush = time.monotonic()
        try:
            await _broadcast()
        except Exception:  # noqa: BLE001 — хаб не должен умирать
            log.exception("ws hub broadcast failed")


async def _broadcast() -> None:
    rooms = sio.manager.rooms.get("/", {})
    for pid in _points_ids():
        room = f"depot:{pid}"
        if not rooms.get(room):
            continue  # в этом депо никого — не собираем payload зря
        payload = await asyncio.to_thread(_payload, None, pid)
        await sio.emit("state", payload, room=room)


def _verify_token(token: str) -> dict | None:
    """Токен = подписанная cookie-сессия → {uid, sid, point} или None."""
    from .shims import unsign_session
    try:
        return unsign_session(token)
    except Exception:  # noqa: BLE001
        return None


@sio.event
async def connect(sid: str, environ: dict, auth: dict | None = None) -> bool:
    auth = auth or {}
    token = str(auth.get("token") or "")
    data = _verify_token(token) if token else None
    if not data or not data.get("uid"):
        log.warning("ws hub: connect rejected (bad token) sid=%s", sid)
        return False  # отказ в соединении
    pid = auth.get("point") or data.get("point") or ""
    if pid not in _points_ids():
        pid = _points_ids()[0] if _points_ids() else ""
    await sio.enter_room(sid, f"depot:{pid}")
    _sessions[sid] = {"uid": data["uid"], "point": pid}
    log.info("ws hub: connected uid=%s depot=%s", data["uid"], pid)
    return True


@sio.event
async def disconnect(sid: str) -> None:
    dp = _sessions.pop(sid, None)
    if dp:
        log.info("ws hub: disconnected uid=%s", dp.get("uid"))


@sio.event
async def workpoint(sid: str, data: dict | None = None) -> None:
    """Диспетчер переключил рабочую точку — перезайти в рум другого депо."""
    pid = str((data or {}).get("point_id") or "")
    if pid not in _points_ids():
        return
    dp = _sessions.get(sid)
    if not dp:
        return
    old = dp.get("point")
    if old and old != pid:
        await sio.leave_room(sid, f"depot:{old}")
    await sio.enter_room(sid, f"depot:{pid}")
    dp["point"] = pid
    # сразу отдать состояние нового депо (не ждать следующего изменения)
    payload = await asyncio.to_thread(_payload, None, pid)
    await sio.emit("state", payload, to=sid)
from .shims import unsign_session as _unsign

def socketio_app():
    """ASGI-приложение socket.io (монтируется в FastAPI на /)."""
    return socketio.ASGIApp(sio)
