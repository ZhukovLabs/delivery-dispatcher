"""Трекер простоя: выдача у депо и доставка по гео-точке."""
import time

from .planstate import _bump, _ev
from .config import CFG, _now, log
from .bot_flow import _bot_ask_kb, _bot_ask_text
from .state import STATE, _home_point
from .adapters.telegram import _tg_answer_cb, _tg_edit_msg, _tg_send, _tg_send_kb
from .domain.geo import haversine_km

_BOT_ASK_AFTER_S = 30   # столько секунд курьер стоит у адреса, прежде чем бот спросит


TG_GEO_FRESH = 600      # гео свежая для расчётов <= 10 мин
TG_GEO_AT_PLACE = 0.15  # ближе 150 м = «на месте» (депо/заказ)
_LOAD_DWELL_S = 120     # столько нужно простоя у точки, чтобы считать выдачу состоявшейся


def _courier_out_orders(c):
    """Заказы «у курьера»: выданы и ещё не закрыты диспетчером."""
    return [o for o in STATE["orders"]
            if (o.get("status") or "ready") == "out"
            and (o.get("assigned") or "") == c.get("id")]


def _courier_has_out(c):
    return bool(_courier_out_orders(c))


def _load_track(c, pos, now=None):
    """Трекер «заказы отдали»: курьер обязан реально постоять у своей точки.

    Ложные срабатывания отсекаются тремя способами: позиция сглажена медианой
    (GPS-прыжок не доезжает до точки), нужен непрерывный простой _LOAD_DWELL_S
    (заезд мимо не считается), и у курьера должны быть выданные заказы.
    """
    chat = c.get("tg_chat_id") or ""
    home = _home_point(c)
    if not chat or not home:
        return
    if not _courier_has_out(c):
        STATE["tg_load"].pop(chat, None)  # партия закрыта — готовимся к следующей
        return
    now = now or time.time()
    rec = STATE["tg_load"].setdefault(chat, {"since": None, "loaded_at": None})
    if rec["loaded_at"]:
        return
    if haversine_km(pos, home) <= TG_GEO_AT_PLACE:
        rec["since"] = rec["since"] or now
        if now - rec["since"] >= _LOAD_DWELL_S:
            rec["loaded_at"] = now
            log.info("load tracked: %s получил заказы у точки «%s»", c.get("name"), home.get("name"))
            _bump()
    else:
        rec["since"] = None  # отошёл, не дождавшись выдачи — отсчёт заново


_DELIVER_DWELL_S = 90  # сколько стоять у адреса, чтобы расчёт счёл заказ доставленным


def _deliver_track(c, pos, now=None):
    """Вывод «курьер отвёз заказ» ПО ГЕО — для расчёта возврата и вопросов бота.

    Заказ считается развезённым в расчёте, когда курьер непрерывно простоял
    _DELIVER_DWELL_S в радиусе TG_GEO_AT_PLACE от адреса. Статус заказа при
    этом НЕ меняется — его по-прежнему закрывает диспетчер вручную.
    Заезд мимо без остановки не считается (счётчик простоя сбрасывается).

    Здесь же бот спрашивает «доставлен?» — раз за заезд: после _BOT_ASK_AFTER_S
    простоя в радиусе, повторный вопрос только после выезда и нового заезда.
    Ответ «да, ещё везу» глушит вопрос до конца текущего заезда.
    """
    chat = c.get("tg_chat_id") or ""
    if not chat:
        return
    out_orders = _courier_out_orders(c)
    st = STATE["tg_deliv"].setdefault(chat, {})
    alive = {o["id"] for o in out_orders}
    for k in list(st):  # закрытые диспетчером записи чистим
        if k not in alive:
            st.pop(k, None)
            STATE["tg_ask"].get(chat, {}).pop(k, None)
    if not out_orders:
        STATE["tg_deliv"].pop(chat, None)
        return
    now = now or time.time()
    for o in out_orders:
        if o.get("lat") is None or o.get("lng") is None:
            continue  # без координат адрес не сверить — поллер крашить нельзя
        rec = st.setdefault(o["id"], {})
        if haversine_km(pos, o) <= TG_GEO_AT_PLACE:
            rec["since"] = rec.get("since") or now
            if (now - rec["since"] >= _BOT_ASK_AFTER_S and not rec.get("asked")
                    and o["id"] not in STATE["tg_ask"].get(chat, {})):
                rec["asked"] = True
                mid = _tg_send_kb(chat, _bot_ask_text(o), _bot_ask_kb(o["id"]))
                if mid is not None or not CFG["tg_poll"]:
                    STATE["tg_ask"].setdefault(chat, {})[o["id"]] = {
                        "msg": mid or 0, "stage": "ask"}
                    log.info("bot ask: %s у «%s» — спросили «доставлен?»",
                             c.get("name"), o.get("address"))
                    _ev("bot", f"спросил {c.get('name')}: «{o.get('address')}» — доставлен?")
            if not rec.get("at") and now - rec["since"] >= _DELIVER_DWELL_S:
                rec["at"] = now
                log.info("deliver tracked: %s был у адреса «%s» — из расчёта возврата",
                         c.get("name"), o.get("address"))
                _ev("sys", f"{c.get('name')} был у адреса «{o.get('address')}»")
                _bump()
        else:
            # выехал из радиуса — заезд закрыт: снимаем простой и закрываем
            # висящий вопрос, чтобы следующий заезд спросил заново
            rec.pop("since", None)
            rec.pop("asked", None)
            pend = STATE["tg_ask"].get(chat, {}).pop(o["id"], None)
            if pend and pend.get("msg"):
                _tg_edit_msg(chat, pend["msg"],
                             "Курьер отъехал от адреса — спрошу при следующем заезде.")
