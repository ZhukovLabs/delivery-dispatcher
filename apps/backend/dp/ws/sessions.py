"""WS-хаб: connect/disconnect/workpoint и сверка живых сессий."""
from __future__ import annotations

import asyncio
import time

from ..core import (ONLINE, ONLINE_WINDOW, _db, _db_lock, _ONLINE_LOCK,
                    _payload, _points_ids)
from .hub import _sessions, log, sio


def _touch_ws_online(rows) -> None:
    """Отметить онлайн всех подключённых сокетов (куки-sid из WS-токена).

    Консоль на живом WS не шлёт HTTP-запросов (хартбит /health анонимен),
    и активный диспетчер выпадал из «онлайна» через ONLINE_WINDOW.
    rows — живые пользователи [{id, email}]: без email запись не создаём,
    но существующую продлеваем (workpoint не знает email).
    """
    now = time.time()
    by_uid = {r["id"]: r.get("email") or "" for r in rows}
    with _ONLINE_LOCK:
        for dp in list(_sessions.values()):
            csid, uid = dp.get("csid"), dp.get("uid")
            if not csid or not uid:
                continue
            email = by_uid.get(uid, "")
            rec = ONLINE.get(csid)
            if rec is None:
                if not email:
                    continue
                ONLINE[csid] = {"uid": uid, "email": email,
                                "point_id": dp.get("point") or "",
                                "last": now}
            else:
                rec["last"] = now
                if email:
                    rec["email"] = email
                if dp.get("point"):
                    rec["point_id"] = dp["point"]


def _drop_ws_online(csid: str) -> bool:
    """Убрать онлайн-запись сессии, если её сокетов больше нет.

    True — запись убрали (нужно рассказать подписчикам)."""
    if not csid:
        return False
    if any(o.get("csid") == csid for o in _sessions.values()):
        return False  # другая вкладка того же браузера ещё держит сокет
    with _ONLINE_LOCK:
        ONLINE.pop(csid, None)
    return True


def _online_snapshot() -> set:
    """Текущий видимый состав онлайна (для сравнения до/после)."""
    now = time.time()
    with _ONLINE_LOCK:
        return {(r["email"], r.get("point_id") or "") for r in ONLINE.values()
                if now - r["last"] < ONLINE_WINDOW}

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


def _alive_users(uids: set) -> list:
    """Живые пользователи (одним запросом): id + email для онлайн-зеркала."""
    marks = ",".join("?" * len(uids))
    with _db_lock, _db() as c:
        return [dict(r) for r in c.execute(
            f"SELECT id, email FROM users WHERE id IN ({marks})", tuple(uids))]


async def _recheck_sessions() -> None:
    """Раз в минуту: сверка пользователей открытых сокетов.

    Удалённый диспетчер иначе не выбросить: connect был давно и валиден.
    Заодно продлеваем «онлайн» всем подключённым (WS-консоль не шлёт HTTP)
    и рассказываем подписчикам, если состав онлайна изменился.
    """
    from .hub import notify_changed
    if not _sessions:
        return
    uids = {dp.get("uid") for dp in _sessions.values() if dp.get("uid")}
    if not uids:
        return
    before = _online_snapshot()
    rows = await asyncio.to_thread(_alive_users, uids)
    alive = {r["id"] for r in rows}
    for sid, dp in list(_sessions.items()):
        if dp.get("uid") and dp["uid"] not in alive:
            log.info("ws hub: пользователь %s удалён — закрываю сокет", dp["uid"])
            await sio.disconnect(sid)
            # событие disconnect вычистит само, но для sid, которого уже нет
            # у менеджера (полуоткрытый), события не будет — чистим явно
            _sessions.pop(sid, None)
    _touch_ws_online(rows)
    if _online_snapshot() != before:
        notify_changed()  # состав «онлайн» изменился — бейджи устарели


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
    _sessions[sid] = {"uid": data["uid"], "point": pid,
                      "csid": data.get("sid") or ""}
    log.info("ws hub: connected uid=%s depot=%s", data["uid"], pid)
    # подключившийся диспетчер сразу виден в «онлайне» своей точки
    rows = await asyncio.to_thread(_alive_users, {data["uid"]})
    _touch_ws_online(rows)
    from .hub import notify_changed
    notify_changed()
    return True


@sio.event
async def disconnect(sid: str) -> None:
    dp = _sessions.pop(sid, None)
    if dp:
        log.info("ws hub: disconnected uid=%s", dp.get("uid"))
        if _drop_ws_online(dp.get("csid") or ""):
            from .hub import notify_changed
            notify_changed()  # ушёл из онлайна — бейджи устарели


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
    # онлайн-зеркало точки и бейджи у остальных диспетчеров
    _touch_ws_online([])
    from .hub import notify_changed
    notify_changed()
    # сразу отдать состояние нового депо (не ждать следующего изменения)
    payload = await asyncio.to_thread(_payload, None, pid)
    await sio.emit("state", payload, to=sid)
