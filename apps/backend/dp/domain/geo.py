"""Домен: гео-математика (чистые функции, без IO и состояния)."""
import math


def haversine_km(a, b):
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def _valid_latlng(lat, lng):
    return (-90 <= lat <= 90) and (-180 <= lng <= 180) and (lat != 0 or lng != 0)
