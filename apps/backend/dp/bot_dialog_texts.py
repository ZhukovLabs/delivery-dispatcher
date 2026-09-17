from .bot_flow import _CANCEL_REASONS, _pay_method_label
from .domain.text import _esc


def _txt_not_actual_closed():
    return "Этот вопрос уже неактуален — заказ закрыт диспетчером."


def _txt_confirm_delivered(addr):
    return f"Точно доставлен? Заказ: <b>{addr}</b>"


def _txt_confirm_cancel(addr):
    return f"Точно отменяем? Заказ: <b>{addr}</b>"


def _txt_confirm_still(addr):
    return f"Точно ещё нет? Заказ: <b>{addr}</b>"


def _txt_reason(addr):
    return f"Причина отмены: <b>{addr}</b>"


def _kb_reasons(oid):
    return ([[{"text": t, "callback_data": f"dlv:{oid}:r:{i}"}]
             for i, t in enumerate(_CANCEL_REASONS)]
            + [[{"text": "↩️ Назад", "callback_data": f"dlv:{oid}:no"}]])


def _txt_cancelled(addr, reason):
    return (f"🗑 Записано: <b>{addr}</b> — заказ отменён.\n"
            f"Причина: <b>{_esc(reason)}</b>")


def _txt_close_failed():
    return "Не получилось закрыть — уже неактуален."


def _txt_delivered_ask_pay(addr):
    return (f"✅ Записано: <b>{addr}</b> доставлен.\n\n"
            "Как оплатил клиент?")


def _kb_pay(oid):
    return [[{"text": "💵 Наличными",
              "callback_data": f"dlv:{oid}:pay:cash"}],
            [{"text": "💳 Картой",
              "callback_data": f"dlv:{oid}:pay:card"}],
            [{"text": "⏭ Без оплаты / не важно",
              "callback_data": f"dlv:{oid}:payskip"}]]


def _txt_pay_amount(addr, method):
    return (f"✅ <b>{addr}</b> доставлен. Оплата: <b>"
            f"{_pay_method_label(method)}</b>.\n\n"
            "Напишите сумму числом в чат — например: <b>24.50</b>")


def _kb_skip(oid):
    return [[{"text": "⏭ Сумму не знаю",
              "callback_data": f"dlv:{oid}:payskip"}]]


def _txt_delivered_thanks(addr):
    return f"✅ Записано: <b>{addr}</b> доставлен. Спасибо!"
