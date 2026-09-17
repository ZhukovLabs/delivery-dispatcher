"""Авто-статусы курьера (база/в пути) и его текущая гео-точка."""
import time

from .planstate import _bump, _invalidate_plan, _ev
from .config import _now, log
from .adapters.sqlite_repo import _persist_couriers
from .bot_dwell import (TG_GEO_AT_PLACE, TG_GEO_FRESH, _courier_out_orders)
from .adapters.speed import _courier_speed, _speed_current_kmh
from .state import ROAD_FACTOR, STATE, _home_point
from .adapters.telegram import _tg_send
from .domain.geo import haversine_km

_AWAY_AUTO_KM = 0.5    # дальше этого от своей точки курьер «уехал» (без заказов)
_AWAY_DWELL_S = 60     # непрерывно, столько секунд (глушит GPS-прыжок и «отошёл к машине»)
_AWAY_ORDER_S = 15     # с выданными заказами «в пути» включаем быстрее
_BACK_DWELL_S = 120    # простой у точки после закрытия всех заказов — «на базе»


def _auto_status_apply(c, new_status):
    """Перевод статуса курьера по гео: БД, сброс его маршрутов, пинок подписчикам."""
    was = c.get("status")
    c["status"] = new_status
    _persist_couriers()
    _invalidate_plan(courier_id=c["id"], geo=True)
    if was != new_status:
        _ev("sys", f"{c['name']}: " + ("уехал в путь" if new_status == "away"
                                       else "вернулся на базу"))
    else:
        # статус не сменился — это просто гео-тик движения, не событие
        _bump(geo=True)
        return
    _bump()


def _auto_status_track(c, pos, now=None):
    """Авто-статусы по гео (в обе стороны, только с живым гео):

    «база» -> «в пути»: с выданными заказами — отъехал от точки дальше
    TG_GEO_AT_PLACE и держится _AWAY_ORDER_S; без заказов — уехал дальше
    _AWAY_AUTO_KM и держится _AWAY_DWELL_S.
    «в пути» -> «база»: БЫЛ в развозке (выданные заказы закрыты) и простоял
    у своей точки _BACK_DWELL_S. Курьер, который «в пути» стоит у точки и
    ждёт выдачи, назад НЕ переводится — заказов не было, возврат за диспетчером.
    Без заказов зона 150 м..500 м — гистерезис: счётчик не тикает и не сбрасывается.
    """
    home = _home_point(c)
    chat = c.get("tg_chat_id") or ""
    if not home or not chat:
        return
    status = c.get("status")
    if status not in ("base", "away"):
        STATE["tg_away"].pop(chat, None)
        return
    now = now or time.time()
    rec = STATE["tg_away"].setdefault(chat, {"since": None})
    d = haversine_km(pos, home)
    out = _courier_out_orders(c)
    if status == "base":
        # с заказами порог ниже и подтверждение короче — курьер уже развозит
        if out:
            away_km, dwell_s = TG_GEO_AT_PLACE, _AWAY_ORDER_S
        else:
            away_km, dwell_s = _AWAY_AUTO_KM, _AWAY_DWELL_S
        if d > away_km:
            rec["since"] = rec["since"] or now
            if now - rec["since"] >= dwell_s:
                rec["since"] = None
                log.info("auto-away: %s уехал от точки «%s» (%.0f м) — статус «в пути»",
                         c.get("name"), home.get("name"), d * 1000)
                _auto_status_apply(c, "away")
        elif d <= TG_GEO_AT_PLACE:
            rec["since"] = None  # у точки — отсчёт заново
        return
    # статус «в пути»: вернулся к точке без открытых заказов и простоял —
    # «на базе». Открытые заказы держат «в пути» (закроет диспетчер/диалог).
    if d <= TG_GEO_AT_PLACE:
        if not out:
            rec["since"] = rec["since"] or now
            if now - rec["since"] >= _BACK_DWELL_S:
                STATE["tg_away"].pop(chat, None)
                log.info("auto-return: %s вернулся к точке «%s» — статус «на базе»",
                         c.get("name"), home.get("name"))
                _auto_status_apply(c, "base")
        else:
            rec["since"] = None  # ещё развозит — не возврат
    else:
        rec["since"] = None     # снова уехал — счётчик простоя сброшен


def _courier_geo(c, depot, now=None):
    """Гео-данные курьера для расчётов: сглаженная позиция + оценка возврата на депо.

    Возвращает None, если привязки нет или гео старше TG_GEO_FRESH.
    back_min - за сколько курьер физически доедет до депо (анти-прыжки уже
    применены медианой при приёме точки, здесь только расстояние).
    """
    chat = c.get("tg_chat_id") or ""
    pos = STATE["tg_pos"].get(chat)
    if not pos or not depot:
        return None
    now = now or time.time()
    age = now - pos["ts"]
    if age > TG_GEO_FRESH:
        return None
    g = {"lat": pos["lat"], "lng": pos["lng"], "age_min": int(age // 60),
         "live": bool(pos.get("live"))}
    km = haversine_km(pos, depot)
    if km <= TG_GEO_AT_PLACE:
        g["at_depot"] = True
        g["back_min"] = 0
    else:
        kmh, _src = _courier_speed(c)
        g["at_depot"] = False
        g["back_min"] = int(min(480, max(1, round(
            km * ROAD_FACTOR / kmh * 60))))
    # фаза развозки: заказы выданы? загрузка у точки зафиксирована?
    out_orders = _courier_out_orders(c)
    has_out = bool(out_orders)
    load = STATE["tg_load"].get(chat) or {}
    g["has_out"] = has_out
    g["loaded"] = bool(load.get("loaded_at"))
    if has_out and not g["at_depot"]:
        g["delivering"] = True   # выданы и не у точки — значит, едет с заказами
        # честный возврат: сначала оставшиеся адреса (в порядке объезда
        # из out_route.stops), затем депо; скорость — реальная курьера.
        # «доставленные» выводим по гео (долго стоял у адреса) — для расчёта
        # их считаем развезёнными; статус заказа не трогаем
        dst = STATE["tg_deliv"].get(chat) or {}
        rem = [o for o in out_orders if not dst.get(o["id"], {}).get("at")]
        stops_order = {s[2]: i for s in
                       ((c.get("out_route") or {}).get("stops") or [])
                       if len(s) > 2}
        rem.sort(key=lambda o: stops_order.get(o["id"], 10 ** 9))
        # без координат адрес в цепочку не попадает — только в порядок объезда
        routed = [o for o in rem if o.get("lat") is not None]
        pts = [g] + routed + [depot]
        chain_km = sum(haversine_km(a, b) for a, b in zip(pts, pts[1:]))
        per_stop = max(0, int(STATE["settings"].get("handover_min", 5)))
        g["back_min"] = int(min(480, max(1, round(
            chain_km * ROAD_FACTOR / kmh * 60 + per_stop * len(routed)))))
    if not has_out and not g["at_depot"]:
        # заказы ещё не в машине: честный ETA — сначала доехать до точки
        kmh2, _ = _courier_speed(c)
        g["to_point_min"] = int(min(240, max(1, round(
            km * ROAD_FACTOR / kmh2 * 60))))
    # стоит ли курьер прямо сейчас у одного из своих выданных заказов
    best, best_km = None, None
    for o in out_orders:
        d = haversine_km(pos, o)
        if d <= TG_GEO_AT_PLACE and (best_km is None or d < best_km):
            best, best_km = o["address"], d
    if best:
        g["at_order"] = best
    return g

