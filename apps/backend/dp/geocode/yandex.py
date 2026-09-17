import re

import requests

from ..core import log
from .base import GEO_FINAL_S, UA, YANDEX_KEY
from .parse import _clean_place, _extract_house, _strip_street_type

# ---------- провайдеры прямого поиска: единый формат ----------
# {label, lat, lng, hn, road, place, kind, state}


def _yandex_to_item(name, desc, kind, lat, lng):
    """Общий форматтер яндекс-результатов (v1 и легаси дают name/description)."""
    desc = re.sub(r"^Беларусь,?\s*", "", (desc or "").strip())
    desc = re.sub(r"^(Гомельская область|Гомельский район),?\s*", "", desc)
    city = desc.split(",")[0].strip()
    name = (name or "").strip()
    label = f"{city}, {name}" if city and city.lower() != name.lower() else name
    hn = _extract_house(name)
    road = _strip_street_type(re.sub(r"\s*\d+[а-яa-z]*(\s*[/-]\s*\d+)?\s*$", "", name))
    kind = {"house": "house", "street": "street", "locality": "village",
            "district": "suburb", "area": "suburb"}.get(kind, kind or "")
    return {"label": label, "lat": lat, "lng": lng,
            "hn": hn, "road": road, "place": _clean_place(city),
            "kind": kind, "state": "Гомельская область"}


def _yandex_v1(geocode, spn, results, kind=None):
    """Официальный HTTP API Геокодера /v1 (требует ключ, координаты «lng lat»)."""
    params = {"apikey": YANDEX_KEY, "geocode": geocode, "format": "json",
              "results": results, "lang": "ru_RU"}
    if spn:
        params["ll"], params["spn"] = spn
    if kind:
        params["kind"] = kind
    resp = requests.get("https://geocode-maps.yandex.ru/v1/", params=params,
                        headers=UA, timeout=GEO_FINAL_S)
    resp.raise_for_status()
    feats = (resp.json() or {}).get("features") or []
    out = []
    for f in feats:
        geo = f.get("geometry") or {}
        pos = geo.get("coordinates") or []
        if geo.get("type") != "Point" or len(pos) != 2:
            continue
        p = f.get("properties") or {}
        out.append(_yandex_to_item(p.get("name"), p.get("description"),
                                   p.get("kind"), float(pos[1]), float(pos[0])))
    return out


def _yandex_legacy(geocode, spn, results, kind=None):
    """Легаси /1.x работает без ключа — страховка, пока /v1 молчит (403)."""
    params = {"geocode": geocode, "format": "json",
              "results": results, "lang": "ru_RU"}
    if spn:
        params["ll"], params["spn"] = spn
    if kind:
        params["kind"] = kind
    resp = requests.get("https://geocode-maps.yandex.ru/1.x/",
                        params=params, headers=UA, timeout=GEO_FINAL_S)
    resp.raise_for_status()
    members = ((resp.json().get("response") or {})
               .get("GeoObjectCollection", {}).get("featureMember") or [])
    out = []
    for m in members:
        g = m.get("GeoObject") or {}
        pos = (g.get("Point", {}).get("pos") or "").split()
        if len(pos) != 2:
            continue
        meta = (g.get("metaDataProperty") or {}).get("GeocoderMetaData") or {}
        out.append(_yandex_to_item(g.get("name"), g.get("description"),
                                   meta.get("kind"), float(pos[1]), float(pos[0])))
    return out


def _yandex(geocode, spn, results, kind=None):
    """Каскад внутри яндекс-ступени: официальный /v1, за ним легаси /1.x."""
    if not YANDEX_KEY:
        return _yandex_legacy(geocode, spn, results, kind)
    try:
        return _yandex_v1(geocode, spn, results, kind)
    except Exception as exc:  # noqa: BLE001
        log.warning("geocoder yandex /v1: %s — перехожу на легаси /1.x", exc)
        return _yandex_legacy(geocode, spn, results, kind)


def search_yandex(q, lat, lng):
    return _yandex(q, (f"{lng},{lat}", "0.35,0.35"), 7)
