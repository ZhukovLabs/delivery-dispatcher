"""Прикрепление геометрии к готовому плану (для карты)."""
from concurrent.futures import ThreadPoolExecutor

from .config import log
from .geometry import routing_geometry
from .state import STATE
from .util import haversine_km

def _attach_geometry(plan):
    """Геометрия маршрутов (для линий на карте), по точке выдачи курьера.

    Заезды запрашиваются ПАРАЛЛЕЛЬНО (до 4 одновременно): при деградации
    роутеров последовательная довеска тянула каждую линию до таймаута каскада.
    Уже готовая геометрия не пересчитывается.
    """
    jobs = []
    for r in plan["routes"]:
        home = r.get("home_point") or STATE["depot"]
        for t in r.get("trips", []):
            if t.get("geometry"):
                continue
            seq = [home] + [{"lat": s["lat"], "lng": s["lng"]}
                            for s in t["stops"]] + [home]
            jobs.append((t, seq))

    def fetch(job):
        t, seq = job
        return t, (routing_geometry(seq) if plan["routing"] == "roads" else None)

    if jobs:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for t, geom in ex.map(fetch, jobs):
                t["geometry"] = geom
        for r in plan["routes"]:
            for t in r.get("trips", []):
                if plan["routing"] == "roads" and not t.get("geometry"):
                    log.warning("geometry: маршрут %s без дорог (роутеры недоступны) "
                                "— на карте будет прямыми", r.get("courier_name"))
