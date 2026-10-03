from .bot_flow import _pay_method_label
from .domain.text import _esc


def _txt_pay_unclear():
    return ("Сумму не понял. Напишите просто числом, например <b>24.50</b>. "
            "Или жмите «Сумму не знаю» под сообщением.")


def _txt_pay_next(addr):
    """Подсказка: в очереди ещё один заказ, ждём его сумму."""
    return (f"Теперь следующий: <b>{_esc(addr)}</b>. Напишите его сумму "
            "числом сюда, в чат. Не помните? Жмите «Сумму не знаю» "
            "в его сообщении.")


def _txt_pay_recorded(pay, amount):
    return (f"✅ Записал: <b>{_esc(pay.get('addr') or pay['oid'])}</b>"
            f" доставлен. Оплата: <b>{_pay_method_label(pay['method'])}</b>, "
            f"<b>{amount:g}</b>.")


def _txt_pay_event(pay, amount, who):
    return (f"оплата: {_pay_method_label(pay['method']).lower()} "
            f"{amount:g} — «{pay.get('addr') or pay['oid']}» ({who})")


def _txt_not_linked(chat_id):
    return (f"Вас пока не подключили к курьеру. Перешлите администратору "
            f"этот ID: <code>{chat_id}</code>, и вас добавят.")


def _txt_start(chat_id):
    return ("🛵 Привет! Это бот развозки еды.\n\n"
            "Чтобы диспетчер видел вас на карте, включите живую "
            "геолокацию:\n"
            "📎 скрепка рядом с полем ввода → «Геолокация» → "
            "«Поделиться моей геолокацией» → время «Пока не отключу».\n\n"
            "Держите её включённой всю смену: без неё вас не видно "
            "на карте.\n\n"
            f"Ваш ID: <code>{chat_id}</code>\n"
            "Назовите его администратору, и вас подключат к курьеру.")
