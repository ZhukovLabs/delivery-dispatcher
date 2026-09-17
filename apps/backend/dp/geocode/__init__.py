# -*- coding: utf-8 -*-
"""Геокодер: каскад провайдеров Яндекс → Nominatim → Photon.

Правило каскада («подстраховка»): следующий провайдер стартует, когда
предыдущий не ответил за GEO_HEDGE_S; побеждает первый ответивший, при
одновременности — более приоритетный. Пустой ответ — тоже промах: ждём
следующего. Обратный геокодинг (клик по карте) идёт тем же каскадом.
"""
from .base import GEO_FINAL_S, GEO_HEDGE_S, UA, YANDEX_KEY, _hedged
from .parse import (_STREET_TYPES, _bbox, _clean_place, _extract_house, _lev,
                    _place_label, _same_house, _strip_street_type,
                    _suggest_normalize, _tok, _typo_max, _word_like)
from .yandex import _yandex, _yandex_legacy, _yandex_to_item, _yandex_v1, search_yandex
from .nominatim import _NOM_LAST, _NOM_LOCK, _reverse_nominatim, search_nominatim
from .photon import _reverse_photon, search_photon
from .suggest import _suggest_legacy, _suggest_v1, suggest_yandex
from .reverse import _reverse_yandex, reverse_geocode
from .api import (_GEO_CACHE, _GEO_CACHE_MAX, _GEO_TTL, _geocode_center,
                  _suggest_safe, geocode, r)
