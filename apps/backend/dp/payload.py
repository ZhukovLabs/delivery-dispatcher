"""Пейлоады для клиентов: полный срез состояния и лёгкий гео-канал."""
import threading
import time

from .config import CFG, _now
from .adapters.sqlite_repo import _courier_day_stats, _history_today
from .online import ONLINE, ONLINE_WINDOW, _ONLINE_LOCK, _my_point, _plan_for
from .adapters.ors import ors_status
from .bot_status import _courier_geo
from .planstate import _bump, _ev
from .shims import jsonify
from .adapters.speed import _courier_speed, _speed_current_kmh
from .state import STATE, _home_point, _obj_point
from .adapters.telegram import TG_POS_TTL
from .users import _admin_users, _me
from .payload_helpers import _geo_payload, _points_with_admins

# кэш базового пейлоада: (rev, myp) -> срез депо (без me/users)
_payload_base_cache: dict = {}
_payload_cache_lock = threading.Lock()


def _payload(me=None, myp=None):
    """Ответ после мутации: состояние + квота ORS + счётчики дня + текущий пользователь.

    me/myp задаются явно при WS-бродкасте (там нет сессии запроса):
    payload конкретного депо для всех его подписчиков. База payload'а
    кэшируется по (rev, депо): мутация собирает её для ответа, а хаб через
    дебаунс переиспользует для бродкаста — на слабом CPU двойная сборка
    заметна.
    """
    me = _me() if me is None else me
    if myp is None:
        myp = _my_point() if me else ""
    else:
        myp = myp or ""
    key = (STATE.get("rev", 0), myp)
    with _payload_cache_lock:
        base = _payload_base_cache.get(key)
    if base is None:
        base = _build_payload_base(myp)
        with _payload_cache_lock:
            if len(_payload_base_cache) > 6:
                _payload_base_cache.clear()  # рев движется — старые не нужны
            _payload_base_cache[key] = base
    return jsonify({**base, "points": _points_with_admins(base["points"]),
                    "me": me, "my_point": myp,
                    "users": _admin_users() if me and me["is_admin"] else []})


def _build_payload_base(myp):
    """Состояние депо для ответов и бродкастов (без пользователя сессии)."""
    now = time.time()
    couriers = []
    for c in STATE["couriers"]:
        cc = dict(c)
        home = _home_point(c)
        pos = STATE["tg_pos"].get(c.get("tg_chat_id") or "")
        if pos and now - pos["ts"] < TG_POS_TTL:
            cc["pos"] = {"lat": pos["lat"], "lng": pos["lng"], "ts": pos["ts"],
                         "live": bool(pos.get("live")), "acc": pos.get("acc") or 0}
        cur = _speed_current_kmh(pos, now)
        if cur is not None:
            cc["cur_kmh"] = cur
        avg_kmh, avg_src = _courier_speed(c)
        cc["avg_kmh"] = round(avg_kmh, 1)
        cc["speed_src"] = avg_src
        geo = _courier_geo(c, home, now)
        if geo:
            cc["geo"] = geo
        # активная развозка: выданные заказы в порядке выдачи (маршрут мог
        # уже уйти из плана — карта рисует его пунктиром по этим данным)
        outs = sorted((o for o in STATE["orders"]
                       if o.get("assigned") == c["id"]
                       and (o.get("status") or "ready") == "out"),
                      key=lambda o: (o.get("out_no", float("inf")),
                                     o.get("out_at") or "", o["id"]))
        if outs:
            cc["out_route"] = {
                # [lat, lng, order_id]: id нужен фронту, чтобы красить точки
                # доставки выбранного курьера в его цвет
                "stops": [[o["lat"], o["lng"], o["id"]] for o in outs],
                "home": {"lat": home["lat"], "lng": home["lng"]} if home else None,
            }
            if c.get("out_geom"):
                cc["out_route"]["geom"] = c["out_geom"]
        elif c.get("status") == "away" and c.get("ret_geom"):
            # возврат на базу: обратная трасса без точек доставки
            cc["out_route"] = {
                "stops": [],
                "home": {"lat": home["lat"], "lng": home["lng"]} if home else None,
                "geom": c["ret_geom"],
            }
        couriers.append(cc)
    # курьеры видны всем депо, но свои — первыми (стабильно по исходному порядку)
    couriers.sort(key=lambda cc: 0 if _obj_point(cc) == myp else 1)
    st = {k: v for k, v in STATE.items()
          if k not in ("tg_seen", "tg_pos", "tg_offset", "tg_nagged", "tg_load",
                       "tg_deliv", "tg_away", "tg_pay", "plans", "advice_modes", "solving")}
    seen = sorted(STATE["tg_seen"].values(), key=lambda x: -x["ts"])[:20]
    # живые счётчики по точкам: курьеры считаются здесь (меняются только
    # с rev), а админы онлайн — в _payload при каждом вызове: ONLINE
    # меняется без bump (логин/хартбит), кэш базы по rev их застевал бы
    st["points"] = [dict(p,
                         couriers=sum(1 for c in STATE["couriers"]
                                      if _obj_point(c) == p["id"]))
                    for p in st.get("points") or []]
    # скоуп депо: свои заказы, свой план и своя история; чужие — только курьеры
    st["orders"] = [o for o in st.get("orders") or [] if _obj_point(o) == myp]
    st["plan"] = _plan_for(myp)
    # идёт ли сейчас расчёт развозки в ЭТОМ депо (блокирует UI всех его
    # диспетчеров; словарь pid->bool наружу не отдаём)
    st["solving"] = bool(STATE.get("solving", {}).get(myp))
    return jsonify({**st, "couriers": couriers,
                    "tg": {"bot": STATE["tg_bot"], "seen": seen},
                    "ors": ors_status(), "today": _history_today(point_id=myp),
                    "cfg": {"tg": bool(CFG["tg_bot_token"])}})
