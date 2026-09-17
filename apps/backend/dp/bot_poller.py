"""Long-poll getUpdates: единственный слушатель бота на процесс."""
import json
import threading
import time

import requests

from .config import CFG, log
from .bot_updates import _tg_handle_update
from .state import STATE
from .tgapi import _tg_api

def _tg_poll_loop():
    while True:
        try:
            r = requests.get(
                _tg_api("getUpdates"),
                params={"offset": STATE["tg_offset"], "timeout": 25,
                        "allowed_updates": json.dumps(
                            ["message", "edited_message", "callback_query"])},
                timeout=30)
            data = r.json()
            if not data.get("ok"):
                # 409 Conflict: бота уже слушает другой процесс. Не боремся за
                # getUpdates в лоб — ждём: второй инстанс умрёт и канал вернётся.
                if r.status_code == 409:
                    log.warning("tg poll: бот уже слушается другим процессом "
                                "(409) — повтор через 5 мин")
                    time.sleep(300)
                    continue
                log.warning("tg poll: api error %s", data.get("description"))
                time.sleep(10)
                continue
            for u in data.get("result", []):
                STATE["tg_offset"] = max(STATE["tg_offset"], u.get("update_id", 0) + 1)
                _tg_handle_update(u)
        except requests.RequestException as e:
            log.warning("tg poll: %s", e)
            time.sleep(5)
        except Exception as e:  # неожиданный формат — не роняем поллер
            log.warning("tg update parse: %s", e)
            time.sleep(2)


def _tg_start_polling():
    """Запуск поллера при старте, если задан токен бота."""
    if not CFG["tg_bot_token"]:
        return
    try:
        me = requests.get(_tg_api("getMe"), timeout=10).json().get("result") or {}
        STATE["tg_bot"] = "@" + me.get("username", "")
        log.info("tg bot: %s", STATE["tg_bot"])
    except requests.RequestException as e:
        log.warning("tg getMe failed: %s", e)
    if not CFG["tg_poll"]:
        log.info("tg bot: поллер выключен (tg_poll=0) — геолокации слушает "
                 "другой сервер")
        return
    threading.Thread(target=_tg_poll_loop, daemon=True).start()
