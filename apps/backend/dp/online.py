"""Онлайн-статусы админов и точечные хелперы планов."""
import threading
import time
from datetime import datetime, timedelta

from .config import _now
from .adapters.sqlite_repo import _db, _db_lock
from .shims import session
from .services.solve_geom import _attach_geometry
from .domain.model import _deadline_rel_min
from .bot_dwell import _courier_has_out
from .bot_status import _courier_geo
from .state import STATE, _home_point
from .users import _me
from .online_helpers import (_courier_plan, _plan_for,
                             _refresh_plan_delays, _start_delay_min)

# ---------- кто из админов онлайн и на какой точке работает ----------
# sid (из cookie-сессии) -> {"uid", "email", "point_id", "last"}.
# «Онлайн» = был любой запрос за ONLINE_WINDOW секунд (long-poll /api/rev
# висит до 25 с, поэтому окно с запасом).
ONLINE: dict = {}
_ONLINE_LOCK = threading.Lock()
ONLINE_WINDOW = 90


def _my_point():
    """Рабочая точка текущей сессии (селектор «Место работы» в шапке).

    Авторитетное значение — session["point"]: переживает простой,
    ONLINE-запись держит лишь живое зеркало для счётчиков «онлайн у точки».
    """
    pt = session.get("point") or ""
    if pt and any(p["id"] == pt for p in STATE.get("points") or []):
        sid = session.get("sid")
        if sid:
            with _ONLINE_LOCK:
                rec = ONLINE.get(sid)
                if rec is not None and rec.get("point_id") != pt:
                    rec["point_id"] = pt  # зеркало догоняет сессию
        return pt
    return (STATE.get("points") or [{}])[0].get("id") or ""


def _touch_online(pt=None):
    """Отметить активность текущей сессии; pt задаёт её рабочую точку.

    Если запись стёрлась (долго висевший long-poll / фоновая вкладка),
    восстанавливаем её из сессии — иначе диспетчер «пропадал» из онлайн-пробок.
    """
    sid = session.get("sid")
    if not sid:
        return
    rec = ONLINE.get(sid)
    if rec is None:
        uid = session.get("uid")
        if not uid:
            return
        with _db_lock, _db() as c:  # чтение — вне ONLINE-блокировки
            r = c.execute("SELECT email FROM users WHERE id = ?", (uid,)).fetchone()
        if not r:
            return
        with _ONLINE_LOCK:
            ONLINE[sid] = {"uid": uid, "email": r["email"],
                 "point_id": pt or session.get("point")
                 or (STATE.get("points") or [{}])[0].get("id") or "",
                 "last": time.time()}
        return
    rec["last"] = time.time()
    if pt is not None:
        rec["point_id"] = pt


def _drop_online():
    sid = session.get("sid")
    if sid:
        with _ONLINE_LOCK:
            ONLINE.pop(sid, None)
