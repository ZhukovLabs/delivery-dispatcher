"""Обработчик callback-кнопок бота (подтверждения, оплата, отмена)."""
import time

from .planstate import _invalidate_plan, _ev
from .config import _now, log
from .adapters.sqlite_repo import _persist_orders
from .state import STATE
from .adapters.telegram import (TG_TEST_REDIRECT, _tg_answer_cb, _tg_edit_msg,
                    _tg_send, _tg_send_kb)
from .bot_dwell import _courier_out_orders
from .bot_flow import (_CANCEL_REASONS, _bot_ask_kb, _bot_ask_text,
                       _bot_close_delivered, _bot_keep_rolling,
                       _pay_method_label, _pay_set, _tg_step_kb)
from .domain.text import _esc

def _tg_callback(cb):
    """Нажатие инлайн-кнопки курьером: «доставил?» → «точно?» → закрытие.

    Стадии: ask → confirm/refconfirm/noconfirm → ok/r:i/nok; «Назад»
    возвращает на шаг назад. Неизвестная кнопка или нажатие вне своей
    стадии диалог не ломает и данные не меняет.
    """
    data = cb.get("data") or ""
    cbid = cb.get("id") or ""
    msg = cb.get("message") or {}
    chat = str((msg.get("chat") or {}).get("id") or "")
    if not data.startswith("dlv:"):
        _tg_answer_cb(cbid)
        return
    parts = data.split(":", 2)
    if len(parts) < 3 or not parts[1]:
        _tg_answer_cb(cbid, "Кнопка не распознана")
        return
    _, oid, act = parts
    # тест-режим: кнопки жмёт живой человек в редирект-чате — возвращаем
    # диалог к синтетическому чату курьера, которому выдан заказ
    if TG_TEST_REDIRECT and chat == TG_TEST_REDIRECT:
        order0 = next((o for o in STATE["orders"] if o["id"] == oid), None)
        c0 = next((c for c in STATE["couriers"]
                   if c.get("id") == (order0 or {}).get("assigned")), None) \
            if order0 else None
        if c0 and c0.get("tg_chat_id"):
            chat = str(c0["tg_chat_id"])
        else:
            # заказ уже закрыт (стадии pay/pay_amount идут ПОСЛЕ закрытия —
            # это нормальный флоу оплаты, а не «неактуально»): ищем чат
            # курьера по самому диалогу — редирект-чат своих записей в
            # tg_ask не имеет
            for c_chat, dlg in STATE["tg_ask"].items():
                if oid in dlg:
                    chat = c_chat
                    break
    pend = STATE["tg_ask"].get(chat, {}).get(oid)
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    courier = next((c for c in STATE["couriers"]
                    if (c.get("tg_chat_id") or "") == chat), None)
    # стадии pay/pay_amount идут ПОСЛЕ закрытия заказа (аналитика оплаты):
    # заказа в STATE уже нет — это не «неактуально», а нормальный флоу
    paying = pend and pend.get("stage") in ("pay", "pay_amount")
    if (not pend or not courier
            or (not order and not paying)
            or (order and order.get("assigned") != courier.get("id"))):
        if pend:
            _tg_edit_msg(chat, pend["msg"],
                         "Этот вопрос уже неактуален — заказ закрыт диспетчером.")
            STATE["tg_ask"].get(chat, {}).pop(oid, None)
            STATE["tg_pay"].pop(chat, None)
        _tg_answer_cb(cbid, "Уже неактуально")
        return
    addr = _esc((order or {}).get("address") or pend.get("addr") or oid)
    if act == "y" and pend["stage"] == "ask":
        pend["stage"] = "confirm"
        _tg_edit_msg(chat, pend["msg"], f"Точно доставлен? Заказ: <b>{addr}</b>",
                     _tg_step_kb(oid, "✅ Подтвердить", "ok"))
        _tg_answer_cb(cbid)
    elif act == "ref" and pend["stage"] == "ask":
        pend["stage"] = "refconfirm"
        _tg_edit_msg(chat, pend["msg"], f"Точно отменяем? Заказ: <b>{addr}</b>",
                     _tg_step_kb(oid, "✅ Да, отменяем", "refyes"))
        _tg_answer_cb(cbid)
    elif act == "n" and pend["stage"] == "ask":
        pend["stage"] = "noconfirm"
        _tg_edit_msg(chat, pend["msg"], f"Точно ещё нет? Заказ: <b>{addr}</b>",
                     _tg_step_kb(oid, "✅ Да, ещё везу", "nok"))
        _tg_answer_cb(cbid)
    elif act == "nok" and pend["stage"] == "noconfirm":
        _bot_keep_rolling(chat, pend, oid, order, courier)
        _tg_answer_cb(cbid)
    elif act == "refyes" and pend["stage"] == "refconfirm":
        pend["stage"] = "reason"
        _tg_edit_msg(chat, pend["msg"],
                     f"Причина отмены: <b>{addr}</b>",
                     [[{"text": t, "callback_data": f"dlv:{oid}:r:{i}"}]
                      for i, t in enumerate(_CANCEL_REASONS)]
                     + [[{"text": "↩️ Назад", "callback_data": f"dlv:{oid}:no"}]])
        _tg_answer_cb(cbid)
    elif act.startswith("r:") and pend["stage"] == "reason":
        try:
            reason = _CANCEL_REASONS[int(act[2:])]
        except (IndexError, ValueError):
            reason = "Другое"
        ok, _ = _bot_close_delivered(oid, outcome="cancelled", reason=reason)
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        if ok:
            _tg_edit_msg(chat, pend["msg"],
                         f"🗑 Записано: <b>{addr}</b> — заказ отменён.\n"
                         f"Причина: <b>{_esc(reason)}</b>")
            _tg_answer_cb(cbid, "Заказ отменён ✓")
        else:
            _tg_edit_msg(chat, pend["msg"], "Не получилось закрыть — уже неактуален.")
            _tg_answer_cb(cbid, "Уже неактуально")
    elif act == "ok" and pend["stage"] == "confirm":
        pend["addr"] = order.get("address") or oid  # адрес для флоу оплаты
        ok, _ = _bot_close_delivered(oid)
        if ok:
            # заказ закрыт тут же, как раньше; дальше — только аналитика:
            # как оплатил клиент и сколько
            pend["stage"] = "pay"
            _tg_edit_msg(chat, pend["msg"],
                         f"✅ Записано: <b>{addr}</b> доставлен.\n\n"
                         "Как оплатил клиент?",
                         [[{"text": "💵 Наличными",
                            "callback_data": f"dlv:{oid}:pay:cash"}],
                          [{"text": "💳 Картой",
                            "callback_data": f"dlv:{oid}:pay:card"}],
                          [{"text": "⏭ Без оплаты / не важно",
                            "callback_data": f"dlv:{oid}:payskip"}]])
            _tg_answer_cb(cbid, "Заказ закрыт ✓")
        else:
            STATE["tg_ask"].get(chat, {}).pop(oid, None)
            _tg_edit_msg(chat, pend["msg"], "Не получилось закрыть — уже неактуален.")
            _tg_answer_cb(cbid, "Уже неактуально")
    elif act.startswith("pay:") and pend["stage"] == "pay":
        method = act[4:]
        if method not in ("cash", "card"):
            _tg_answer_cb(cbid, "Кнопка не распознана")
            return
        _pay_set(oid, method=method)
        STATE["tg_pay"][chat] = {"oid": oid, "method": method,
                                 "msg": pend["msg"], "addr": pend.get("addr") or oid,
                                 "ts": time.time()}
        pend["stage"] = "pay_amount"
        _tg_edit_msg(chat, pend["msg"],
                     f"✅ <b>{addr}</b> доставлен. Оплата: <b>"
                     f"{_pay_method_label(method)}</b>.\n\n"
                     "Напишите сумму числом в чат — например: <b>24.50</b>",
                     [[{"text": "⏭ Сумму не знаю",
                        "callback_data": f"dlv:{oid}:payskip"}]])
        _tg_answer_cb(cbid)
    elif act == "payskip" and pend["stage"] in ("pay", "pay_amount"):
        STATE["tg_ask"].get(chat, {}).pop(oid, None)
        STATE["tg_pay"].pop(chat, None)
        _tg_edit_msg(chat, pend["msg"],
                     f"✅ Записано: <b>{addr}</b> доставлен. Спасибо!")
        _tg_answer_cb(cbid, "Заказ закрыт ✓")
    elif act == "no":
        # «Назад»: на шаг диалога назад, диалог не закрываем
        if pend["stage"] in ("confirm", "refconfirm", "noconfirm"):
            pend["stage"] = "ask"
            _tg_edit_msg(chat, pend["msg"], _bot_ask_text(order), _bot_ask_kb(oid))
        elif pend["stage"] == "reason":
            pend["stage"] = "refconfirm"
            _tg_edit_msg(chat, pend["msg"], f"Точно отменяем? Заказ: <b>{addr}</b>",
                         _tg_step_kb(oid, "✅ Да, отменяем", "refyes"))
        _tg_answer_cb(cbid)
    else:
        # неизвестная кнопка или нажатие вне своей стадии (старый экран) —
        # диалог не трогаем, данные не меняем
        log.warning("tg cb: неизвестный act=%s stage=%s oid=%s chat=%s",
                    act, pend.get("stage"), oid, chat)
        _tg_answer_cb(cbid, "Кнопка не распознана")
