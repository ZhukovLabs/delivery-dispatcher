import math
import time

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
