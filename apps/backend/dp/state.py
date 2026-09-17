"""Глобальное состояние диспетчерской (один процесс — один словарь)."""
from .config import DEFAULT_DEPOT

STATE = {
    "rev": 0,            # счетчик изменений для long-poll /api/rev
    "depot": dict(DEFAULT_DEPOT),  # совместимый вид первой точки {address, lat, lng}
    "points": [],        # места выдачи: {"id", "name", "address", "lat", "lng"}
    "couriers": [],      # {"id", "name", "status": base|away|off, "color", "back_min", "point_id"}
    "orders": [],        # {"id", "address", "lat", "lng"}
    "settings": {"speed_kmh": 60, "handover_min": 5, "max_orders": 5, "traffic": 1.25,
                 "lights_sec_per_km": 15, "auto_prio_min": 0, "reload_min": 10,
                 "hour_traffic": 1, "approach_center_min": 4, "approach_far_min": 2},
    "plans": {},         # pid -> план развозки (у каждого депо свой)
    "advice_modes": {},  # pid -> ручной выбор «ждать/не ждать» (now|split)
    "solving": {},       # pid -> True: в депо идёт расчёт развозки (клиенты
                         # блокируют UI, повторный запуск отклоняется)
    "color_seq": 0,      # монотонный счётчик: цвета не перемешиваются при удалениях
    # Telegram: кто писал боту (для привязки), последние локации курьеров, курсор getUpdates
    "tg_seen": {},       # chat_id -> {"chat_id", "login", "ts"}
    "tg_pos": {},        # chat_id -> {"lat", "lng", "ts", "live"}
    "tg_nagged": {},     # chat_id -> ts последнего «не привязан» (антиспам live-правок)
    "tg_load": {},       # chat_id -> {"since", "loaded_at"} — трекер выдачи заказов
    "tg_deliv": {},      # chat_id -> {order_id: {"since", "at"}} — вывод «доставлен»
                         # ТОЛЬКО для расчёта возврата; статус заказа не меняет
    "tg_away": {},       # chat_id -> {"since"} — авто-«в пути» при отъезде от точки
    "tg_ask": {},        # chat_id -> {order_id: {"msg", "stage"}} — бот ждёт «доставил?»
    "tg_pay": {},        # chat_id -> {"oid", "method", "msg", "addr", "ts"} —
                         # бот ждёт сумму оплаты после «доставил»
    "tg_offset": 0,
    "tg_bot": "",        # @username бота (для подсказок в интерфейсе)
    "events": [],        # лента активности: {"t", "actor": bot|disp|cour|sys, "text"}
}

ROAD_FACTOR = 1.4  # запасной расчёт (если OSRM недоступен): прямая -> дорога
_PRIO_WEIGHT = 60  # вес минуты доставки приоритетного заказа (против 1 у обычного)
MAX_POINTS = 10    # максимум мест выдачи


def _depot_view():
    """Совместимый со старым API вид первой точки выдачи ({"address","lat","lng"})."""
    p = (STATE.get("points") or [None])[0]
    if not p:
        return dict(DEFAULT_DEPOT)
    return {"address": p["address"], "lat": p["lat"], "lng": p["lng"]}


def _home_point(courier):
    """Точка выдачи курьера (или первая, если привязка не задана/битая)."""
    pid = (courier.get("point_id") or "").strip()
    for p in STATE.get("points") or []:
        if p["id"] == pid:
            return p
    return (STATE.get("points") or [None])[0]


def _obj_point(x):
    """Точка выдачи заказа/курьера: пустая привязка = первая точка."""
    pid = (x.get("point_id") or "").strip()
    if pid and any(p["id"] == pid for p in STATE.get("points") or []):
        return pid
    return (STATE.get("points") or [{}])[0].get("id") or ""


PALETTE = ["#e8482b", "#2563eb", "#059669", "#9333ea",
           "#d97706", "#0891b2", "#be185d", "#4d7c0f"]
STATUSES = {"base", "away", "off"}
