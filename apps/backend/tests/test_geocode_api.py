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


def test_geocode_apartment_details(monkeypatch):
    """«Телегина 15, кв 12, этаж 3»: провайдеры видят адрес без деталей,
    а предложенные адреса с домом получают их хвостом."""
    api._GEO_CACHE.clear()
    got = {}

    def sugg(q, lat, lng):
        got["sugg"] = q
        return ["Гомель, улица Терегина"]  # улица без дома — хвост не вешаем

    def ya(q, lat, lng):
        got["ya"] = q
        return [_item(label="Гомель, ул. Терегина, 15")]

    monkeypatch.setattr(api, "suggest_yandex", sugg)
    monkeypatch.setattr(api, "search_yandex", ya)
    monkeypatch.setattr(api, "search_nominatim", lambda q, lat, lng: [])
    monkeypatch.setattr(api, "search_photon", lambda q, lat, lng: [])
    out = _call("Терегина 15, кв 12, этаж 3")
    assert got["ya"] == "Терегина 15"      # геокодер искал без кв/этажа
    assert got["sugg"] == "Терегина 15"
    labels = [x["label"] for x in out]
    assert "Гомель, ул. Терегина, 15, кв 12, эт 3" in labels
    assert "Гомель, улица Терегина" in labels  # без дома — как была


def test_split_detail_variants():
    assert api._split_detail("Школьная 13, кв. 4") == ("Школьная 13", ["кв 4"])
    assert api._split_detail("Школьная 13 под 2 эт 5") == \
        ("Школьная 13", ["под 2", "эт 5"])
    assert api._split_detail("Школьная 13, квартира 12/3") == \
        ("Школьная 13", ["кв 12/3"])
    assert api._split_detail("Подгорная 12/1") == ("Подгорная 12/1", [])
    assert api._split_detail("Школьная 13а") == ("Школьная 13а", [])
