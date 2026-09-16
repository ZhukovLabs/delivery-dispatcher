# -*- coding: utf-8 -*-
"""Геокодер: локальный индекс улиц (Overpass), Nominatim/Photon, номера домов."""
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
import threading
import time
from collections import OrderedDict

import requests
from fastapi import APIRouter

from .core import CFG, DATA_DIR, STATE, haversine_km, log
from .shims import flaskish, jsonify, request

r = APIRouter()

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


UA = {"User-Agent": "delivery-dispatcher/1.0 (local admin tool)"}
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter")
HOUSES_CACHE = {}  # street_lower -> (ts, houses[(num, lat, lng)])
HOUSES_CACHE_MAX = 200
HOUSES_DISK = os.path.join(DATA_DIR, "houses_cache.json")
HOUSES_DISK_LOCK = threading.Lock()


def _houses_disk_load():
    """дома с Overpass качаются дорого — переживаем рестарты через диск"""
    try:
        with open(HOUSES_DISK, encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            try:
                HOUSES_CACHE[k] = (float(v["ts"]), [tuple(h) for h in v["houses"]])
            except Exception:  # noqa: BLE001
                continue
        log.info("houses cache: %d улиц с диска", len(HOUSES_CACHE))
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("houses cache не читается: %s", exc)


def _houses_disk_save():
    with HOUSES_DISK_LOCK:
        try:
            with open(HOUSES_DISK, "w", encoding="utf-8") as f:
                json.dump({k: {"ts": ts, "houses": hs} for k, (ts, hs) in HOUSES_CACHE.items()},
                          f, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("houses cache не записан: %s", exc)
HOUSES_TTL_FULL, HOUSES_TTL_EMPTY = 24 * 3600, 600


def osm_local_street_name(osm_type, osm_id):
    """Локальное (OSM) название улицы: «вуліца Цялегіна» -> «Цялегіна».
    Сначала — из индекса улиц (без сети, улицы Гомеля); иначе теги объекта из Overpass."""
    if not osm_type or not osm_id:
        return ""
    try:
        names = STREET_IDX["ways"].get(int(osm_id))
        if names and names[0]:
            return names[0]
    except (TypeError, ValueError):
        pass
    t = {"way": "way", "relation": "rel", "node": "node"}.get(osm_type)
    if not t:
        return ""
    q = f'[out:json][timeout:10];{t}({osm_id});out tags 1;'
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=15)
            r.raise_for_status()
            els = r.json().get("elements", [])
            if not els:
                return ""
            tags = els[0].get("tags") or {}
            # addr:street у домов хранит локальное (бел) имя — оно и нужно для сверки
            return _strip_street_type(tags.get("name") or tags.get("name:ru") or "")
        except Exception:  # noqa: BLE001
            continue
    return ""


def overpass_street_houses(base, clat, clng, alt=None):
    """Дома с номерами на улице base вокруг точки (clat, clng). Кэш 24 ч.
    alt — второе написание улицы (рус/бел): в OSM addr:street бывает «вуліца Цялегіна»,
    а lookup имени упал и вернул русское «Телегина» — ищем по обоим."""
    bases = [b for b in (base, alt) if b and b.strip()]
    key = "+".join(sorted(b.lower() for b in bases))
    now = time.time()
    if key in HOUSES_CACHE:
        ts, houses = HOUSES_CACHE[key]
        if now - ts < (HOUSES_TTL_FULL if houses else HOUSES_TTL_EMPTY):
            return houses
    rx = "|".join(re.escape(b) for b in bases)
    lows = [b.lower() for b in bases]
    q = (f'[out:json][timeout:15];'
         f'nwr["addr:housenumber"]["addr:street"~"{rx}",i]'
         f'({clat - 0.025:.5f},{clng - 0.04:.5f},{clat + 0.025:.5f},{clng + 0.04:.5f});out center 100;')
    houses = []
    answered = False  # Overpass ответил (пусть и пусто) — иначе сбой сети портит кэш
    for attempt in range(2):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, headers=UA, timeout=8)
                r.raise_for_status()
                answered = True
                for e in r.json().get("elements", []):
                    tags = e.get("tags") or {}
                    street = _strip_street_type(tags.get("addr:street") or "").lower()
                    if not any(b in street for b in lows):
                        continue
                    m = re.match(r"\s*(\d+)", str(tags.get("addr:housenumber") or ""))
                    if not m:
                        continue
                    c = e.get("center") or e
                    if c.get("lat") is None or (c.get("lon") is None and c.get("lng") is None):
                        continue
                    houses.append((int(m.group(1)), float(c["lat"]),
                                   float(c.get("lon", c.get("lng")))))
                break
            except Exception:  # noqa: BLE001
                continue
        if answered:
            break
        time.sleep(2)  # обе зеркала заняты: короткая пауза и ещё попытка
    if not answered:
        return []  # сбой сети — не кэшируем его как «домов нет»
    if len(HOUSES_CACHE) >= HOUSES_CACHE_MAX:
        HOUSES_CACHE.clear()  # улиц немного: проще сбросить, чем вести LRU
    HOUSES_CACHE[key] = (now, houses)
    _houses_disk_save()
    return houses


def interp_house(houses, num):
    """(lat, lng, note) для дома num по известным домам улицы."""
    exact = [h for h in houses if h[0] == num]
    if exact:
        return exact[0][1], exact[0][2], ""
    same = sorted(h for h in houses if h[0] % 2 == num % 2)
    lo = [h for h in same if h[0] < num]
    hi = [h for h in same if h[0] > num]
    if lo and hi:
        a, b = max(lo), min(hi)
        t = (num - a[0]) / (b[0] - a[0])
        return (a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t,
                f"точка между домами {a[0]} и {b[0]}")
    if lo or hi:
        n = max(lo) if lo else min(hi)
        return n[1], n[2], f"рядом с домом {n[0]}"
    return None


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


_NOM_LAST = [0.0]  # темп 1 запрос/сек к Nominatim: спим только остаток, а не вслепую
_GEO_CACHE = OrderedDict()  # готовые ответы геокодера: адреса не двигаются
_GEO_CACHE_MAX = 300
_GEO_TTL = 24 * 3600


def search_nominatim(q, lat, lng, radius_km, bounded=True):
    wait = 1.05 - (time.monotonic() - _NOM_LAST[0])
    if wait > 0:
        time.sleep(wait)
    _NOM_LAST[0] = time.monotonic()
    params = {"q": q, "format": "json", "limit": 12, "addressdetails": 1,
              "accept-language": "ru", "countrycodes": "by"}
    if bounded:
        params["viewbox"] = _bbox(lat, lng, radius_km)
        params["bounded"] = 1
    resp = requests.get("https://nominatim.openstreetmap.org/search",
                        params=params, headers=UA, timeout=7)
    resp.raise_for_status()
    out = []
    for it in resp.json():
        addr = it.get("address") or {}
        road = _strip_street_type(addr.get("road") or addr.get("pedestrian") or "")
        hn = addr.get("house_number") or ""
        # Nominatim кладёт сельсовет в city, а сам посёлок — в village: деревня важнее
        place = _clean_place(addr.get("village") or addr.get("hamlet") or addr.get("town")
                             or addr.get("city") or addr.get("municipality") or "")
        state = addr.get("state") or ""
        label = _place_label(road or _clean_place(it.get("name") or ""), place, hn, is_street=bool(road))
        plat, plng = float(it["lat"]), float(it.get("lng") or it.get("lon"))
        out.append({"label": label,
                    "lat": plat, "lng": plng, "hn": hn, "road": road,
                    "place": place, "kind": it.get("type") or "",
                    "state": state,
                    "osm_type": it.get("osm_type") or "", "osm_id": it.get("osm_id") or ""})
    return out


def search_photon(q, lat, lng, radius_km=None):
    params = {"q": q, "lat": lat, "lon": lng, "limit": 12, "lang": "default"}
    if radius_km:
        params["bbox"] = _bbox(lat, lng, radius_km)
    resp = requests.get("https://photon.komoot.io/api",
                        params=params, headers=UA, timeout=6)
    resp.raise_for_status()
    out = []
    for f in resp.json().get("features", []):
        p = f.get("properties") or {}
        lon, plat = f.get("geometry", {}).get("coordinates", [0, 0])
        street = _strip_street_type(p.get("street") or (p.get("osm_value") in ("street", "residential", "house") and p.get("name")) or "")
        hn = p.get("housenumber") or ""
        if street:
            place = p.get("city") or p.get("town") or ""
        else:  # населённый пункт: деревня/местность важнее района и сельсовета
            place = p.get("locality") or p.get("village") or p.get("city") or p.get("town") or ""
        place = _clean_place(place)
        state = p.get("state") or ""
        label = _place_label(street or _clean_place(p.get("name") or ""), place, hn, is_street=bool(street))
        out.append({"label": label,
                    "lat": plat, "lng": lon, "hn": hn, "street": street,
                    "place": place, "kind": p.get("osm_value") or "",
                    "state": state})
    return out


V1_IMP = {"house": 0.40, "building": 0.35, "entrance": 0.30, "street": 0.15,
          "residential": 0.15, "suburb": 0.10, "neighbourhood": 0.10, "quarter": 0.10,
          "administrative": 0.30, "village": 0.30, "hamlet": 0.28, "town": 0.30}
V2_IMP = {"house": 0.42, "house-number": 0.42, "building": 0.35, "street": 0.20,
          "residential": 0.18, "suburb": 0.10, "district": 0.10}


def reverse_geocode(lat, lng):
    """Адрес по координате (для кликов по карте): «Телегина 9, Гомель» или ''."""
    try:
        resp = requests.get("https://nominatim.openstreetmap.org/reverse",
                            params={"lat": lat, "lon": lng, "format": "json",
                                    "zoom": 18, "addressdetails": 1, "accept-language": "ru"},
                            headers=UA, timeout=8)
        resp.raise_for_status()
        addr = (resp.json() or {}).get("address") or {}
        road = _strip_street_type(addr.get("road") or addr.get("pedestrian") or "")
        hn = addr.get("house_number") or ""
        city = addr.get("city") or addr.get("town") or addr.get("village") or ""
        label = _place_label(road or "", city, hn, is_street=bool(road)) if road or hn else ""
        return label
    except Exception:  # noqa: BLE001
        pass
    return ""


GOMEL_BBOX = (52.325, 30.78, 52.61, 31.14)  # S, W, N, E — город с Новобелицей
STREET_IDX = {"ts": 0.0, "names": {}, "ways": {}, "alt": {}}  # names: lower(name)->(shown,lat,lng); ways: osm_id -> (local, ru); alt: lower -> другое написание


def _rebuild_alt():
    """Пары написаний (рус <-> бел) из ways: для интерполяции домов по опечаткам."""
    alt = {}
    for loc, ru in STREET_IDX["ways"].values():
        if loc and ru:
            alt[ru.lower()] = loc
            alt[loc.lower()] = ru
    STREET_IDX["alt"] = alt
STREET_IDX_TTL = 24 * 3600
STREET_IDX_LOCK = threading.Lock()


def _fill_street_index(network=True):
    """Тело загрузки индекса улиц; вызывать только под STREET_IDX_LOCK.
    network=False — только дисковый кэш (для запросов геокода, без задержек на сеть)."""
    if STREET_IDX["names"] and time.time() - STREET_IDX["ts"] < STREET_IDX_TTL:
        return STREET_IDX["names"]
    path = os.path.join(DATA_DIR, "streets_cache.json")
    try:  # дисковый кэш переживает рестарты (формат v2: names + ways)
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < STREET_IDX_TTL:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("v") == 3:
                STREET_IDX["names"] = {k: tuple(v) for k, v in data["names"].items()}
                STREET_IDX["ways"] = {int(k): tuple(v) for k, v in data["ways"].items()}
                _rebuild_alt()
                STREET_IDX["ts"] = os.path.getmtime(path)
                return STREET_IDX["names"]
    except Exception:  # noqa: BLE001
        pass
    s, w, n, e = GOMEL_BBOX
    q = (f'[out:json][timeout:25];'
         f'way["highway"~"^(residential|tertiary|secondary|primary|unclassified|living_street|pedestrian|service)$"]["name"]'
         f'({s},{w},{n},{e});out tags center 8000;')
    acc = {}  # lower -> [shown, sum_lat, sum_lng, cnt]
    for url in (OVERPASS_URLS if network else []):
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=15)
            r.raise_for_status()
            for el in r.json().get("elements", []):
                tags = el.get("tags") or {}
                c = el.get("center") or {}
                if not c.get("lat"):
                    continue
                loc = _strip_street_type(tags.get("name") or "")
                ru = _strip_street_type(tags.get("name:ru") or "")
                if el.get("id") and (loc or ru):
                    STREET_IDX["ways"][el["id"]] = (loc, ru)
                for nm in (tags.get("name:ru"), tags.get("name")):
                    nm = _strip_street_type(nm or "")
                    if len(nm) < 3:
                        continue
                    k = nm.lower()
                    a = acc.setdefault(k, [nm, 0.0, 0.0, 0])
                    a[1] += float(c["lat"]); a[2] += float(c.get("lon", 0)); a[3] += 1
            break
        except Exception:  # noqa: BLE001
            continue
    if acc:  # пустой ответ не затирает то, что уже есть
        STREET_IDX["names"] = {k: (v[0], v[1] / v[3], v[2] / v[3]) for k, v in acc.items()}
        STREET_IDX["ts"] = time.time()
        _rebuild_alt()
        log.info("street index: %d улиц", len(STREET_IDX["names"]))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"v": 3, "names": STREET_IDX["names"], "ways": STREET_IDX["ways"]},
                          fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass
    return STREET_IDX["names"]


def _load_street_index():
    """Именованные улицы Гомеля одним Overpass-запросом; кэш в памяти, на диске и на сутки.
    Photon не индексирует name:ru, Nominatim не умеет префиксы — этот индекс закрывает both."""
    with STREET_IDX_LOCK:
        return _fill_street_index()


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


def _search_local_streets(token, limit=6):
    """Улицы города по token: точное/префикс/подстрока, затем опечатки (левенштейн).
    Индекс грузится в фоне? Не ждём — просто без локальных подсказок."""
    t = (token or "").lower().strip()
    if len(t) < 3:
        return []
    if not (STREET_IDX["names"] and time.time() - STREET_IDX["ts"] < STREET_IDX_TTL):
        if not STREET_IDX_LOCK.acquire(blocking=False):
            return []
        try:
            _fill_street_index(network=False)  # диск-кэш мгновенно; сеть — только фоновый поток
        finally:
            STREET_IDX_LOCK.release()
    hits = []
    fuzzy = []
    tm = _typo_max(t)
    for k, (shown, lat, lng) in STREET_IDX["names"].items():
        if k.startswith(t):
            hits.append((0 if k == t else 1, 0, len(k), shown, lat, lng))
        elif len(t) >= 4 and t in k:
            hits.append((2, 0, len(k), shown, lat, lng))
        elif tm and _lev(t, k, tm) <= tm:
            fuzzy.append((3, _lev(t, k, tm), len(k), shown, lat, lng))
    hits.sort(key=lambda x: (x[0], x[2]))
    res = hits[:limit]
    if len(res) < limit and fuzzy:
        fuzzy.sort(key=lambda x: (x[1], x[2]))
        res += fuzzy[:limit - len(res)]
    return [(h[3], h[4], h[5]) for h in res]


def _tok(s):
    # ё -> е: «еремино» должен находить «Ерёмино»
    return [t.replace("ё", "е") for t in re.split(r"[^а-яёa-z0-9]+", (s or "").lower()) if len(t) >= 3]


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
    try:
        # два геокодера — параллельно; для запроса с домом параллельно же
        # прогреваем кэш домов вероятной улицы (Overpass медленный, но теперь
        # он не блокирует: к моменту интерполяции кэш уже горячий)
        with ThreadPoolExecutor(3) as ex:
            f_nom = ex.submit(search_nominatim, q, lat, lng, 25.0)
            f_ph = ex.submit(search_photon, q, lat, lng)
            f_houses = (ex.submit(overpass_street_houses, _strip_street_type(q_words[-1]), lat, lng)
                        if qnum and q_words else None)
            v1 = f_nom.result()
            try:
                v2 = f_ph.result()
            except Exception:  # noqa: BLE001
                v2 = []
            if f_houses is not None:
                try:
                    f_houses.result()
                except Exception:  # noqa: BLE001
                    pass
        # «еремино, школьная 13» Nominatim в таком порядке не находит —
        # пробуем «школьная 13, еремино» (улица+дом вперёд)
        if qnum and len(q_words) >= 2 and not v1:
            v1 = search_nominatim(f"{q_words[-1]} {qnum}, {' '.join(q_words[:-1])}", lat, lng, 25.0)

        def dist_of(it):
            return haversine_km({"lat": lat, "lng": lng}, {"lat": it["lat"], "lng": it["lng"]})

        # в городе мало совпадений — ищем по всей Гомельской области
        if sum(1 for it in v1 + v2 if dist_of(it) <= 25) < 4:
            v3 = search_nominatim(q, lat, lng, 0, bounded=False)
        else:
            v3 = []

        def _ok(it, dist):
            if dist <= 25:        # город и ближние пригороды — всегда подходят
                return True
            if dist > 170:        # дальше края области не нужно
                return False
            return "гомельск" in (it.get("state") or "").lower()

        scored = []
        seen = {}
        seen_lbl = {}
        exact_house = False
        for src, items, imp in (("v1", v1, V1_IMP), ("v2", v2, V2_IMP), ("v3", v3, V1_IMP)):
            for it in items:
                dist = dist_of(it)
                if not _ok(it, dist):
                    continue
                # ищут конкретный дом — районы и области не предлагать
                if qnum and it["kind"] in ("administrative", "county", "state", "region"):
                    continue
                # близкие весят дёшево, за городом — вполцены
                w = dist * 0.3 if dist <= 25 else 7.5 + (dist - 25) * 0.12
                importance = imp.get(it["kind"], 0.05)
                score = w + (1.0 - importance)
                # релевантность: слова запроса против адреса (точное слово весит сильнее префикса)
                if q_words:
                    hay = set(_tok(f"{it['label']} {it.get('road') or ''} {it.get('place') or ''}"))
                    for t in q_words:
                        if t in hay:
                            score -= 1.5
                        elif any(h.startswith(t) for h in hay):
                            score -= 0.8
                        elif any(_word_like(t, h) for h in hay):
                            score -= 0.5   # опечатка — тоже релевантно, но слабее
                if qnum:
                    if _same_house(qnum, it["hn"]):
                        score -= 0.6          # точное совпадение номера дома
                        exact_house = True
                    elif not it["hn"]:
                        score += 0.35         # ищут дом, а это просто улица
                    else:
                        score += 0.15         # чужой номер — шум
                key = (round(it["lat"], 5), round(it["lng"], 5))
                if key in seen and seen[key] <= score:
                    continue
                lbl = it["label"].split("(")[0].strip().lower()  # один адрес — одна строка
                if lbl in seen_lbl:
                    continue
                seen[key] = score
                seen_lbl[lbl] = score
                scored.append({"label": it["label"], "lat": it["lat"], "lng": it["lng"],
                               "km": round(dist), "_s": score})
        # все слова запроса обязаны найтись в адресе (с допуском опечаток):
        # «еремино, школьная 13» не должно давать «Улукаўскі, ул. Школьная, 13»
        # ведущая цифра запроса («3-я авиационная») — часть имени улицы:
        # фильтр должен требовать её в адресе, иначе сыплются 1-я/2-я
        lead_ord = re.match(r"\s*(\d{1,2})\s*[-–—]?\s*([а-яё]{0,2})\s", q.lower() + " ")
        ord_num = int(lead_ord.group(1)) if lead_ord and 1 <= int(lead_ord.group(1)) <= 30 else None
        fw = q_words + ([f"{ord_num}-я"] if ord_num else [])
        if fw and scored:
            def _hit_all(x):
                hay = set(_tok(x["label"].split("(")[0]))
                return all(any(_word_like(t, h) for h in hay) for t in fw)
            survived = [x for x in scored if _hit_all(x)]
            if survived:
                scored = survived
        # префиксы («телег…») геокодеры не умеют — локальный индекс улиц города
        if q_words and not qnum:
            for shown, slat, slng in _search_local_streets(q_words[0]):
                lbl_txt = _place_label(shown, "Гомель", "", True)
                lbl = lbl_txt.split("(")[0].strip().lower()
                if seen_lbl.get(lbl, 99) <= 2.5:
                    continue
                d = haversine_km({"lat": lat, "lng": lng}, {"lat": slat, "lng": slng})
                if d > 30:
                    continue
                seen_lbl[lbl] = 2.5
                scored.append({"label": lbl_txt, "lat": slat, "lng": slng,
                               "km": round(d), "_s": 2.5})
        # ведущая цифра или хвостовой номер («авиационная 3») —
        # в семье нумерованных улиц это улица, а не дом
        ordinal_hit = None
        if (qnum or ord_num) and q_words:
            mq = re.match(r"\d+", qnum or "")
            n_ord = ord_num or (int(mq.group(0)) if mq and 1 <= int(mq.group(0)) <= 30 else None)
            if n_ord:
                for shown, slat, slng in _search_local_streets(q_words[0], 12):
                    if shown.lower().startswith(f"{n_ord}-"):
                        ordinal_hit = (shown, slat, slng)
                        break
        if ordinal_hit:
            shown, slat, slng = ordinal_hit
            lbl_txt = _place_label(shown, "Гомель", "", True)
            lbl = lbl_txt.split("(")[0].strip().lower()
            if seen_lbl.get(lbl, 99) > -1:
                d_ord = haversine_km({"lat": lat, "lng": lng}, {"lat": slat, "lng": slng})
                if d_ord <= 30:
                    seen_lbl[lbl] = -1
                    scored.insert(0, {"label": lbl_txt, "lat": slat, "lng": slng,
                                      "km": round(d_ord), "_s": -1})
        # Номер дома не найден геокодерами — интерполируем по соседним домам улицы
        if qnum and not exact_house and not ordinal_hit:
            def _word_hit(it):
                hay = set(_tok(f"{it.get('road') or ''} {it.get('place') or ''}"))
                return sum(1 for t in q_words if t in hay or any(h.startswith(t) for h in hay))
            cands = [it for it in v1 if it["kind"] in ("street", "residential") and it.get("road")]
            cands.sort(key=lambda it: -_word_hit(it))
            street_hit = cands[0] if cands else None
            if not street_hit and q_words:
                # геокодеры промахнулись (опечатка/префикс) — локальный индекс улиц
                loc = _search_local_streets(q_words[0], 1)
                if loc:
                    shown, slat, slng = loc[0]
                    street_hit = {"road": shown, "place": "Гомель",
                                  "lat": slat, "lng": slng, "osm_type": "", "osm_id": "",
                                  "_local": True}
            if street_hit:

                def _interp():
                    mnum = re.match(r"\d+", qnum)
                    if not mnum:
                        return None
                    base_ru = _strip_street_type(street_hit["road"])
                    base_osm = (osm_local_street_name(street_hit.get("osm_type"), street_hit.get("osm_id"))
                                or STREET_IDX["alt"].get(base_ru.lower()) or "")
                    houses = overpass_street_houses(base_osm or base_ru, street_hit["lat"], street_hit["lng"],
                                                    alt=base_ru if base_osm else None)
                    return interp_house(houses, int(mnum.group(0)))

                pt = None
                ex2 = ThreadPoolExecutor(1)  # бюджет на сетевые походы за домами
                try:
                    pt = ex2.submit(_interp).result(timeout=9)
                except FuturesTimeout:
                    pt = None
                finally:
                    ex2.shutdown(wait=False)
                if pt:
                    hlat, hlng, note = pt
                    base = _strip_street_type(street_hit["road"])
                    city = street_hit.get("place") or "Гомель"
                    suffix = f" ({note})" if note else ""
                    scored.insert(0, {"label": _place_label(base, city, qnum, True) + suffix,
                                      "lat": hlat, "lng": hlng, "_s": -1})
                else:
                    # дома не добыли за бюджет — хотя бы сама улица,
                    # но только если геокодеры её сами не дали (нет дубля)
                    if street_hit.get("_local"):
                        base = _strip_street_type(street_hit["road"])
                        city = street_hit.get("place") or "Гомель"
                        d = haversine_km({"lat": lat, "lng": lng},
                                         {"lat": street_hit["lat"], "lng": street_hit["lng"]})
                        scored.append({"label": _place_label(base, city, "", False),
                                       "lat": street_hit["lat"], "lng": street_hit["lng"],
                                       "km": round(d), "_s": 3.0 + d * 0.1})
        if not scored and len(q_words) > 1:  # «бобовичи советская» -> «бобовичи»
            try:
                v4 = search_nominatim(" ".join(q_words[:-1]), lat, lng, 25.0, bounded=False)
            except Exception:  # noqa: BLE001
                v4 = []
            for it in v4[:5]:
                d = dist_of(it)
                if d > 60:
                    continue
                lbl = it["label"].split("(")[0].strip().lower()
                if lbl in seen_lbl:
                    continue
                seen_lbl[lbl] = 99
                scored.append({"label": it["label"], "lat": it["lat"], "lng": it["lng"],
                               "km": round(d), "_s": 3.0 + d * 0.1})
        if not scored:  # запасной вариант без рамки — только Беларусь
            scored = [{"label": it["label"], "lat": it["lat"], "lng": it["lng"], "_s": 99}
                      for it in search_nominatim(q, lat, lng, 25.0, bounded=False)[:5]]
        scored.sort(key=lambda x: x["_s"])
        payload = [{k: v for k, v in x.items() if k != "_s"} for x in scored[:7]]
        if payload:  # пустой ответ не кэшируем: часто это «индекс ещё не прогрелся»
            _GEO_CACHE[gkey] = (time.time(), payload)
            _GEO_CACHE.move_to_end(gkey)
            while len(_GEO_CACHE) > _GEO_CACHE_MAX:
                _GEO_CACHE.popitem(last=False)
        return jsonify(payload)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Геокодер недоступен: {e}"}), 502


def _warm_street_index():
    # Облачный инстанс: Overpass может отвергнуть первый запрос (rate-limit) —
    # повторяем с нарастающей паузой, пока индекс не соберётся.
    for attempt, delay in enumerate((5, 60, 300, 900), start=1):
        _load_street_index()
        if STREET_IDX["names"]:
            return
        log.warning("street index empty (attempt %d), retry in %ss", attempt, delay)
        time.sleep(delay)
    log.error("street index failed — prefix search degraded")



def _geocode_center():
    d = STATE.get("depot")
    if d:
        return float(d["lat"]), float(d["lng"])
    return 52.4345, 31.0137  # центр Гомеля по умолчанию
