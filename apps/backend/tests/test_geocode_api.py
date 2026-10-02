"""Регрессия /api/geocode: каскад провайдеров не должен падать на вызове
hedged_first (баг: вызов без обязательных hedge_s/final_wait давал 502
«Геокодер недоступен» на каждый ввод адреса)."""
from types import SimpleNamespace

from dp.geocode import api
from dp.shims_state import reset_request_ctx, set_request_ctx


def _call(q):
    tok = set_request_ctx(SimpleNamespace(query_params={"q": q}), None, {})
    try:
        return api.geocode()
    finally:
        reset_request_ctx(tok)


def _item(label="Гомель, ул. Терегина 5", lat=52.44, lng=31.02):
    return {"label": label, "lat": lat, "lng": lng, "hn": "5",
            "road": "Терегина", "place": "Гомель", "kind": "house",
            "state": "Гомельская область"}


def test_geocode_returns_items(monkeypatch):
    api._GEO_CACHE.clear()
    monkeypatch.setattr(api, "suggest_yandex", lambda q, lat, lng: [])
    monkeypatch.setattr(api, "search_yandex", lambda q, lat, lng: [_item()])
    monkeypatch.setattr(api, "search_nominatim", lambda q, lat, lng: [])
    monkeypatch.setattr(api, "search_photon", lambda q, lat, lng: [])
    out = _call("Терегина")
    assert isinstance(out, list) and out
    assert out[0]["label"].startswith("Гомель")
    assert "lat" in out[0] and "lng" in out[0]


def test_geocode_all_providers_down(monkeypatch):
    """Все провайдеры упали → пустой список, а не 502."""
    api._GEO_CACHE.clear()
    monkeypatch.setattr(api, "suggest_yandex", lambda q, lat, lng: [])

    def boom(*a, **kw):
        raise RuntimeError("провайдер недоступен")

    monkeypatch.setattr(api, "search_yandex", boom)
    monkeypatch.setattr(api, "search_nominatim", boom)
    monkeypatch.setattr(api, "search_photon", boom)
    out = _call("Терегина")
    assert out == []


def test_geocode_suggest_first(monkeypatch):
    """Подсказки саджеста идут первыми, геокодер дополняет координатами."""
    api._GEO_CACHE.clear()
    monkeypatch.setattr(api, "suggest_yandex",
                        lambda q, lat, lng: ["Гомель, улица Терегина"])
    monkeypatch.setattr(api, "search_yandex", lambda q, lat, lng: [_item()])
    monkeypatch.setattr(api, "search_nominatim", lambda q, lat, lng: [])
    monkeypatch.setattr(api, "search_photon", lambda q, lat, lng: [])
    out = _call("Терегина")
    assert out and out[0] == {"label": "Гомель, улица Терегина"}
