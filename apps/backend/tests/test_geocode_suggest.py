"""Регрессия геосаджеста: пустой ответ /v1 (так молча отвечает отклонённый
ключ) обязан подстраховываться легаси suggest-geo, иначе при печати адреса
нет ни одной подсказки."""
from dp.geocode import suggest
from dp.geocode.suggest import suggest_yandex


def test_v1_items_win(monkeypatch):
    monkeypatch.setattr(suggest, "YANDEX_KEY", "k")
    monkeypatch.setattr(suggest, "_suggest_v1", lambda q, ll, spn: ["Гомель, Ленина 1"])
    called = []
    monkeypatch.setattr(suggest, "_suggest_legacy",
                        lambda q, ll, spn: called.append(1) or ["Гомель, legacy 2"])
    out = suggest_yandex("Ленина", 52.4345, 31.0137)
    assert out == ["Ленина 1, Гомель"] and not called


def test_v1_empty_falls_to_legacy(monkeypatch):
    monkeypatch.setattr(suggest, "YANDEX_KEY", "dead-key")
    monkeypatch.setattr(suggest, "_suggest_v1", lambda q, ll, spn: [])
    monkeypatch.setattr(suggest, "_suggest_legacy",
                        lambda q, ll, spn: ["15, улица Телегина, Гомель"])
    out = suggest_yandex("Телегина", 52.4345, 31.0137)
    assert out == ["15, ул. Телегина, Гомель"]


def test_v1_error_falls_to_legacy(monkeypatch):
    monkeypatch.setattr(suggest, "YANDEX_KEY", "k")

    def boom(*a):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(suggest, "_suggest_v1", boom)
    monkeypatch.setattr(suggest, "_suggest_legacy", lambda q, ll, spn: ["Гомель, Ленина 1"])
    out = suggest_yandex("Ленина", 52.4345, 31.0137)
    assert out == ["Ленина 1, Гомель"]


def test_no_key_uses_legacy(monkeypatch):
    monkeypatch.setattr(suggest, "YANDEX_KEY", "")
    monkeypatch.setattr(suggest, "_suggest_legacy", lambda q, ll, spn: ["Гомель, Ленина 1"])
    out = suggest_yandex("Ленина", 52.4345, 31.0137)
    assert out == ["Ленина 1, Гомель"]
