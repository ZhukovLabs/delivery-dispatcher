import json
import re

import requests

from ..core import log
from .base import UA, YANDEX_KEY
from .parse import _suggest_normalize, _tok, _word_like


def _suggest_v1(q, ll, spn):
    """Официальный Геосаджест /v1/suggest: JSON с address.formatted_address и uri."""
    resp = requests.get("https://suggest-maps.yandex.ru/v1/suggest",
                        params={"apikey": YANDEX_KEY, "text": q, "lang": "ru_RU",
                                "ll": ll, "spn": spn, "print_address": 1},
                        headers=UA, timeout=3.0)
    resp.raise_for_status()
    data = resp.json()
    # API отвечает полем results (в старых версиях было items) — принимаем оба
    items = (data.get("results") or data.get("items")) if isinstance(data, dict) else data
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


def _fetch(q, ll, spn):
    """Официальный /v1/suggest, за ним легаси suggest-geo."""
    try:
        if not YANDEX_KEY:
            raise RuntimeError("не задан YANDEX_GEOCODER_KEY")
        raw = _suggest_v1(q, ll, spn)
    except Exception as exc:  # noqa: BLE001
        log.warning("geosuggest /v1: %s — перехожу на легаси suggest-geo", exc)
        raw = []
    if not raw:
        raw = _suggest_legacy(q, ll, spn)
    return raw


def _relevant(raw, q):
    """Все слова запроса (с допуском опечаток) есть хоть в одной подсказке."""
    toks = _tok(q)
    if not toks:
        return bool(raw)

    def hit(lbl):
        hay = set(_tok(lbl))
        return all(any(_word_like(t, h) for h in hay) for t in toks)

    return any(hit(lbl) for lbl in raw)


def suggest_yandex(q, lat, lng):
    """Подсказки Геосаджеста при печати: только тексты, координат нет —
    их добирает геокодер, когда пользователь выбрал подсказку.
    Официальный /v1/suggest; при ошибке или пустом ответе (так ведёт себя
    отклонённый ключ) — легаси suggest-geo. ll+spn держат подсказки в Гомеле.
    Саджест не понимает склонённое название пункта («Еремина, школьная 13»
    даёт Турку и Мозырь): если ответ нерелевантен, повторяем запрос,
    заменив окончание слов -а на -о (именительная форма: «Еремино»)."""
    ll, spn = f"{lng},{lat}", "0.4,0.4"
    raw = _fetch(q, ll, spn)
    if not _relevant(raw, q):
        variant = re.sub(r"\b([а-яё]{4,})а\b", r"\1о", q, flags=re.I)
        if variant and variant != q:
            raw2 = _fetch(variant, ll, spn)
            if _relevant(raw2, q):
                raw = raw2
    return [lbl for lbl in (_suggest_normalize(x) for x in raw) if lbl]
