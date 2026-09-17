"""Геометрия маршрутов: кэш + каскад ORS -> OSRM, упрощение полилиний."""
import math
import time

from .config import log
from .ors import ORS_STATE, _ors_available
from .osrm import ROUTE_FINAL_S, ROUTE_HEDGE_S
from .routing import _routing_providers
from .util import haversine_km, hedged_first

_GEOM_CACHE = {}          # ключ(точки) -> (ts, geometry)
_GEOM_TTL = 1800          # дороги за полчаса не меняются
_GEOM_CACHE_MAX = 60


def _geom_key(points):
    return tuple((round(p["lat"], 5), round(p["lng"], 5)) for p in points)


_SIMPLIFY_MAX_IN = 2048  # потолок входа _simplify_poly до Дугласа-Пекера


def _simplify_poly(points, tol_m=10.0, max_pts=1200):
    """Дуглас-Пекер в метрах: трасса для карты в разы короче без визуальной
    разницы (на зуме города 10 м — доля пикселя). Концевые точки сохраняем
    всегда; если не влезли в лимит — ужимаем грубее (tol растёт в 2 раза).
    Очень плотный вход (ORS бывает) сначала равномерно прореживается:
    худший случай ДП — O(n²), на слабом CPU его надо ограничить заранее.
    """
    n = len(points)
    if n <= 16:
        return points
    if n > _SIMPLIFY_MAX_IN:
        step = (n - 1) / (_SIMPLIFY_MAX_IN - 1)
        keep_idx = sorted({round(i * step) for i in range(_SIMPLIFY_MAX_IN)}
                          | {0, n - 1})
        points = [points[i] for i in keep_idx]
        n = len(points)
    while True:
        kx = 111320.0 * max(0.2, math.cos(math.radians(points[0][0])))
        xs = [p[1] * kx for p in points]
        ys = [p[0] * 111320.0 for p in points]
        keep = [False] * n
        keep[0] = keep[n - 1] = True
        stack = [(0, n - 1)]
        while stack:
            a, b = stack.pop()
            if b <= a + 1:
                continue
            ax, ay = xs[a], ys[a]
            dx, dy = xs[b] - ax, ys[b] - ay
            seg = dx * dx + dy * dy
            best, bi = -1.0, -1
            for i in range(a + 1, b):
                if seg <= 1e-9:
                    d2 = (xs[i] - ax) ** 2 + (ys[i] - ay) ** 2
                else:
                    t = (((xs[i] - ax) * dx + (ys[i] - ay) * dy) / seg)
                    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                    d2 = (xs[i] - ax - t * dx) ** 2 + (ys[i] - ay - t * dy) ** 2
                if d2 > best:
                    best, bi = d2, i
            if best > tol_m * tol_m:
                keep[bi] = True
                stack.append((a, bi))
                stack.append((bi, b))
        out = [p for p, k in zip(points, keep) if k]
        if len(out) <= max_pts or tol_m > 400.0:
            return out
        tol_m *= 2.0


def routing_geometry(points):
    """Геометрия маршрута тем же каскадом, что и матрицы. None при сбое всех.

    Успешные ответы кэшируются по набору точек (30 мин): повторный расчёт
    того же заезда и клики по курьеру на карте не ходят в сеть лишний раз.
    Результат упрощается до ~10 м: трассы ORS бывают плотными, и копить их
    в out_geom курьера (растёт с каждой выдачей) незачем.
    """
    key = _geom_key(points)
    hit = _GEOM_CACHE.get(key)
    if hit and time.time() - hit[0] < _GEOM_TTL:
        return hit[1]
    won = hedged_first(_routing_providers("geometry", points),
                       hedge_s=ROUTE_HEDGE_S, final_wait=ROUTE_FINAL_S)
    geom = _simplify_poly(won[1]) if won and won[1] else None
    if geom:
        if len(_GEOM_CACHE) >= _GEOM_CACHE_MAX:  # простая вытесняющая чистка
            oldest = min(_GEOM_CACHE, key=lambda k: _GEOM_CACHE[k][0])
            _GEOM_CACHE.pop(oldest, None)
        _GEOM_CACHE[key] = (time.time(), geom)
    return geom
