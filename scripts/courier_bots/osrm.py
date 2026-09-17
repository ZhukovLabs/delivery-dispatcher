import time

import requests

from .geo import log


class Osrm:
    """Дорожные трассы через OSRM (по умолчанию локальный; кэш + прямой фолбэк)."""

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
