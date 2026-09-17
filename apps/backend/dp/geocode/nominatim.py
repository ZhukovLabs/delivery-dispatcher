import threading
import time

import requests

from .base import GEO_FINAL_S, UA
from .parse import _bbox, _clean_place, _place_label, _strip_street_type

_NOM_LAST = [0.0]  # темп 1 запрос/сек к Nominatim: спим только остаток, а не вслепую
_NOM_LOCK = threading.Lock()  # глобальный темп один на всех: параллельные
                              # запросы выстраиваются, а не просачиваются мимо паузы


def search_nominatim(q, lat, lng):
    with _NOM_LOCK:
        wait = 1.05 - (time.monotonic() - _NOM_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _NOM_LAST[0] = time.monotonic()
    resp = requests.get("https://nominatim.openstreetmap.org/search",
                        params={"q": q, "format": "json", "limit": 7, "addressdetails": 1,
                                "accept-language": "ru", "countrycodes": "by",
                                "viewbox": _bbox(lat, lng, 25.0), "bounded": 1},
                        headers=UA, timeout=GEO_FINAL_S)
    resp.raise_for_status()
    out = []
    for it in resp.json():
        addr = it.get("address") or {}
        road = _strip_street_type(addr.get("road") or addr.get("pedestrian") or "")
        hn = addr.get("house_number") or ""
        # Nominatim кладёт сельсовет в city, а сам посёлок — в village: деревня важнее
        place = _clean_place(addr.get("village") or addr.get("hamlet") or addr.get("town")
                             or addr.get("city") or addr.get("municipality") or "")
        label = _place_label(road or _clean_place(it.get("name") or ""), place, hn,
                             is_street=bool(road))
        out.append({"label": label,
                    "lat": float(it["lat"]), "lng": float(it.get("lng") or it.get("lon")),
                    "hn": hn, "road": road, "place": place,
                    "kind": it.get("type") or "", "state": addr.get("state") or ""})
    return out


def _reverse_nominatim(lat, lng):
    resp = requests.get("https://nominatim.openstreetmap.org/reverse",
                        params={"lat": lat, "lon": lng, "format": "json",
                                "zoom": 18, "addressdetails": 1, "accept-language": "ru"},
                        headers=UA, timeout=GEO_FINAL_S)
    resp.raise_for_status()
    addr = (resp.json() or {}).get("address") or {}
    road = _strip_street_type(addr.get("road") or addr.get("pedestrian") or "")
    hn = addr.get("house_number") or ""
    city = addr.get("city") or addr.get("town") or addr.get("village") or ""
    return _place_label(road or "", city, hn, is_street=bool(road)) if road or hn else ""
