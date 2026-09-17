import requests

from .base import GEO_FINAL_S, UA
from .parse import _clean_place, _place_label, _strip_street_type


def search_photon(q, lat, lng):
    resp = requests.get("https://photon.komoot.io/api",
                        params={"q": q, "lat": lat, "lon": lng, "limit": 7, "lang": "default"},
                        headers=UA, timeout=GEO_FINAL_S)
    resp.raise_for_status()
    out = []
    for f in resp.json().get("features", []):
        p = f.get("properties") or {}
        lon, plat = f.get("geometry", {}).get("coordinates", [0, 0])
        street = _strip_street_type(p.get("street") or
                                    (p.get("osm_value") in ("street", "residential", "house")
                                     and p.get("name")) or "")
        hn = p.get("housenumber") or ""
        place = _clean_place(p.get("city") or p.get("town") or ""
                             if street else
                             (p.get("locality") or p.get("village")
                              or p.get("city") or p.get("town") or ""))
        label = _place_label(street or _clean_place(p.get("name") or ""), place, hn,
                             is_street=bool(street))
        out.append({"label": label, "lat": plat, "lng": lon, "hn": hn,
                    "road": street, "place": place,
                    "kind": p.get("osm_value") or "", "state": p.get("state") or ""})
    return out


def _reverse_photon(lat, lng):
    resp = requests.get("https://photon.komoot.io/reverse",
                        params={"lat": lat, "lon": lng, "limit": 1, "lang": "default"},
                        headers=UA, timeout=GEO_FINAL_S)
    resp.raise_for_status()
    feats = resp.json().get("features") or []
    if not feats:
        return ""
    p = feats[0].get("properties") or {}
    street = _strip_street_type(p.get("street") or "")
    hn = p.get("housenumber") or ""
    city = p.get("city") or p.get("town") or p.get("locality") or ""
    return _place_label(street or "", city, hn, is_street=bool(street)) if street or hn else ""
