import math
import time

from .bot import Bot
from .client import ensure_bots, home_of, login, state
from .config import parse_args
from .geo import ARRIVE_TOL, STOP_KM, hav_km, log, walk
from .messaging import _confirm, push_geo
from .osrm import Osrm


def main():
    args = parse_args()
    s = login(args)
    osrm = Osrm(args.osrm)
    st = ensure_bots(s, args, state(s, args.base)) or state(s, args.base)
    bots = {}
    log("старт: скорость %.0f км/ч, у адреса %.0f с, тик %.0f с"
        % (args.speed, args.dwell, args.tick))

    while True:
        try:
            st = state(s, args.base)
            for c in st["couriers"]:
                cid = c["id"]
                try:
                    synth = int(c.get("tg_chat_id") or 0) >= args.chat_base
                except ValueError:
                    synth = False
                if not synth or c.get("status") == "off":
                    bots.pop(cid, None)
                    continue
                home = home_of(st, c.get("point_id"))
                if not home:
                    continue
                b = bots.get(cid)
                if b is None:
                    b = bots[cid] = Bot(cid, c["name"],
                                        int(c["tg_chat_id"]), home)
                    b.cur = home
                    log(f"{b.name}: в игре (чат {b.chat})")
                b.home = home

                out_ids = {o["id"] for o in st["orders"]
                           if o.get("status") == "out"
                           and (o.get("assigned") or "") == cid}
                orr = c.get("out_route") or {}
                stops_now = {}
                for sp in orr.get("stops") or []:
                    if len(sp) > 2 and sp[2] in out_ids:
                        stops_now[sp[2]] = (sp[0], sp[1])
                for o in st["orders"]:          # выданные без трассы — адресом
                    if o["id"] in out_ids and o["id"] not in stops_now \
                            and o.get("lat") is not None:
                        stops_now[o["id"]] = (o["lat"], o["lng"])

                now = time.time()
                lat, lng = b.cur

                if stops_now:
                    key = frozenset(stops_now)
                    if b.mode != "deliver" or key != b.done_key:
                        b.stops = stops_now
                        b.done_key = key
                        order = [sp[2] for sp in orr.get("stops") or []
                                 if len(sp) > 2 and sp[2] in stops_now]
                        b.ride(order or list(stops_now), osrm)
                    b.stops = stops_now
                    stops_left = [(oid, pt) for oid, pt in b.stops.items()
                                  if oid not in b.visited]
                    near = next((oid for oid, pt in stops_left
                                 if hav_km((lat, lng), pt) <= STOP_KM),
                                None)
                    if near is not None:
                        if not b.dwell_stop:
                            b.dwell_stop, b.dwell_until = near, now + args.dwell
                            log(f"{b.name}: у адреса, стоит {args.dwell:.0f} с")
                        if now < b.dwell_until:
                            lat, lng = b.cur
                        else:
                            b.visited.add(near)
                            b.dwell_stop = None
                            log(f"{b.name}: адрес готов, дальше")
                            # как живой курьер: подтвердить доставку в диалоге,
                            # иначе заказ висит открытым и статус курьера не
                            # вернётся на «на базе» после возврата
                            if not args.no_autoclose:
                                _confirm(s, args.base, b.chat, near, b.name)
                    else:
                        b.dwell_stop = None
                        done = walk(b, b.step_deg(args.speed, args.tick),
                                    [(oid, pt) for oid, pt in stops_left])
                        lat, lng = b.cur
                        if done and not stops_left:
                            b.done_key = None
                            b.go_home(osrm)
                elif b.mode == "deliver":
                    b.done_key = None
                    b.go_home(osrm)

                if b.mode == "to_home":
                    if math.hypot(lat - home[0], lng - home[1]) <= ARRIVE_TOL \
                            or b.pos[0] >= len(b.path) - 1:
                        b.mode = "stand"
                        b.cur = lat, lng = home
                        log(f"{b.name}: на базе, ждёт")
                    else:
                        done = walk(b, b.step_deg(args.speed, args.tick), [])
                        lat, lng = b.cur
                        if done:
                            b.mode = "stand"
                            b.cur = lat, lng = home
                            log(f"{b.name}: на базе, ждёт")
                elif b.mode in ("stand", "init"):
                    if hav_km((lat, lng), home) > STOP_KM:
                        b.go_home(osrm)
                        walk(b, b.step_deg(args.speed, args.tick), [])
                        lat, lng = b.cur
                    else:
                        b.cur = lat, lng = home
                        if b.mode == "init":
                            b.mode = "stand"

                g = push_geo(s, args.base, b.chat, lat, lng)
                if g.status_code != 200:
                    log(f"{b.name}: geo {g.status_code} {g.text[:80]}")
            alive = [b.name for b in bots.values()]
            if alive:
                log("живых: %d | %s" % (len(alive), ", ".join(alive)))
        except Exception as e:
            log("tick error: " + repr(e))
        time.sleep(args.tick)
