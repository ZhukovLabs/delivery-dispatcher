# -*- coding: utf-8 -*-
"""Геокодер: каскад провайдеров Яндекс → Nominatim → Photon.

Правило каскада («подстраховка»): следующий провайдер стартует, когда
предыдущий не ответил за GEO_HEDGE_S; побеждает первый ответивший, при
одновременности — более приоритетный. Пустой ответ — тоже промах: ждём
следующего. Обратный геокодинг (клик по карте) идёт тем же каскадом.
"""
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict

import requests
from fastapi import APIRouter

from .core import STATE, haversine_km, hedged_first, log
from .shims import flaskish, jsonify, request

r = APIRouter()

UA = {"User-Agent": "delivery-dispatcher/1.0 (local admin tool)",
      # ключ ограничен по Referer — серверный геокодер шлёт заголовок сам
      "Referer": "https://barak-dispatcher.vercel.app"}
GEO_HEDGE_S = 2.0    # столько ждём провайдера, прежде чем подстраховать следующим
GEO_FINAL_S = 4.0    # максимум ожидания после старта последнего провайдера
YANDEX_KEY = os.environ.get("YANDEX_GEOCODER_KEY", "")


def _strip_street_type(s):
    s = re.sub(r"^(улица|вуліца|ул\.|ulitsa|ul\.|проспект|пр-т|переулок|пер\.|бульвар|площадь|пл\.)\s*",
               "", (s or "").strip(), flags=re.I)
    # «Советская улица» -> «Советская» (проспекты/площади не трогаем: тип — часть имени)
    return re.sub(r"\s*(улица|вуліца)$", "", s, flags=re.I).strip()


def _extract_house(q):
    m = re.search(r"(\d+[а-яa-zA-Z]*(?:\s*[/-]\s*\d+)?)\s*$", q.strip())
    return m.group(1).replace(" ", "") if m else ""


def _same_house(qnum, hnum):
    if not qnum or not hnum:
        return False
    a = str(hnum).lower().replace(" ", "")
    b = qnum.lower()
    return a == b or a.split("/")[0] == b.split("/")[0] or a.split("к")[0] == b.split("к")[0]


def _bbox(lat, lng, radius_km):
    """Рамка (viewbox/bbox для Nominatim/Photon) радиусом radius_km вокруг точки."""
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * math.cos(math.radians(lat)) or 1.0)
    return f"{lng - dlng},{lat + dlat},{lng + dlng},{lat - dlat}"


_STREET_TYPES = re.compile(r"(проспект|праспект|площадь|плошча|бульвар|шоссе|тракт|"
                           r"переулок|завулак|набережная|спуск|линия)", re.I)


def _clean_place(p):
    """«Поколюбичский сельский Совет» -> «Поколюбичский»."""
    return re.sub(r"\s*(сельский совет|сельсовет|сельскі савет)$", "", (p or "").strip(), flags=re.I)


def _place_label(street, place, hn="", is_street=True):
    """«Гомель, ул. Тельмана, 19». Микрорайоны (Мельников Луг и пр.) не показываем."""
    name = (street or "").strip()
    if is_street and name and not _STREET_TYPES.search(name):
        name = "ул. " + name
    if hn:
        name = f"{name}, {hn}" if name else str(hn)
    place = _clean_place(place)
    if place and place.lower() != (street or "").strip().lower():
        return f"{place}, {name}" if name else place
    return name or place or "точка"


def _hedged(providers, hedge_s=GEO_HEDGE_S, final_wait=GEO_FINAL_S):
    """Каскад геокодера поверх общего hedged_first (живёт в core, чтобы не
    тянуть цикл импортов). Возвращает список (может быть пустым)."""
    return hedged_first(providers, hedge_s=hedge_s, final_wait=final_wait) or []


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


_NOM_LAST = [0.0]  # темп 1 запрос/сек к Nominatim: спим только остаток, а не вслепую


def search_nominatim(q, lat, lng):
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


# ---------- обратный геокодинг тем же каскадом ----------

def _suggest_v1(q, ll, spn):
    """Официальный Геосаджест /v1/suggest: JSON с address.formatted_address и uri."""
    resp = requests.get("https://suggest-maps.yandex.ru/v1/suggest",
                        params={"apikey": YANDEX_KEY, "text": q, "lang": "ru_RU",
                                "ll": ll, "spn": spn, "print_address": 1},
                        headers=UA, timeout=3.0)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items") if isinstance(data, dict) else data
    res = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        addr = (it.get("address") or {}).get("formatted_address") or ""
        if not addr:
            title = (it.get("title") or {}).get("text") or ""
            sub = (it.get("subtitle") or {}).get("text") or ""
            addr = f"{title}, {sub}".strip(", ")
        if addr:
            res.append(addr)
    return res


def _suggest_legacy(q, ll, spn):
    """Легаси suggest-geo (JSONP) работает без ключа — страховка /v1."""
    resp = requests.get("https://suggest-maps.yandex.ru/suggest-geo",
                        params={"text": q, "lang": "ru_RU", "ll": ll, "spn": spn},
                        headers=UA, timeout=3.0)
    resp.raise_for_status()
    raw = resp.text.strip()
    pre = "suggest.apply("
    if raw.startswith(pre) and raw.endswith(")"):
        raw = raw[len(pre):-1]
    data = json.loads(raw)

    def _flat(tokens):
        out = ""
        for t in tokens or []:
            if isinstance(t, str):
                out += t
            elif isinstance(t, (list, tuple)) and len(t) == 2 and isinstance(t[1], str):
                out += t[1]  # ["hl", "Телег"] — подсвеченный кусок строки
        return out.strip()

    res = []
    for it in (data[1] if isinstance(data, list) and len(data) > 1 else []):
        if not isinstance(it, (list, tuple)) or len(it) < 2:
            continue
        label = _flat(it[1])
        if label:
            res.append(label)
    return res


def _suggest_normalize(label):
    """«1, улица Тельмана, Гомель» -> «Гомель, ул. Тельмана, 1»."""
    parts = [p.strip() for p in label.split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) >= 2 and not re.match(r"^\d", parts[0]):
        parts = parts[1:] + [parts[0]]
    if len(parts) >= 3 and re.match(r"^\d", parts[1]):
        parts = [parts[0], parts[2], parts[1]]
    return ", ".join(re.sub(r"^улица\s+", "ул. ", p) for p in parts if p)


def suggest_yandex(q, lat, lng):
    """Подсказки Геосаджеста при печати: только тексты, координат нет —
    их добирает геокодер, когда пользователь выбрал подсказку.
    Официальный /v1/suggest; при ошибке — легаси suggest-geo. ll+spn держат
    подсказки в Гомеле."""
    ll, spn = f"{lng},{lat}", "0.4,0.4"
    try:
        if not YANDEX_KEY:
            raise RuntimeError("не задан YANDEX_GEOCODER_KEY")
        raw = _suggest_v1(q, ll, spn)
    except Exception as exc:  # noqa: BLE001
        log.warning("geosuggest /v1: %s — перехожу на легаси suggest-geo", exc)
        raw = _suggest_legacy(q, ll, spn)
    return [lbl for lbl in (_suggest_normalize(x) for x in raw) if lbl]

def _reverse_yandex(lat, lng):
    try:
        if not YANDEX_KEY:
            raise RuntimeError("не задан YANDEX_GEOCODER_KEY")
        items = _yandex_v1(f"{lng},{lat}", None, 1, kind="house")
    except Exception as exc:  # noqa: BLE001
        log.warning("reverse yandex /v1: %s — перехожу на легаси /1.x", exc)
        items = _yandex_legacy(f"{lng},{lat}", None, 1, kind="house")
    for it in items:
        return it["label"]
    return ""


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


def reverse_geocode(lat, lng):
    """Адрес по координате (клик по карте) каскадом Яндекс → Nominatim → Photon."""
    items = hedged_first([lambda: [_reverse_yandex(lat, lng)],
                          lambda: [_reverse_nominatim(lat, lng)],
                          lambda: [_reverse_photon(lat, lng)]],
                         hedge_s=1.5, final_wait=3.0) or []
    return items[0] if items else ""


# ---------- ---- API ---- ----------

_GEO_CACHE = OrderedDict()  # готовые ответы геокодера: адреса не двигаются
_GEO_CACHE_MAX = 300
_GEO_TTL = 24 * 3600


def _lev(a, b, maxd=2):
    """Левенштейн с отсечкой: >maxd — сразу maxd+1 (без полной матрицы)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > maxd:
        return maxd + 1
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        best = cur[0]
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
            if cur[j] < best:
                best = cur[j]
        if best > maxd:
            return maxd + 1
        prev = cur
    return prev[lb]


def _typo_max(word):
    """Допуск опечаток: 1 для слов >=6 букв, 2 для >=9; короткие — строго."""
    n = len(word)
    return 2 if n >= 9 else (1 if n >= 6 else 0)


def _word_like(t, h):
    """Слово запроса t против слова адреса h: точное/префикс или опечатка."""
    if t == h or h.startswith(t) or t.startswith(h):
        return True
    m = _typo_max(t)
    return m > 0 and _lev(t, h, m) <= m


def _tok(s):
    # ё -> е: «еремино» должен находить «Ерёмино»
    return [t.replace("ё", "е") for t in re.split(r"[^а-яёa-z0-9]+", (s or "").lower()) if len(t) >= 3]


def _suggest_safe(q, lat, lng):
    try:
        return suggest_yandex(q, lat, lng)[:3]
    except Exception as exc:  # noqa: BLE001
        log.warning("geosuggest: %s", exc)
        return []


@r.get("/api/geocode")
@flaskish
def geocode():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    lat, lng = _geocode_center()
    gkey = (q.lower(), round(lat, 3), round(lng, 3))
    hit = _GEO_CACHE.get(gkey)
    if hit and time.time() - hit[0] < _GEO_TTL:
        return jsonify(hit[1])
    qnum = _extract_house(q)
    q_words = _tok(re.sub(r"\d+[а-яa-z]*", " ", q))  # слова запроса без номера дома
    sugg_box = []  # подсказки саджеста гоняются параллельно каскаду геокодеров
    th = threading.Thread(target=lambda: sugg_box.extend(_suggest_safe(q, lat, lng)),
                          daemon=True)
    th.start()
    try:
        items = hedged_first([lambda: search_yandex(q, lat, lng),
                              lambda: search_nominatim(q, lat, lng),
                              lambda: search_photon(q, lat, lng)]) or []
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Геокодер недоступен: {e}"}), 502
    th.join(timeout=2)

    def dist_of(it):
        return haversine_km({"lat": lat, "lng": lng}, {"lat": it["lat"], "lng": it["lng"]})

    def _ok(it, dist):
        if dist <= 25:        # город и ближние пригороды — всегда подходят
            return True
        if dist > 170:        # дальше края области не нужно
            return False
        return "гомельск" in (it.get("state") or "").lower()

    scored, seen, seen_lbl = [], set(), set()
    for it in items:
        dist = dist_of(it)
        if not _ok(it, dist):
            continue
        # ищут конкретный дом — районы и области не предлагать
        if qnum and it["kind"] in ("administrative", "county", "state", "region"):
            continue
        key = (round(it["lat"], 5), round(it["lng"], 5))
        lbl = it["label"].split("(")[0].strip().lower()  # один адрес — одна строка
        if key in seen or lbl in seen_lbl:
            continue
        seen.add(key)
        seen_lbl.add(lbl)
        scored.append({"label": it["label"], "lat": it["lat"], "lng": it["lng"],
                       "km": round(dist)})
    # все слова запроса обязаны найтись в адресе (с допуском опечаток):
    # «еремино, школьная 13» не должно давать «Улукаўскі, ул. Школьная, 13»
    if q_words and scored:
        def _hit_all(x):
            hay = set(_tok(x["label"].split("(")[0]))
            return all(any(_word_like(t, h) for h in hay) for t in q_words)
        survived = [x for x in scored if _hit_all(x)]
        if survived:
            scored = survived
    # подсказки саджеста — первыми (координат нет: доберёт геокодер при выборе),
    # затем адреса геокодеров; дублей по строке — не держим
    payload = []
    for lbl in sugg_box:
        k = lbl.split("(")[0].strip().lower()
        if k in seen_lbl or any(x["label"].split("(")[0].strip().lower() == k for x in payload):
            continue
        payload.append({"label": lbl})
    payload += scored[:7 - len(payload)]
    if payload:  # пустой ответ не кэшируем
        _GEO_CACHE[gkey] = (time.time(), payload)
        _GEO_CACHE.move_to_end(gkey)
        while len(_GEO_CACHE) > _GEO_CACHE_MAX:
            _GEO_CACHE.popitem(last=False)
    return jsonify(payload)


def _geocode_center():
    d = STATE.get("depot")
    if d:
        return float(d["lat"]), float(d["lng"])
    return 52.4345, 31.0137  # центр Гомеля по умолчанию
