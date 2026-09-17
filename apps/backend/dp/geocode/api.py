import re
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter

from ..core import STATE, haversine_km, hedged_first, log
from ..shims import flaskish, jsonify, request
from .nominatim import search_nominatim
from .parse import _extract_house, _tok, _word_like
from .photon import search_photon
from .suggest import suggest_yandex
from .yandex import search_yandex

r = APIRouter()

# ---------- ---- API ---- ----------

_GEO_CACHE = OrderedDict()  # готовые ответы геокодера: адреса не двигаются
_GEO_CACHE_MAX = 300
_GEO_TTL = 24 * 3600


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
        _GEO_CACHE.move_to_end(gkey)  # LRU: свежеиспользованный живёт дольше
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
