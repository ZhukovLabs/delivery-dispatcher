from .bot_flow import _pay_method_label
from .domain.text import _esc


def _txt_pay_unclear():
    return ("Не понял сумму — напишите числом, например: "
            "<b>24.50</b> (или нажмите «Сумму не знаю»)")


def _txt_pay_recorded(pay, amount):
    return (f"✅ Записано: <b>{_esc(pay.get('addr') or pay['oid'])}</b>"
            f" доставлен. Оплата: <b>{_pay_method_label(pay['method'])}</b>, "
            f"<b>{amount:g}</b>.")


def _txt_pay_event(pay, amount, who):
    return (f"оплата: {_pay_method_label(pay['method']).lower()} "
            f"{amount:g} — «{pay.get('addr') or pay['oid']}» ({who})")


def _txt_not_linked(chat_id):
    return (f"Похоже, вас ещё не привязали к курьеру. Отправьте этот ID "
            f"администратору: <code>{chat_id}</code>")


def _txt_start(chat_id):
    return ("Привет! Это бот развозки.\n\n"
            "Нужна <b>живая геолокация</b>:\n"
            "скрепка → «Геолокация» → «Поделиться моей геолокацией» → "
            "время <b>«Пока не отключу»</b>.\n\n"
            "Тогда диспетчер видит вас на карте всю смену.\n\n"
            f"Ваш ID: <code>{chat_id}</code>\n"
            "Скажите его администратору, и вас подключат к курьеру.")
