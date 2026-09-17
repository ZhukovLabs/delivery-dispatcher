import time

from .geo import log


def _confirm(sess, base, chat, oid, name):
    """Прожать диалог как курьер: «Да» -> «Подтвердить» (заказ закрыт
    доставленным). Работает, пока диалог жив — бот жмёт сразу после
    простоя у адреса, вопрос уже пришёл."""
    try:
        sess.post(base + "/api/sim/tgcb",
                  json={"chat_id": chat, "data": f"dlv:{oid}:y"}, timeout=15)
        time.sleep(0.4)
        r = sess.post(base + "/api/sim/tgcb",
                      json={"chat_id": chat, "data": f"dlv:{oid}:ok"}, timeout=15)
        if r.status_code == 200:
            log(f"{name}: доставил {oid[:6]} — подтвердил в диалоге")
    except Exception as e:  # noqa: BLE001
        log(f"{name}: confirm error {e!r}")


def push_geo(s, base, chat, lat, lng):
    return s.post(base + "/api/sim/geo",
                  json={"chat_id": chat, "lat": round(lat, 6),
                        "lng": round(lng, 6), "login": "courier-bot"},
                  timeout=15)
