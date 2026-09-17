import math

from dp.geocode.parse import (_bbox, _clean_place, _extract_house, _lev,
                              _place_label, _same_house, _strip_street_type,
                              _suggest_normalize, _tok, _typo_max,
                              _word_like)


def test_strip_street_type():
    assert _strip_street_type("ул. Советская") == "Советская"
    assert _strip_street_type("улица Тельмана") == "Тельмана"
    assert _strip_street_type("Советская улица") == "Советская"
    assert _strip_street_type("проспект Ленина") == "Ленина"
    assert _strip_street_type("") == ""


def test_extract_house():
    assert _extract_house("ул. Советская 12") == "12"
    assert _extract_house("Советская 12/2") == "12/2"
    assert _extract_house("Советская 12 - 4") == "12-4"
    assert _extract_house("Советская") == ""


def test_same_house():
    assert _same_house("12", "12")
    assert _same_house("12", "12/2")
    assert _same_house("12", "12к3")
    assert not _same_house("12", "13")
    assert not _same_house("", "12")


def test_bbox():
    lat, lng, r = 52.0, 31.0, 1.11
    dlng = r / (111.0 * math.cos(math.radians(lat)))
    parts = [float(x) for x in _bbox(lat, lng, r).split(",")]
    assert parts == [lng - dlng, lat + 0.01, lng + dlng, lat - 0.01]


def test_levenshtein():
    assert _lev("кот", "код") == 1
    assert _lev("абв", "абв") == 0
    assert _lev("a", "abcdef", 2) == 3
    assert _lev("abcdef", "a", 2) == 3


def test_typo_max():
    assert _typo_max("ул") == 0
    assert _typo_max("улица") == 0
    assert _typo_max("тельмана") == 1
    assert _typo_max("проспекты") == 2


def test_word_like():
    assert _word_like("еремино", "ерёмино")
    assert _word_like("дом", "дома")
    assert _word_like("тел", "тельмана")
    assert not _word_like("котов", "домов")


def test_clean_place():
    assert _clean_place("Поколюбичский сельский Совет") == "Поколюбичский"
    assert _clean_place("Ильич") == "Ильич"


def test_place_label():
    assert _place_label("Тельмана", "Гомель", "19") == "Гомель, ул. Тельмана, 19"
    assert _place_label("проспект Ленина", "") == "проспект Ленина"
    assert _place_label("Советская", "Советская") == "ул. Советская"
    assert _place_label("", "", "") == "точка"


def test_suggest_normalize():
    assert _suggest_normalize(
        "улица Тельмана, Гомель, 1") == "Гомель, ул. Тельмана, 1"
    assert _suggest_normalize(
        "1, улица Тельмана, Гомель") == "1, ул. Тельмана, Гомель"
    assert _suggest_normalize("") == ""


def test_tok_normalizes_yo_and_drops_short():
    assert _tok("Ерёмино, 17!") == ["еремино"]
    assert _tok("ул. Тельмана") == ["тельмана"]
