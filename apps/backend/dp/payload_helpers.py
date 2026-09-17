"""Пейлоады-хелперы: лёгкий гео-канал и точки с админами онлайн."""
import time

from .online import ONLINE, ONLINE_WINDOW, _ONLINE_LOCK
from .bot_status import _courier_geo
from .adapters.speed import _speed_current_kmh
from .adapters.telegram import TG_POS_TTL
from .state import STATE, _home_point

def _geo_payload():
    """Лёгкий срез движения (сотни байт вместо полного состояния).

    Тик движения курьера каждую секунду тянет только позицию, скорость и
    гео-оценки возврата — заказы, планы и трассы приезжают с событиями.
    По каждому ПРИВЯЗАННОМУ к боту курьеру айтем летит всегда, даже без
    свежей позиции ({id}): клиент снимает устаревший маркер сам, не дожидаясь
    полного снапшота. Непривязанные в срез не попадают — им нечего снимать.
    """
    now = time.time()
    out = []
    for c in list(STATE["couriers"]):  # снапшот: TG-поток меняет список
        chat = c.get("tg_chat_id") or ""
        if not chat:
            continue
        item = {"id": c["id"]}
        pos = STATE["tg_pos"].get(chat)
        if pos and now - pos["ts"] < TG_POS_TTL:
            item["pos"] = {"lat": pos["lat"], "lng": pos["lng"],
                           "ts": pos["ts"], "live": bool(pos.get("live")),
                           "acc": pos.get("acc") or 0}
            cur = _speed_current_kmh(pos, now)
            if cur is not None:
                item["cur_kmh"] = cur
        geo = _courier_geo(c, _home_point(c), now)
        if geo:
            item["geo"] = geo
        out.append(item)
    return {"t": now, "couriers": out}


def _points_with_admins(points):
    """Точки с админами онлайн — НЕ кэшируется: ONLINE живёт своей жизнью
    (логин/хартбит/логаут) и не меняет rev, по которому кэшируется база."""
    now = time.time()
    with _ONLINE_LOCK:
        for sid in [s for s, r in ONLINE.items()
                    if now - r["last"] > ONLINE_WINDOW * 4]:
            ONLINE.pop(sid, None)  # подчистили давно ушедших
        live = [r for r in ONLINE.values() if now - r["last"] < ONLINE_WINDOW]
    by_point: dict = {}
    for a in live:
        by_point.setdefault(a["point_id"], []).append(a["email"])
    return [dict(p, admins=list(dict.fromkeys(  # один человек в нескольких
                by_point.get(p["id"], []))))       # сессиях = одна запись
            for p in points]
