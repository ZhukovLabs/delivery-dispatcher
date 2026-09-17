import json

import requests

from ..core import log
from .base import UA, YANDEX_KEY
from .parse import _suggest_normalize


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
