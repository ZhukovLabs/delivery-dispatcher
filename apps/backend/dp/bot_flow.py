"""Действия бота: закрытие заказа, диалоги «доставил?», оплата."""
import sqlite3
import time

from .planstate import _bump, _invalidate_plan, _ev
from .config import _now, log
from .db import _archive_order, _db, _db_lock, _persist_orders
from .state import STATE, _obj_point
from .tgapi import (_esc, _tg_answer_cb, _tg_edit_msg, _tg_send,
                    _tg_send_kb)
from .util import _plural

def _flip_return_route(courier_id):
    """Все заказы развозки закрыты: разворачиваем трассу — курьер едет домой
    по улицам, карта рисует возврат (ret_geom вместо out_geom)."""
    c = next((x for x in STATE["couriers"] if x["id"] == courier_id), None)
    if c and c.get("out_geom") and not any(
            o.get("assigned") == courier_id and o.get("status") == "out"
            for o in STATE["orders"]):
        c["ret_geom"] = list(reversed(c["out_geom"]))
        c.pop("out_geom", None)


def _bot_close_delivered(oid, outcome="delivered", reason=""):
    """Закрыть заказ по подтверждению КУРЬЕРА (без сессии): доставлен или
    отменён (с причиной из диалога бота).

    Тот же след, что у ручного закрытия диспетчером: архив, снятие из
    развозки, разворот трассы на возврат, инвалидация плана.
    """
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order or (order.get("status") or "ready") != "out":
        return False, ""
    cid = order.get("assigned") or ""
    c = next((x for x in STATE["couriers"] if x["id"] == cid), None)
    _archive_order(order, outcome, c["name"] if c else cid, cid, reason=reason)
    STATE["orders"] = [o for o in STATE["orders"] if o["id"] != oid]
    _flip_return_route(cid)
    _persist_orders()
    _invalidate_plan(drop_plan=True, pid=_obj_point(order))
    _bump()
    log.info("bot confirm: заказ %s (%s) закрыт курьером %s (%s)",
             oid, order.get("address"), c.get("name") if c else cid, outcome)
    who = c.get("name") if c else cid
    _ev("bot", (f"отменил «{order.get('address')}» — {who}, причина: {reason}"
                if outcome == "cancelled" else
                f"закрыл «{order.get('address')}» — {who} подтвердил"))
    return True, who


# Причины отмены — по частоте (чаще всего в начале, «Другое» всегда последним)
_CANCEL_REASONS = [
    "Долгое ожидание",
    "Человек не отвечает",
    "Просто отказ",
    "Неправильный заказ",
    "Плохое качество товара",
    "Не тот адрес",
    "Другое",
]


def _bot_ask_text(o):
    return (f"🛍 Кажется, заказ по адресу <b>{_esc(o.get('address') or '')}</b> "
            "доставлен. Это так?")


def _bot_ask_kb(oid):
    return [[{"text": "✅ Доставил", "callback_data": f"dlv:{oid}:y"}],
            [{"text": "❌ Нет", "callback_data": f"dlv:{oid}:n"}],
            [{"text": "🚫 Заказ отменён", "callback_data": f"dlv:{oid}:ref"}]]


def _tg_step_kb(oid, label, act):
    """Клавиатура шага-подтверждения: «<label>» и назад к вопросу."""
    return [[{"text": label, "callback_data": f"dlv:{oid}:{act}"}],
            [{"text": "↩️ Назад", "callback_data": f"dlv:{oid}:no"}]]


def _bot_keep_rolling(chat, pend, oid, order, courier):
    """«Нет, ещё везу»: диалог закрыт, заказ остаётся в развозке."""
    addr = _esc(order.get("address") or "")
    STATE["tg_ask"].get(chat, {}).pop(oid, None)
    _tg_edit_msg(chat, pend["msg"],
                 f"Понял: <b>{addr}</b> ещё в развозке. "
                 "Закроет диспетчер или спрошу при следующем заезде.")
    _ev("cour", f"{courier['name']}: «{order.get('address') or oid}» ещё в развозке")


def _pay_method_label(m):
    return {"cash": "Наличными", "card": "Картой"}.get(m, "")


def _pay_set(oid, method=None, amount=None):
    """Записать аналитику оплаты в архивную строку заказа (заказ уже закрыт
    на этапе «доставил» — правим history, STATE-заказа больше нет)."""
    sets, args = [], []
    if method is not None:
        sets.append("payment = ?"); args.append(method)
    if amount is not None:
        sets.append("pay_amount = ?"); args.append(amount)
    if not sets:
        return
    args.append(oid)
    try:
        with _db_lock, _db() as c:
            c.execute(f"UPDATE history SET {', '.join(sets)} WHERE id = ?", args)
    except sqlite3.Error:
        log.exception("pay: не записать оплату заказа %s", oid)
