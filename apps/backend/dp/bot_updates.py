"""Диспетчер входящих обновлений Telegram: гео, тексты, привязка."""
import json
import re
import sqlite3
import time

from .planstate import _bump, _ev
from .config import CFG, _now, log
from .db import _db, _db_lock, _speed_add
from .speed import _speed_geo_sample
from .state import STATE
from .tgapi import TG_TEST_REDIRECT, _tg_edit_msg, _tg_send
from .bot_dialog import _tg_callback
from .bot_dwell import _deliver_track, _load_track
from .bot_flow import _pay_method_label, _pay_set
from .bot_status import _auto_status_track
from .util import _esc

def _tg_handle_update(u):
    """Один апдейт от Telegram: текст (/start), геолокация или кнопка."""
    cb = u.get("callback_query")
    if cb:
        _tg_callback(cb)
        return
    msg = u.get("message") or u.get("edited_message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or "")
    if not chat_id:
        return
    frm = msg.get("from") or {}
    login = frm.get("username") or " ".join(
        filter(None, [frm.get("first_name"), frm.get("last_name")])).strip() or chat_id
    STATE["tg_seen"][chat_id] = {"chat_id": chat_id, "login": login[:64],
                                 "ts": time.time()}
    if len(STATE["tg_seen"]) > 50:  # храним только недавних
        for k in sorted(STATE["tg_seen"], key=lambda x: STATE["tg_seen"][x]["ts"])[:-50]:
            STATE["tg_seen"].pop(k, None)
            STATE["tg_nagged"].pop(k, None)  # антиспам-память чистим вместе

    courier = next((c for c in STATE["couriers"]
                    if (c.get("tg_chat_id") or "") == chat_id), None)
    # флоу оплаты: бот ждёт от курьера сумму числом. Диалог живёт под чатом
    # КУРЬЕРА; в тест-режиме человек пишет из редирект-чата — ремапим
    pay_chat = chat_id
    if TG_TEST_REDIRECT and chat_id == TG_TEST_REDIRECT and \
            pay_chat not in STATE.get("tg_pay", {}):
        for cand in STATE.get("tg_pay", {}):
            pay_chat = cand
            break
    pay = STATE.get("tg_pay", {}).get(pay_chat)
    if pay and not courier and pay_chat != chat_id:
        courier = next((c for c in STATE["couriers"]
                        if (c.get("tg_chat_id") or "") == pay_chat), None)
    if pay and (not pay.get("method")
                or time.time() - pay.get("ts", 0) > 1800):
        # зависший диалог (рестарт/полчаса тишины) — не мешаем остальному
        STATE["tg_pay"].pop(pay_chat, None)
        pay = None
    if pay:
        text = (msg.get("text") or "").strip()
        # границы числа: «50.123»/«-5»/«1,234» не должны схлопываться в
        # «50.12»/«5»/«1,23» — кривой ввод просим повторить целиком
        m = re.search(r"(?<![-\d.,])\d+(?:[.,]\d{1,2})?(?![\d.,])", text)
        if not m:
            _tg_send(chat_id,
                     "Не понял сумму — напишите числом, например: "
                     "<b>24.50</b> (или нажмите «Сумму не знаю»)")
            return
        amount = round(float(m.group(0).replace(",", ".")), 2)
        if not 0 < amount <= 1_000_000:
            _tg_send(chat_id, "Сумма странная — проверьте и напишите ещё раз")
            return
        _pay_set(pay["oid"], amount=amount)
        pend = STATE.get("tg_ask", {}).get(pay_chat, {}).get(pay["oid"])
        if pend:
            STATE["tg_ask"].get(pay_chat, {}).pop(pay["oid"], None)
        STATE["tg_pay"].pop(pay_chat, None)
        _tg_edit_msg(pay_chat, pay["msg"],
                     f"✅ Записано: <b>{_esc(pay.get('addr') or pay['oid'])}</b>"
                     f" доставлен. Оплата: <b>{_pay_method_label(pay['method'])}</b>, "
                     f"<b>{amount:g}</b>.")
        who = courier["name"] if courier else pay_chat
        _ev("bot", f"оплата: {_pay_method_label(pay['method']).lower()} "
                   f"{amount:g} — «{pay.get('addr') or pay['oid']}» ({who})")
        log.info("bot pay: заказ %s — %s %s", pay["oid"], pay["method"], amount)
        _bump()
        return
    loc = msg.get("location")
    # шум гео-тиков (2+ строки/сек при live-geo) — в debug, журнал читаем
    log.debug("tg upd: chat=%s %s%s bound=%s", chat_id,
             "edit " if u.get("edited_message") else "msg ",
             ("live-geo" if loc and loc.get("live_period") else
              "static-geo" if loc else "text"),
             courier["name"] if courier else "-")
    if loc:
        if courier:
            raw = {"lat": loc["latitude"], "lng": loc["longitude"],
                   "ts": time.time(), "live": bool(loc.get("live_period")),
                   "acc": loc.get("horizontal_accuracy") or 0}
            # анти-дребезг: буфер последних точек, сглаживание медианой.
            # Буфер длинный: тики бывают частые (боты 0.5 с) — окну текущей
            # скорости (240 с) нужен запас, реальным гео 8-15 с хватает с избытком
            hist = STATE["tg_pos"].get(chat_id, {}).get("hist", [])
            hist = [h for h in hist if raw["ts"] - h["ts"] <= 600][-520:]
            hist.append(raw)
            recent = [h for h in hist if raw["ts"] - h["ts"] <= 600][-3:]
            lats = sorted(h["lat"] for h in recent)
            lngs = sorted(h["lng"] for h in recent)
            smoothed = {"lat": lats[len(lats) // 2], "lng": lngs[len(lngs) // 2],
                        "ts": raw["ts"], "acc": raw["acc"]}
            # замер скорости — по сглаженному треку: одиночный GPS-прыжок
            # гасится медианой и в отрезок не попадает. Якорь замера двигается
            # только на «зрелые» точки (≥3 с от предыдущей): при частых тиках
            # (боты 0.5 с) соседние пары не набирают dt — меряем раз в 3+ с
            prev_s = STATE["tg_pos"].get(chat_id, {}).get("sprev")
            if prev_s:
                _speed_geo_sample(courier["id"], prev_s, smoothed)
                sprev_new = smoothed if smoothed["ts"] - prev_s["ts"] >= 3 else prev_s
            else:
                sprev_new = smoothed
            STATE["tg_pos"][chat_id] = {
                "lat": smoothed["lat"], "lng": smoothed["lng"],
                "ts": raw["ts"], "live": raw["live"], "acc": raw["acc"],
                "hist": hist, "sprev": sprev_new}
            _load_track(courier, smoothed, raw["ts"])
            _deliver_track(courier, smoothed, raw["ts"])
            _auto_status_track(courier, smoothed, raw["ts"])
            # диалоги «доставлен?» и трекеры простоя — в БД не реже раза в 15 с
            # (этот же поток обрабатывает нажатия кнопок — гонок нет)
            if raw["ts"] - STATE.get("_tg_persist_ts", 0) > 15:
                STATE["_tg_persist_ts"] = raw["ts"]
                try:
                    with _db_lock, _db() as cx:
                        cx.executemany(
                            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                            [("tg_ask", json.dumps(STATE["tg_ask"], ensure_ascii=False)),
                             ("tg_deliv",
                              json.dumps(STATE["tg_deliv"], ensure_ascii=False)),
                             ("tg_pay", json.dumps(STATE["tg_pay"], ensure_ascii=False))])
                except sqlite3.Error:
                    pass
            _bump(geo=True)  # движение курьера — карта обновится (хаб батчит ≥1 с)
        else:
            # live-локация шлёт правки каждые несколько секунд — «не привязан»
            # отправляем не чаще раза в 30 минут на чат
            now_ts = time.time()
            if now_ts - STATE["tg_nagged"].get(chat_id, 0) > 1800:
                STATE["tg_nagged"][chat_id] = now_ts
                _tg_send(chat_id,
                          f"Похоже, вас ещё не привязали к курьеру. Отправьте этот ID "
                          f"администратору: <code>{chat_id}</code>")
    elif (msg.get("text") or "").strip().startswith("/start"):
        _tg_send(chat_id,
                 "Привет! Это бот развозки.\n\n"
                 "Нужна <b>живая геолокация</b>:\n"
                 "скрепка → «Геолокация» → «Поделиться моей геолокацией» → "
                 "время <b>«Пока не отключу»</b>.\n\n"
                 "Тогда диспетчер видит вас на карте всю смену.\n\n"
                 f"Ваш ID: <code>{chat_id}</code>\n"
                 "Скажите его администратору, и вас подключат к курьеру.")

