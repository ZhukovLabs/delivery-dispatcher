"""Хелперы выдачи: синк живого TG-сообщения курьера с реальностью."""
import threading

import requests

from .core import CFG, STATE, _esc, _home_point, _me, _tg_send, log
from .routes_plan import _tg_route_message

def _tg_sync_route(cid, warn=None):
    """Живое TG-сообщение курьера догоняет реальность: пересобрать маршрут
    по оставшимся заказам (ссылки/кнопки обновятся) и предупредить курьера.

    Фоном — запрос диспетчера сеть Telegram не ждёт. warn=None — только
    правка сообщения (без отдельного предупреждения).
    """
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    chat = (courier or {}).get("tg_chat_id") or ""
    if not (courier and chat and CFG["tg_bot_token"]):
        return
    ref = STATE.get("tg_assign", {}).get(cid)
    if not warn and not ref:
        return
    me = _me()
    home = _home_point(courier)
    origin = (f"{home['lat']},{home['lng']}"
              if home.get("lat") is not None else None)
    pos = STATE["tg_pos"].get(chat)

    def _job():
        if warn:
            _tg_send(chat, warn)
        if not ref:
            return
        stops = [{"address": o["address"], "eta_clock": None,
                  "lat": o.get("lat"), "lng": o.get("lng")}
                 for o in STATE["orders"]
                 if o.get("assigned") == cid and (o.get("status") or "ready") == "out"]
        if stops:
            payload = _tg_route_message(courier["name"], stops, me,
                                        origin=origin, pos=pos)
        else:
            payload = {"text": f"📦 {_esc(courier['name'])}: все заказы"
                               " сняты с развозки", "parse_mode": "HTML"}
        payload.update({"chat_id": ref["chat"], "message_id": ref["mid"]})
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{CFG['tg_bot_token']}"
                "/editMessageText", json=payload, timeout=10)
            desc = resp.json().get("description", "")
            if "not modified" not in desc.lower() and not resp.json().get("ok"):
                log.warning("tg sync: не отредактировано (%s): %s",
                            courier["name"], desc[:200])
            if "message to edit not found" in desc.lower():
                STATE.get("tg_assign", {}).pop(cid, None)
        except (requests.RequestException, ValueError) as e:
            log.warning("tg sync: %s", e)

    threading.Thread(target=_job, daemon=True).start()
