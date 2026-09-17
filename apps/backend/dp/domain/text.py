"""Домен: текстовые правила (экранирование разметки, русская плюрализация)."""


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _plural(n, forms):
    """Русское склонение: _plural(3, ("заказ", "заказа", "заказов")) -> "заказа"."""
    n = abs(n)
    if n % 100 in (11, 12, 13, 14):
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if n % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]
