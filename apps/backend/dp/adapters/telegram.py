"""Telegram API-обёртки: отправка/редактирование, редирект тест-чата."""
import os

import requests

from ..config import CFG, log
from ..state import STATE
from ..domain.text import _esc, _plural

# --- Telegram: бот принимает геолокации курьеров ---------------------------------
TG_POS_TTL = 30 * 60  # локация старше 30 минут считается устаревшей
def _tg_api(method):
    return f"https://api.telegram.org/bot{CFG['tg_bot_token']}/{method}"


# Тест-режим: все диалоги бота (гео-запросы, «доставлен?», привязки) уходят
# одному живому человеку вместо реальных курьеров. Гео-конвейер при этом
# остаётся честным: каждый бот-курьер привязан к своему синтетическому chat_id,
# редирект происходит только в момент отправки сообщений.
TG_TEST_REDIRECT = os.environ.get("TG_TEST_REDIRECT", "").strip()


def _tg_out_chat(chat_id):
    """(адресат, префикс) для исходящего сообщения: в тест-режиме всё одному
    человеку, с пометкой, от какого курьера сообщение."""
    cid = str(chat_id)
    if TG_TEST_REDIRECT and cid != TG_TEST_REDIRECT:
        c = next((x for x in STATE["couriers"]
                  if str(x.get("tg_chat_id") or "") == cid), None)
        pref = f"[{_esc(c['name'])}] " if c else "[тест] "
        return TG_TEST_REDIRECT, pref
    return cid, ""
    return f"https://api.telegram.org/bot{CFG['tg_bot_token']}/{method}"
def _tg_send(chat_id, text):
    """Исходящее сообщение курьеру (ошибки не критичны — молча в лог)."""
    chat_id, pref = _tg_out_chat(chat_id)
    try:
        requests.post(_tg_api("sendMessage"),
                      json={"chat_id": chat_id, "text": pref + text, "parse_mode": "HTML"}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg sendMessage: %s", e)


def _tg_send_kb(chat_id, text, buttons):
    """Сообщение с инлайн-кнопками. buttons = [[{text, callback_data}, ...], ...].
    Возвращает message_id или None (не отправилось)."""
    chat_id, pref = _tg_out_chat(chat_id)
    try:
        r = requests.post(_tg_api("sendMessage"),
                          json={"chat_id": chat_id, "text": pref + text, "parse_mode": "HTML",
                                "reply_markup": {"inline_keyboard": buttons}}, timeout=5)
        data = r.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        log.warning("tg sendMessage kb: %s", data.get("description"))
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("tg sendMessage kb: %s", e)
    return None


def _tg_edit_msg(chat_id, message_id, text, buttons=None):
    """Правка сообщения бота (смена текста/кнопок). Ошибки молча в лог."""
    chat_id, pref = _tg_out_chat(chat_id)
    payload = {"chat_id": chat_id, "message_id": message_id,
               "text": pref + text, "parse_mode": "HTML"}
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(_tg_api("editMessageText"), json=payload, timeout=5)
        data = r.json()
        if not data.get("ok") and data.get("description") != "message is not modified":
            log.warning("tg editMessageText: %s", data.get("description"))
    except (requests.RequestException, ValueError) as e:
        log.warning("tg editMessageText: %s", e)


def _tg_answer_cb(callback_id, text=""):
    """Ответ на нажатие кнопки (закрывает «часики» у курьера)."""
    try:
        requests.post(_tg_api("answerCallbackQuery"),
                      json={"callback_query_id": callback_id, "text": text}, timeout=5)
    except requests.RequestException as e:
        log.warning("tg answerCallbackQuery: %s", e)
