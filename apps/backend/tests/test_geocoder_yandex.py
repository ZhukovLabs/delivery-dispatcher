"""Геокодер Яндекс /v1 c ключом JS API и format=json отвечает легаси-конвертом
response.GeoObjectCollection (а не GeoJSON features). Парсер обязан читать оба
формата — иначе ступень Яндекса всегда пустует и деревенские адреса не
резолвятся в координаты (nominatim/photon их не знают)."""
from dp.geocode import yandex


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


_ENVELOPE = {"response": {"GeoObjectCollection": {"featureMember": [
    {"GeoObject": {
        "name": "Школьная улица, 13",
        "description": "агрогородок Ерёмино, Гомельский район, Беларусь",
        "Point": {"pos": "30.946935 52.509145"},
        "metaDataProperty": {"GeocoderMetaData": {"kind": "house"}},
    }},
]}}}


def test_v1_parses_legacy_envelope(monkeypatch):
    monkeypatch.setattr(yandex, "YANDEX_KEY", "k")
    monkeypatch.setattr(yandex.requests, "get",
                        lambda *a, **kw: _FakeResp(_ENVELOPE))
    items = yandex._yandex_v1("ерёмино школьная 13", None, 5)
    assert len(items) == 1
    assert items[0]["label"] == "агрогородок Ерёмино, Школьная улица, 13"
    assert abs(items[0]["lat"] - 52.509145) < 1e-6
    assert abs(items[0]["lng"] - 30.946935) < 1e-6
    assert items[0]["kind"] == "house"


def test_v1_parses_geojson_features(monkeypatch):
    payload = {"features": [{
        "geometry": {"type": "Point", "coordinates": [30.9947, 52.4425]},
        "properties": {"name": "улица Телегина, 15",
                       "description": "Гомель, Беларусь", "kind": "house"},
    }]}
    monkeypatch.setattr(yandex, "YANDEX_KEY", "k")
    monkeypatch.setattr(yandex.requests, "get",
                        lambda *a, **kw: _FakeResp(payload))
    items = yandex._yandex_v1("Гомель, Телегина 15", None, 5)
    assert len(items) == 1
    assert items[0]["label"] == "Гомель, улица Телегина, 15"


def test_legacy_sends_apikey(monkeypatch):
    """Легаси /1.x без ключа теперь отвечает 400 Missing apikey."""
    seen = {}

    def fake_get(url, params=None, **kw):
        seen.update(params or {})
        return _FakeResp({"response": {"GeoObjectCollection": {"featureMember": []}}})

    monkeypatch.setattr(yandex, "YANDEX_KEY", "k")
    monkeypatch.setattr(yandex.requests, "get", fake_get)
    assert yandex._yandex_legacy("Гомель", None, 5) == []
    assert seen.get("apikey") == "k"
