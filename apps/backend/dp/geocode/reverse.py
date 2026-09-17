from ..core import hedged_first, log
from .base import YANDEX_KEY
from .nominatim import _reverse_nominatim
from .photon import _reverse_photon
from .yandex import _yandex_legacy, _yandex_v1

# ---------- обратный геокодинг тем же каскадом ----------


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


def reverse_geocode(lat, lng):
    """Адрес по координате (клик по карте) каскадом Яндекс → Nominatim → Photon.

    Пустой ответ ступени ('' — рядом нет дома/улицы в базе провайдера)
    считается промахом: хедж запускает следующего, а не фиксирует пустоту."""
    def _rev(fn):
        def wrapped():
            v = fn(lat, lng)
            return [v] if v and v.strip() else []
        return wrapped
    items = hedged_first([_rev(_reverse_yandex), _rev(_reverse_nominatim), _rev(_reverse_photon)],
                         hedge_s=1.5, final_wait=3.0) or []
    return items[0].strip() if items else ""
