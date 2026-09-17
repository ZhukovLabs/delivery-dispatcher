import time

from .core import _esc, _plural

_PAY_GEO_FRESH_S = 15 * 60  # живая геопозиция старше 15 минут — уже не «текущая»


def _tg_route_message(courier_name, stops, me, origin=None, pos=None):
    """Текст+клавиатура TG-сообщения о выдаче: адреса с ETA, у каждого —
    ссылки «Маршрут: Яндекс | Google» с новой строки, внизу — кнопки
    «Весь маршрут» друг под другом.

    Стартовая точка всех маршрутов — текущая геопозиция курьера (pos,
    живая локация от бота, не старше 15 минут); если её нет — точка
    выдачи origin. Явная точка надёжнее «моего местоположения» карты:
    браузер внутри Telegram геолокацию не отдаёт. Используется и при
    отправке (assign), и при редактировании (return)."""

    start = None
    if pos and pos.get("lat") is not None and \
            time.time() - pos.get("ts", 0) <= _PAY_GEO_FRESH_S:
        start = f"{pos['lat']},{pos['lng']}"
    if not start:
        start = origin or "~"

    def _ya_link(sp):
        if sp.get("lat") is None or sp.get("lng") is None:
            return None
        return (f"https://yandex.ru/maps/?rtext={start}~{sp['lat']},{sp['lng']}"
                "&rtt=auto")

    def _gg_link(sp):
        if sp.get("lat") is None or sp.get("lng") is None:
            return None
        return (f"https://www.google.com/maps/dir/?api=1"
                + (f"&origin={start}" if start != "~" else "")
                + f"&destination={sp['lat']},{sp['lng']}&travelmode=driving")
    z_word = _plural(len(stops), ("заказ", "заказа", "заказов"))
    lines = [f"🛵 <b>{_esc(courier_name)}, в развозку</b>: {len(stops)} {z_word}"]
    for i, s in enumerate(stops, start=1):
        head = f"{i}. {_esc(s['address'])}"
        if s.get("eta_clock"):
            head += f" · ≈{s['eta_clock']}"
        lines.append(head)
        ya, gg = _ya_link(s), _gg_link(s)
        if ya and gg:
            lines.append(f"    Маршрут: <a href=\"{ya}\">Яндекс</a>"
                         f" | <a href=\"{gg}\">Google</a>")
    lines.append("Время приблизительное, следите за сообщениями.")
    if (me or {}).get("name") and (me or {}).get("phone"):
        lines.append(f"\nЕсть вопросы? - {_esc(me['name'])}, {me['phone']}")
    payload = {"text": "\n".join(lines), "parse_mode": "HTML"}
    pts = [f"{s['lat']},{s['lng']}" for s in stops
           if s.get("lat") is not None and s.get("lng") is not None]
    if len(pts) >= 2:  # маршрут строим минимум по двум точкам
        gg = ("https://www.google.com/maps/dir/?api=1"
              + (f"&origin={start}" if start != "~" else "")
              + "&destination=" + pts[min(len(pts), 10) - 1]
              + "&waypoints=" + "%7C".join(pts[:min(len(pts), 10) - 1])
              + "&travelmode=driving")
        kb = [[{"text": "Яндекс | Весь маршрут",
                "url": "https://yandex.ru/maps/?rtext="
                       + "~".join([start] + pts[:10]) + "&rtt=auto"}],
              [{"text": "Google | Весь маршрут", "url": gg}]]
        payload["reply_markup"] = {"inline_keyboard": kb}
    return payload
