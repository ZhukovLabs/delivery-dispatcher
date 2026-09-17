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


def _start_delay_min(c):
    """Когда away-курьер сможет выехать со своей точки с новой партией.
    Занятость не выкидывает курьера из расчёта — она честно удорожает
    его старт, и решатель сам взвешивает, выгодно ли его ждать."""
    settings = STATE["settings"]
    reload_min = max(0, int(settings.get("reload_min", 10)))
    g = _courier_geo(c, _home_point(c))
    if g:  # живая гео точнее ручной оценки
        if g.get("has_out"):
            # ещё развозит: довезти остаток по адресам + вернуться +
            # перезагрузиться (back_min уже содержит цепочку адресов)
            return min(480, g["back_min"] + reload_min)
        if g.get("to_point_min") is not None:
            # заказы прежней партии ещё не забраны: доехать + погрузиться
            return min(480, g["to_point_min"] + reload_min)
        return g["back_min"]
    return max(0, int(c.get("back_min", 15)))


def _refresh_plan_delays(plan):
    """План — снимок на момент расчёта, а «старт +N мин» на карточке
    должен показывать, сколько ждать СЕЙЧАС. Сдвигаем задержку ВСЕХ заездов
    курьера с сохранением цепочки (заезд k+1 не раньше конца заезда k +
    перезагрузка); сами назначение заказов не трогаем.

    away — по живой оценке возврата; base — на время, прошедшее с расчёта
    (только пока курьер ещё не выехал: нет выданных заказов; выехавший без
    выдачи остаётся base и сдвигается — ошибка редкая и односторонняя).
    off не трогаем: он не поедет, сдвиг был бы враньём.
    """
    if not plan or not plan.get("routes"):
        return plan
    now = _now()  # единые часы приложения (Минск), не системные
    now_hm = now.hour * 60 + now.minute
    reload_min = max(0, int(STATE["settings"].get("reload_min", 10)))
    cmap = {c["id"]: c for c in STATE["couriers"]}
    elapsed_min = None
    try:
        # якорь — момент, к которому привязаны задержки плана СЕЙЧАС:
        # ретайминг (выдача/перенос) перепривязывает их к своему «сейчас»,
        # и считать elapsed от solved_at стало бы двойным сдвигом
        anchor = plan.get("anchored_at") or plan["solved_at"]
        elapsed_min = ((now - datetime.fromisoformat(anchor))
                       .total_seconds() / 60.0)
    except (KeyError, ValueError, TypeError):
        elapsed_min = None
    shifted = False
    for r in plan["routes"]:
        c = cmap.get(r.get("courier_id"))
        if not c or not r.get("trips"):
            continue
        if c.get("status") == "away":
            new_d = min(480, _start_delay_min(c))
        elif (c.get("status") == "base" and elapsed_min is not None
              and not _courier_has_out(c)):
            new_d = min(480, elapsed_min)
        else:
            continue
        d = new_d - (r["trips"][0].get("start_delay_min") or 0)
        if abs(d) < 1:
            continue
        prev_total = 0  # конец предыдущего заезда после сдвига (мин от now)
        for i, tr in enumerate(r["trips"]):
            old = tr.get("start_delay_min") or 0
            new = new_d if i == 0 else max(old + d, prev_total + reload_min)
            shift = new - old
            tr["start_delay_min"] = new
            tr["start_clock"] = (now + timedelta(minutes=new)).strftime("%H:%M")
            tr["total_min"] += shift
            tr["end_clock"] = (now + timedelta(minutes=tr["total_min"])).strftime("%H:%M")
            tr["eta_at"] = now.isoformat(timespec="seconds")
            for s in tr["stops"]:
                s["eta_min"] += shift
                s["eta_clock"] = (now + timedelta(minutes=s["eta_min"])).strftime("%H:%M")
                rel = _deadline_rel_min(s.get("deadline"), now_hm)
                s["late_min"] = max(0, s["eta_min"] - rel) if rel is not None else 0
            prev_total = tr["total_min"]
        r["start_delay_min"] = new_d
        r["total_min"] = max(t["total_min"] for t in r["trips"])
        shifted = True
    if shifted:
        plan["anchored_at"] = now.isoformat(timespec="seconds")
    return plan


def _plan_for(pid):
    """План депо по id (с живой задержкой старта away-курьеров).
    План, посчитанный пока роутеры молчали, остаётся без дорожной
    геометрии — дотягиваем на каждом запросе (локальный OSRM ~20 мс):
    линии на карте должны быть дорогами, прямыми — только последний
    резерв после всего каскада."""
    plan = STATE["plans"].get(pid)
    plan = _refresh_plan_delays(plan)
    if plan and plan.get("routing") == "roads":
        if any(not t.get("geometry") for r in plan.get("routes", [])
               for t in r.get("trips", [])):
            _attach_geometry(plan)
    return plan


def _courier_plan(c):
    """План депо, к которому приписан курьер."""
    hp = _home_point(c)
    return STATE["plans"].get(hp["id"]) if hp else None


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


