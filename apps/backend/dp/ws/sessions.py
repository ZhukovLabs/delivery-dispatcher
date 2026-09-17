"""WS-хаб: connect/disconnect/workpoint и сверка живых сессий."""
from __future__ import annotations

import asyncio

from ..core import _db, _db_lock, _payload, _points_ids
from .hub import _sessions, log, sio

def _verify_token(token: str) -> dict | None:
    """Токен = подписанная cookie-сессия → {uid, sid, point} или None.

    Подписи мало: пользователь ещё должен быть жив в БД. HTTP-cookie это
    проверяет _me() на каждом запросе, а сокет живёт своей жизнью — без
    сверки удалённый диспетчер продолжал бы получать состояние депо
    до конца 12-часовой жизни токена.
    """
    from ..shims import unsign_session
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
