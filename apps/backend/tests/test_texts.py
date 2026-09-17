from dp.bot_dialog_texts import (_kb_confirm, _kb_pay, _kb_reasons, _kb_skip,
                                 _cancel_reason, _txt_cancelled,
                                 _txt_confirm_cancel, _txt_confirm_delivered,
                                 _txt_confirm_still, _txt_delivered_ask_pay,
                                 _txt_delivered_thanks, _txt_not_actual_closed,
                                 _txt_pay_amount, _txt_reason)
from dp.bot_flow import _CANCEL_REASONS
from dp.bot_updates_helpers import (_txt_not_linked, _txt_pay_event,
                                    _txt_pay_recorded, _txt_pay_unclear,
                                    _txt_start)
from dp.domain.text import _esc, _plural


def test_texts_non_empty():
    texts = [
        _txt_not_actual_closed(),
        _txt_confirm_delivered("ул. Ленина 1"),
        _txt_confirm_cancel("ул. Ленина 1"),
        _txt_confirm_still("ул. Ленина 1"),
        _txt_reason("ул. Ленина 1"),
        _txt_cancelled("ул. Ленина 1", "нет клиента"),
        _txt_delivered_ask_pay("ул. Ленина 1"),
        _txt_pay_amount("ул. Ленина 1", "cash"),
        _txt_delivered_thanks("ул. Ленина 1"),
        _txt_pay_unclear(),
        _txt_start(12345),
        _txt_not_linked(77),
    ]
    assert all(isinstance(t, str) and t.strip() for t in texts)


def test_confirm_texts_embed_address():
    assert "<b>ул. Ленина 1</b>" in _txt_confirm_delivered("ул. Ленина 1")
    assert "Точно отменяем" in _txt_confirm_cancel("A")
    assert "Точно ещё нет" in _txt_confirm_still("A")


def test_cancelled_escapes_reason_only():
    t = _txt_cancelled("<x>", "прич >на &")
    assert "<b><x></b>" in t
    assert "&gt;на &amp;" in t
    assert _esc(None) == ""


def test_pay_amount_labels():
    assert "Наличными" in _txt_pay_amount("A", "cash")
    assert "Картой" in _txt_pay_amount("A", "card")


def test_kb_pay_buttons():
    rows = _kb_pay("o1")
    assert [r[0]["text"] for r in rows] == [
        "💵 Наличными", "💳 Картой", "⏭ Без оплаты / не важно"]
    assert [r[0]["callback_data"] for r in rows] == [
        "dlv:o1:pay:cash", "dlv:o1:pay:card", "dlv:o1:payskip"]


def test_kb_reasons_buttons():
    rows = _kb_reasons("o1")
    assert len(rows) == len(_CANCEL_REASONS) + 1
    assert rows[0][0]["callback_data"] == "dlv:o1:r:0"
    assert rows[-1][0]["callback_data"] == "dlv:o1:no"
    assert rows[0][0]["text"] == _CANCEL_REASONS[0]


def test_kb_confirm_and_skip():
    rows = _kb_confirm("o9")
    assert rows[0][0]["callback_data"] == "dlv:o9:ok"
    assert rows[1][0]["callback_data"] == "dlv:o9:no"
    assert _kb_skip("o2")[0][0]["callback_data"] == "dlv:o2:payskip"


def test_cancel_reason_index():
    assert _cancel_reason("r:2") == _CANCEL_REASONS[2] == "Просто отказ"
    assert _cancel_reason("r:99") == "Другое"
    assert _cancel_reason("r:xx") == "Другое"


def test_pay_recorded_and_event():
    pay = {"addr": "A<b>", "oid": "1", "method": "cash"}
    t = _txt_pay_recorded(pay, 24.5)
    assert "&lt;b&gt;" in t and "Наличными" in t and "<b>24.5</b>" in t
    assert "<b>24</b>" in _txt_pay_recorded(
        {"oid": "7", "method": "card"}, 24)
    ev = _txt_pay_event({"method": "card", "addr": "X", "oid": "2"}, 10, "Иван")
    assert ev == "оплата: картой 10 — «X» (Иван)"


def test_start_and_not_linked_embed_id():
    assert "12345" in _txt_start(12345)
    assert "<code>77</code>" in _txt_not_linked(77)


def test_plural():
    forms = ("заказ", "заказа", "заказов")
    assert [_plural(n, forms) for n in (1, 2, 5, 11, 21, 111)] == [
        "заказ", "заказа", "заказов", "заказов", "заказ", "заказов"]
