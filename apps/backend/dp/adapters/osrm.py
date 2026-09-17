"""OSRM: резервные провайдеры матриц/геометрии (каскад с подстраховкой)."""
import math
import threading
import time

import requests

from ..config import log
from .http_hedge import hedged_first

OSRM_URLS = [
    "http://127.0.0.1:5000",                        # свой OSRM (docker, вся Беларусь)
    "https://routing.openstreetmap.de/routed-car",  # серверы сообщества OSM (FOSSGIS)
    "http://router.project-osrm.org",               # официальный демо-сервер
]
OSRM_NAMES = {OSRM_URLS[0]: "OSRM(local)", OSRM_URLS[1]: "OSRM(fossgis)",
              OSRM_URLS[2]: "OSRM(demo)"}
# каскад маршрутизации: 3 с на ступень, потом параллельно следующая; итоговый
# потолок ожидания — 8 с (дольше считает только полностью мёртвый интернет)
ROUTE_HEDGE_S = 3.0
ROUTE_FINAL_S = 8.0
def osrm_get(base, path, params):
    """Один запрос к конкретному OSRM-серверу. None при любом сбое."""
    try:
        resp = requests.get(f"{base}{path}", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == "Ok":
            return data
        log.warning("OSRM %s: code=%s", base, data.get("code"))
    except Exception as e:  # noqa: BLE001
        log.warning("OSRM %s недоступен: %s", base, e)
    return None


def _osrm_matrix_at(base, points):
    data = osrm_get(base, f"/table/v1/driving/{_osrm_coords(points)}",
                    {"annotations": "duration,distance"})
    durations = data and data.get("durations")
    if not durations or any(d is None for row in durations for d in row):
        return None, None
    distances = data.get("distances")
    if distances and any(d is None for row in distances for d in row):
        distances = None
    return durations, distances


def _osrm_geometry_at(base, points):
    data = osrm_get(base, f"/route/v1/driving/{_osrm_coords(points)}",
                    {"overview": "full", "geometries": "geojson"})
    try:
        return [[c[1], c[0]] for c in data["routes"][0]["geometry"]["coordinates"]]
    except (KeyError, IndexError, TypeError):
        return None


def _osrm_coords(points):
    return ";".join(f"{p['lng']:.6f},{p['lat']:.6f}" for p in points)

