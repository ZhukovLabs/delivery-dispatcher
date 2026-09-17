"""Матрица расстояний: кэш + каскад провайдеров ORS -> OSRM -> гаверсинус."""
import time

from ..config import log
from .ors import ORS_MAX_POINTS, ORS_STATE, _ors_available, ors_geometry, ors_matrix
from .osrm import OSRM_NAMES, OSRM_URLS, ROUTE_FINAL_S, ROUTE_HEDGE_S, _osrm_geometry_at, _osrm_matrix_at
from .http_hedge import hedged_first

_MATRIX_CACHE = {}          # ключ(точки) -> (ts, durations, distances, provider)
_MATRIX_TTL = 1800          # 30 минут: дорожная сеть не меняется так быстро
_MATRIX_CACHE_MAX = 40


def _matrix_key(points):
    # Ключ СТРОГО ПО ПОРЯДКУ точек: матрица индексна — один и тот же набор
    # точек в другом порядке это ДРУГАЯ матрица. Сортировка в ключе (как было)
    # молча склеивала разные порядки после удаления+пересоздания заказа.
    return tuple((round(p["lat"], 5), round(p["lng"], 5)) for p in points)


def _cache_matrix(key, value):
    if len(_MATRIX_CACHE) >= _MATRIX_CACHE_MAX:  # простая вытесняющая чистка
        oldest = min(_MATRIX_CACHE, key=lambda k: _MATRIX_CACHE[k][0])
        _MATRIX_CACHE.pop(oldest, None)
    _MATRIX_CACHE[key] = (time.time(), *value)


def _routing_providers(kind, points):
    """Ступени каскада маршрутизации в порядке приоритета. Каждая возвращает
    (метка_провайдера, значение) или None (промах): OSRM local → FOSSGIS →
    демо → ORS. ORS пропускаем сразу при исчерпанной квоте/отключении."""
    def osrm_step(base):
        def call():
            if kind == "matrix":
                dur, dist = _osrm_matrix_at(base, points)
                val = (dur, dist) if dur is not None else None
            else:
                val = _osrm_geometry_at(base, points)
            return (OSRM_NAMES[base], val) if val else None
        call.__name__ = f"routing:{OSRM_NAMES[base]}"
        return call

    def ors_step():
        def call():
            if len(points) > ORS_MAX_POINTS:
                return None
            if kind == "matrix":
                dur, dist = ors_matrix(points)
                val = (dur, dist) if dur is not None else None
            else:
                val = ors_geometry(points)
            return ("ORS", val) if val else None
        call.__name__ = "routing:ORS"
        return call

    return [osrm_step(b) for b in OSRM_URLS] + [ors_step()]


def routing_table(points):
    """Матрица времени/расстояния каскадом с подстраховкой (hedged):
    локальный OSRM → FOSSGIS → демо → ORS. Ступень, молчащая дольше
    ROUTE_HEDGE_S, подстраховывается следующей; побеждает первый ответ.

    Успешный результат кэшируется по набору точек (30 мин): повторный расчёт
    того же набора не тратит квоту внешних сервисов и занимает миллисекунды.
    Неудача НЕ кэшируется: транзиентный сбой каскада не «отравляет» кэш на
    полчаса фоллбеком — следующий расчёт снова пробует роутеры.
    Возвращает (durations|None, distances|None, provider).
    """
    key = _matrix_key(points)
    hit = _MATRIX_CACHE.get(key)
    if hit and time.time() - hit[0] < _MATRIX_TTL:
        ts, durations, distances, provider = hit
        return durations, distances, provider
    won = hedged_first(_routing_providers("matrix", points),
                       hedge_s=ROUTE_HEDGE_S, final_wait=ROUTE_FINAL_S)
    provider, (durations, distances) = won if won else (None, (None, None))
    if durations is None:
        log.warning("routing_table: каскад роутеров молчит (%d точек) — "
                    "фоллбек скорости, без кэша", len(points))
        return None, None, provider or "offline"
    _cache_matrix(key, (durations, distances, provider or "offline"))
    return durations, distances, provider or "offline"
