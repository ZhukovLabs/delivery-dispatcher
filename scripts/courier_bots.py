# -*- coding: utf-8 -*-
"""Боты-курьеры: эмуляция живой геолокации и развозки.

Поведение одного бота:
  — выданных заказов нет: едет ПО ДОРОГАМ (OSRM) к своей точке выдачи и стоит;
  — диспетчер выдал заказы: едет от точки по трассе плана (out_route.geom —
    дорожная геометрия солвера; если нет — своя трасса через OSRM) к адресам;
    у каждого адреса стоит DWELL секунд (в это время бот бэкенда спрашивает
    «доставлен?» — кнопки уходят в тест-чат живому человеку), затем следующий;
  — все адресы посещены: возвращается на точку по дорогам и стоит на месте.

Боты — обычные курьеры в системе, помечены tg_login="courier-bot" и привязаны
к синтетическим чатам (CHAT_BASE+1, +2, ...), чтобы гео-конвейер бэкенда
работал честно, как с реальными курьерами. Сообщения бота уходят живому
человеку, если на бэкендe задан env TG_TEST_REDIRECT=<chat_id>.

Запуск (пример, прод на ноуте):
  venv/bin/python scripts/courier_bots.py \
      --base http://127.0.0.1:5050 --email admin@local --password admin \
      --add "Подгорная:2,Барыкина:1" --speed 150 --dwell 60

Ключи:
  --add "Точка:N,Точка:M"  создать недостающих ботов на точках (idempotent)
  --speed KM/H             скорость езды (по умолчанию 150 — тест-режим)
  --dwell SEC              простой у адреса (по умолчанию 60)
  --tick SEC               период пуша гео (по умолчанию 5)
  --chat-base N            база синтетических chat_id (по умолчанию 9100000)
"""
import argparse
import json
import math
import sys
import time

import requests

DEG_PER_M = 1.0 / 111320.0
STOP_KM = 0.15        # км, «у адреса» — как TG_GEO_AT_PLACE бэкенда
ARRIVE_TOL = 0.0013   # ~140 м, порог прибытия к точке/адресу для езды


def log(msg):
    print(time.strftime("[%H:%M:%S]"), msg, flush=True)


def seg(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def hav_km(a, b):
    """Расстояние по большим кругам, км — как haversine_km бэкенда."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + \
        math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def advance(route, pos, dist_deg):
    """Идём по полилинии от (i, t) на dist градусов; → (i, t, lat, lng, done)."""
    i, t = pos
    if i < len(route) - 1:
        a, b = route[i], route[i + 1]
        lat = a[0] + (b[0] - a[0]) * t
        lng = a[1] + (b[1] - a[1]) * t
    else:
        lat, lng = route[i]
    while dist_deg > 0 and i < len(route) - 1:
        a, b = route[i], route[i + 1]
        L = seg(a, b) or 1e-12
        rem = L * (1 - t)
        if dist_deg < rem:
            t += dist_deg / L
            lat = a[0] + (b[0] - a[0]) * t
            lng = a[1] + (b[1] - a[1]) * t
            dist_deg = 0
        else:
            dist_deg -= rem
            i += 1
            t = 0.0
            lat, lng = route[i]
    return i, t, lat, lng, i >= len(route) - 1


class Osrm:
    """Дорожные трассы через публичный OSRM (кэш + прямой фолбэк)."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.cache = {}

    def route(self, pts):
        key = tuple((round(p[0], 5), round(p[1], 5)) for p in pts)
        if key in self.cache:
            return self.cache[key]
        path = ";".join(f"{p[1]:.6f},{p[0]:.6f}" for p in pts)
        out = None
        for _ in range(2):
            try:
                r = requests.get(
                    f"{self.base}/route/v1/driving/{path}",
                    params={"overview": "full", "geometries": "geojson"},
                    timeout=15)
                if r.ok:
                    coords = r.json()["routes"][0]["geometry"]["coordinates"]
                    if len(coords) >= 2:
                        out = [(c[1], c[0]) for c in coords]
                        break
            except requests.RequestException:
                pass
            time.sleep(2)
        if out is None:
            log(f"osrm fallback: прямая трасса через {len(pts)} точек")
            out = list(pts)
        self.cache[key] = out
        return out


class Bot:
    """Конечный автомат: to_home -> stand -> deliver -> to_home -> ..."""

    def __init__(self, cid, name, chat, home):
        self.cid, self.name, self.chat, self.home = cid, name, chat, home
        self.mode = "init"
        self.path = []          # полилиния [(lat, lng), ...]
        self.pos = (0, 0.0)     # (индекс сегмента, доля)
        self.cur = home         # текущая точка (lat, lng)
        self.stops = {}         # oid -> (lat, lng)
        self.done_key = None    # frozenset(oid) текущей развозки
        self.visited = set()
        self.dwell_until = 0.0
        self.dwell_stop = None

    def ride(self, target_stops, osrm):
        """Построить трассу развозки: текущая позиция -> адреса -> домой."""
        pts = [self.cur] + [self.stops[oid] for oid in target_stops
                            if oid in self.stops] + [self.home]
        geom = [tuple(p) for p in pts]
        if len(geom) > 2:
            geom = osrm.route(pts)
        self.path = geom
        self.pos = (0, 0.0)
        self.mode = "deliver"
        self.visited = set()
        self.dwell_until = 0.0
        self.dwell_stop = None
        log(f"{self.name}: развозка, {len(self.stops)} заказ(ов), "
            f"трасса {len(self.path)} точек")

    def go_home(self, osrm):
        self.path = osrm.route([self.cur, self.home])
        self.pos = (0, 0.0)
        self.mode = "to_home"
        log(f"{self.name}: едет на базу ({len(self.path)} точек)")

    def step_deg(self, speed_kmh, tick):
        return speed_kmh / 3.6 * tick * DEG_PER_M


def walk(bot, step, stops_left):
    """Продвижение по трассе с подшагами — не проскочить 150-м зону адреса."""
    i, t = bot.pos
    _, _, lat, lng, _ = advance(bot.path, bot.pos, 0)
    sub = max(1, int(step / (ARRIVE_TOL * 0.7)) + 1)
    for _ in range(sub):
        i, t, lat, lng, done = advance(bot.path, (i, t), step / sub)
        if any(o and hav_km((lat, lng), s) <= STOP_KM
               for o, s in stops_left):
            done = False if any(o not in ("", None) for o, _ in stops_left) else done
            break
    bot.pos, bot.cur = (i, t), (lat, lng)
    return done


def main():
    ap = argparse.ArgumentParser(description="Боты-курьеры: эмуляция гео и развозки")
    ap.add_argument("--base", default="http://127.0.0.1:5050")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--add", default="",
                    help='создать ботов: "Точка:N,Точка:M" (idempotent)')
    ap.add_argument("--prefix", default="Бот")
    ap.add_argument("--speed", type=float, default=150.0, help="км/ч")
    ap.add_argument("--dwell", type=float, default=60.0, help="сек у адреса")
    ap.add_argument("--tick", type=float, default=5.0, help="сек между тиками")
    ap.add_argument("--chat-base", type=int, default=9100000)
    ap.add_argument("--osrm", default="https://router.project-osrm.org")
    args = ap.parse_args()

    s = requests.Session()
    r = s.post(args.base + "/api/login",
               json={"email": args.email, "password": args.password}, timeout=15)
    if r.status_code != 200:
        log("login failed: " + str(r.status_code))
        sys.exit(1)
    log("login ok: " + args.email)

    osrm = Osrm(args.osrm)

    def state():
        return s.get(args.base + "/api/state", timeout=15).json()

    def point_by_name(st, wanted):
        wl = wanted.strip().lower()
        for p in st.get("points") or []:
            if p["name"].strip().lower() == wl:
                return p
        for p in st.get("points") or []:
            if p["name"].strip().lower().startswith(wl):
                return p
        return None

    def ensure_bots(st):
        """--add: создать недостающих ботов и привязать синтетические чаты."""
        if not args.add:
            return
        serial = 0
        for c in st["couriers"]:
            try:
                if int(c.get("tg_chat_id") or 0) >= args.chat_base:
                    serial += 1
            except ValueError:
                pass
        for spec in args.add.split(","):
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            pname, _, n = spec.partition(":")
            p = point_by_name(st, pname)
            if not p:
                log(f"точка «{pname}» не найдена — пропуск")
                continue
            for i in range(1, int(n) + 1):
                name = f"{args.prefix} {p['name']} {i}"
                if any(c["name"] == name for c in st["couriers"]):
                    serial += 1
                    continue
                rr = s.post(args.base + "/api/couriers", json={"name": name}, timeout=15)
                if rr.status_code != 200:
                    log(f"создать «{name}»: {rr.status_code} {rr.text[:100]}")
                    serial += 1
                    continue
                st = state()
                c = next(c for c in st["couriers"] if c["name"] == name)
                s.post(args.base + f"/api/couriers/{c['id']}/point",
                       json={"point_id": p["id"]}, timeout=15)
                serial += 1
                rr = s.post(args.base + f"/api/couriers/{c['id']}/bind",
                            json={"chat_id": args.chat_base + serial,
                                  "login": "courier-bot"}, timeout=15)
                log(f"бот «{name}» создан, чат {args.chat_base + serial}, "
                    f"точка «{p['name']}»"
                    + ("" if rr.status_code == 200 else f" (bind {rr.status_code})"))
        return state()

    st = ensure_bots(state()) or state()

    def home_of(st, pid):
        for p in st.get("points") or []:
            if p["id"] == pid:
                return (p["lat"], p["lng"])
        return None

    bots = {}
    log("старт: скорость %.0f км/ч, у адреса %.0f с, тик %.0f с"
        % (args.speed, args.dwell, args.tick))

    while True:
        try:
            st = state()
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

                g = s.post(args.base + "/api/sim/geo",
                           json={"chat_id": b.chat, "lat": round(lat, 6),
                                 "lng": round(lng, 6), "login": "courier-bot"},
                           timeout=15)
                if g.status_code != 200:
                    log(f"{b.name}: geo {g.status_code} {g.text[:80]}")
            alive = [b.name for b in bots.values()]
            if alive:
                log("живых: %d | %s" % (len(alive), ", ".join(alive)))
        except Exception as e:
            log("tick error: " + repr(e))
        time.sleep(args.tick)


if __name__ == "__main__":
    main()
