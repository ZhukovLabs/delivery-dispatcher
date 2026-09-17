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

from .core import STATE, _db, _db_lock, _geo_payload, _payload, _points_ids

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


async def _broadcast() -> None:
    rooms = sio.manager.rooms.get("/", {})
    for pid in _points_ids():
        room = f"depot:{pid}"
        if not rooms.get(room):
            continue  # в этом депо никого — не собираем payload зря
        payload = await asyncio.to_thread(_payload, None, pid)
        await sio.emit("state", payload, room=room)


async def _broadcast_geo() -> None:
    """Тик движения лёгким событием: позиция/скорость/оценки курьеров."""
    rooms = sio.manager.rooms.get("/", {})
    if not any(rooms.get(f"depot:{pid}") for pid in _points_ids()):
        return
    snap = await asyncio.to_thread(_geo_payload)
    for pid in _points_ids():
        room = f"depot:{pid}"
        if rooms.get(room):
            await sio.emit("geo", snap, room=room)


def _verify_token(token: str) -> dict | None:
    """Токен = подписанная cookie-сессия → {uid, sid, point} или None.

    Подписи мало: пользователь ещё должен быть жив в БД. HTTP-cookie это
    проверяет _me() на каждом запросе, а сокет живёт своей жизнью — без
    сверки удалённый диспетчер продолжал бы получать состояние депо
    до конца 12-часовой жизни токена.
    """
    from .shims import unsign_session
    try:
        data = unsign_session(token)
    except Exception:  # noqa: BLE001
        return None
    if not data or not data.get("uid"):
        return None
    try:
        with _db_lock, _db() as c:
            row = c.execute("SELECT id FROM users WHERE id = ?",
                            (data["uid"],)).fetchone()
    except Exception:  # noqa: BLE001
        log.exception("ws hub: не смогли проверить пользователя %s", data["uid"])
        return None
    return data if row else None


def _alive_uids(uids: set) -> set:
    """Какие из uid ещё есть в users (одним запросом)."""
    marks = ",".join("?" * len(uids))
    with _db_lock, _db() as c:
        return {r["id"] for r in c.execute(
            f"SELECT id FROM users WHERE id IN ({marks})", tuple(uids))}


async def _recheck_sessions() -> None:
    """Раз в минуту: пользователь удалён — его открытые сокеты закрываются.

    Пассивного слушателя иначе не выбросить: connect был давно и валиден.
    """
    if not _sessions:
        return
    uids = {dp.get("uid") for dp in _sessions.values() if dp.get("uid")}
    if not uids:
        return
    alive = await asyncio.to_thread(_alive_uids, uids)
    for sid, dp in list(_sessions.items()):
        if dp.get("uid") and dp["uid"] not in alive:
            log.info("ws hub: пользователь %s удалён — закрываю сокет", dp["uid"])
            await sio.disconnect(sid)
            # событие disconnect вычистит само, но для sid, которого уже нет
            # у менеджера (полуоткрытый), события не будет — чистим явно
            _sessions.pop(sid, None)


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


def socketio_app():
    """ASGI-приложение socket.io (монтируется в FastAPI на /)."""
    return socketio.ASGIApp(sio)
