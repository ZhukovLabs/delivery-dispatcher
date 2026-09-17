"""Модель оптимизации: веса, дедлайны, подход к точке, ETA-проход."""
import re
from datetime import timedelta

from ..state import STATE
from .geo import haversine_km

# Источники: местные СМИ (BGmedia, сентябрь 2026), разборы проспекта Ленина.
# Шкала консервативная: пик +35%, межпик -5..-10%.
_HOURLY_TRAFFIC = {0: 0.90, 1: 0.90, 2: 0.90, 3: 0.90, 4: 0.90, 5: 0.90,
                   6: 1.00, 7: 1.20, 8: 1.35, 9: 1.15, 10: 1.00, 11: 1.00,
                   12: 1.10, 13: 1.05, 14: 1.00, 15: 1.00, 16: 1.05,
                   17: 1.20, 18: 1.35, 19: 1.15, 20: 1.00, 21: 0.95,
                   22: 0.95, 23: 0.90}
_LATE_WEIGHT = 25    # штраф за минуту опоздания к дедлайну; складывается с
                     # приоритетом (60/мин «поскорее»): у заказа с обоими
                     # флагами давят ОБА давления через две размерности
_DROP_PENALTY = 1_000_000  # штраф отказа от заказа; приоритет ×10, дедлайн ×5
                           # (см. AddDisjunction ниже)
_ASAP_WEIGHT = 5     # вес минуты ожидания обычного заказа («поскорее»)
_SPAN_WEIGHT = 3     # вес секунды длительности заезда: компактность против
                     # срочности. Перекалибровано 17.09 на живом кейсе
                     # (6 заказов, 2 свободных курьера): при 100:1 решатель
                     # сваливал почти всё одному курьеру цепочкой заездов
                     # (5-6 из 6, last 89 мин), т.к. час второй машины «дороже»
                     # 100 часов ожидания. При 3:5 развоз делится 3+3,
                     # avg 29 / last 55; дедлайны держатся (late=0), цепочки
                     # соло-курьера и загрузка 5+5 на 10 заказах сохраняются.
                     # Соотношения: late:asap = 5:1, prio:asap = 12:1
_MAX_TRIPS = 1       # заездов на курьера в расчёте: только первый (решение
                     # от 17.09 — цепочки заездов не строим; добавить сверх
                     # лимита можно только вручную переносом из «Готовых
                     # адресов», следующий пересчёт лишние снимет)
                     # Механика копий k>=1 (release_s, _LOOP_EST_FACTOR)
                     # сохранена в коде на случай возврата к цепочкам
_LOOP_EST_FACTOR = 1.5  # оценка длительности заезда для нижних границ стартов
                         # копий k>=1: минимальный круг ×1.5. Чистый минимум —
                         # «идеальный мир»: решатель верил в слишком ранний
                         # возврат и обещал невлезающие дедлайны


def _deadline_rel_min(hhmm, now_hm):
    """Дедлайн «обещали к HH:MM» в минутах от текущего момента. None, если не задан."""
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", (hhmm or "").strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2))) - now_hm


_APPROACH_RADIUS_KM = 2.5  # ближе к центру — плотная застройка, парковка дольше


def _approach_map(points, home, k_orders, settings):
    """Добавка на парковку/подъезд для заказов (узлы k_orders..) от точки home.

    Возвращает {узел_заказа: минуты}; центру ближе _APPROACH_RADIUS_KM - «центр».
    """
    near = max(0, int(settings.get("approach_center_min", 4)))
    far = max(0, int(settings.get("approach_far_min", 2)))
    out = {}
    for j in range(k_orders, len(points)):
        km = haversine_km(home, points[j])
        out[j] = near if km <= _APPROACH_RADIUS_KM else far
    return out


def _eta_pass(stop_nodes, delay, matrix, settings, solved_dt, appr=None, home=0,
              spd_factor=1.0):
    """ETA остановок поездки (минуты от solved_dt) с почасовыми коэффициентами.

    Матрица в СЕКУНДАХ, построена с базовым коэффициентом traffic и вручением
    на дугах прибытия в заказ: дуга очищается от них и домножается на
    коэффициент часа фактического выезда на дугу.
    spd_factor — индивидуальный множитель курьера (замедленная/быстрая езда).
    Возвращает (список ETA остановок в минутах, полная длительность в минутах).
    """
    hourly = int(settings.get("hour_traffic", 1))
    base_traffic = max(1.0, float(settings.get("traffic", 1.3)))
    handover = max(0, int(settings["handover_min"]))
    handover_s = handover * 60
    t = float(delay)
    node = home
    etas = []
    for g in stop_nodes:
        hour = (solved_dt + timedelta(minutes=t)).hour
        factor = _HOURLY_TRAFFIC.get(hour, 1.0) if hourly else 1.0
        travel = (matrix[node][g] - handover_s) / base_traffic * factor * spd_factor
        t += travel / 60.0 + handover + (appr.get(g, 0) if appr else 0)
        etas.append(int(round(t)))
        node = g
    hour = (solved_dt + timedelta(minutes=t)).hour
    factor = _HOURLY_TRAFFIC.get(hour, 1.0) if hourly else 1.0
    # возвратная дуга вручения не содержит (строится без него)
    t += matrix[node][home] / base_traffic / 60.0 * factor * spd_factor
    return etas, int(round(t))

