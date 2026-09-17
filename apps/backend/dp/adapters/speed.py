"""Индивидуальная скорость курьера: замер по гео, фоллбек по доставкам."""
from ..config import _now
from .sqlite_repo import _db, _db_lock, _speed_add
from ..state import ROAD_FACTOR, STATE
from ..domain.geo import haversine_km

# ---------- индивидуальная скорость курьера ----------
# Замер по гео: пары СГЛАЖЕННЫХ (медиана) точек live-локации с dt >= 15 c,
# отрезком >= 40 м и скоростью 3..80 км/ч добавляют метры/секунды в speed_day
# за сегодня. Одиночный GPS-прыжок гасится медианой (не попадает в трек),
# мелкая дрожь на месте — порогом дистанции, выброс «1000 км/ч» — потолком,
# а дневная сумма усредняет остаточный шум.
# Фоллбек по доставкам: средний цикл курьера против среднего по флоту
# за тот же день — отношение масштабирует скорость по умолчанию.
_SPEED_MIN_GEO_S = 180.0   # нужно >= 3 минут движения, чтобы доверять гео
_SPEED_MIN_DEL_N = 2       # нужно >= 2 доставок, чтобы сравнивать темп
_SPEED_KMH_BOUNDS = (5.0, 160.0)
_SPEED_RATIO_BOUNDS = (0.6, 1.7)  # фоллбек не может уводить далеко от нормы
_SPEED_SEG_MIN_M = 40.0    # короче 40 м — дрожь стояния, не движение
_SPEED_MAX_ACC_M = 100.0   # точность хуже 100 м — точка мусорная
def _speed_geo_sample(courier_id, prev, cur):
    """Складывает отрезок между двумя гео-точками в дневной замер (или игнор)."""
    dt = cur["ts"] - prev["ts"]
    if not (3 <= dt <= 600):
        return
    if max(prev.get("acc") or 0, cur.get("acc") or 0) > _SPEED_MAX_ACC_M:
        return  # точность хуже 100 м — верить отрезку нельзя
    m = haversine_km(prev, cur) * ROAD_FACTOR * 1000.0
    if m < _SPEED_SEG_MIN_M:
        return  # дрожь на месте / шаг внутри погрешности GPS
    kmh = m / 1000.0 / (dt / 3600.0)
    if 3.0 <= kmh <= 160.0:
        _speed_add(courier_id, geo_m=m, geo_s=dt)


_SPEED_CUR_WINDOW = 240.0  # окно «текущей» скорости, секунды
_SPEED_CUR_MAX_AGE = 300.0  # гео старше 5 минут — текущей скорости нет


def _speed_current_kmh(pos, now):
    """Скорость «прямо сейчас» по свежему гео-треку. None — гео нет/устарело,
    0.0 — стоит на месте (точки есть, движения нет)."""
    if not pos or now - pos["ts"] > _SPEED_CUR_MAX_AGE:
        return None
    hist = [h for h in pos.get("hist", []) if now - h["ts"] <= _SPEED_CUR_WINDOW]
    if len(hist) < 2:
        return None
    m_sum = t_sum = 0.0
    # цепочка якорей ≥5 с: тики бывают чаще (боты 0.5 с) — соседние пары
    # не набирают dt, ждём следующую «зрелую» точку от последнего якоря
    anchor = None
    for h in hist:
        if anchor is None:
            anchor = h
            continue
        dt = h["ts"] - anchor["ts"]
        if dt < 5:
            continue
        a, b = anchor, h
        anchor = h
        if max(a.get("acc") or 0, b.get("acc") or 0) > _SPEED_MAX_ACC_M:
            continue
        m_raw = haversine_km(a, b) * 1000.0
        kmh_real = m_raw / 1000.0 / (dt / 3600.0)
        if kmh_real > 170.0:
            continue  # GPS-прыжок фильтруем по реальной скорости GPS;
            # дорожный фактор применяем после, иначе быстрый курьер
            # (150 × 1.3 = 195) весь улетает в фильтр и «едет» выглядит как «стоит»
        m = m_raw * ROAD_FACTOR
        kmh = kmh_real * ROAD_FACTOR
        if m < 15.0 and kmh < 5.0:
            t_sum += dt  # стоит на месте: время идёт, метры — нет
            continue
        m_sum += m
        t_sum += dt
    return round(m_sum / 1000.0 / (t_sum / 3600.0), 1) if t_sum else 0.0


def _speed_rows(courier_id, limit=30):
    with _db_lock, _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM speed_day WHERE courier_id = ? "
            "ORDER BY day DESC LIMIT ?", (courier_id, limit))]


def _speed_fleet_cycle_avg(day):
    """Средний цикл доставок по всем курьерам за день (или None)."""
    with _db_lock, _db() as c:
        r = c.execute("SELECT SUM(del_min) AS s, SUM(del_n) AS n FROM speed_day "
                      "WHERE day = ? AND del_n > 0", (day,)).fetchone()
    return (r["s"] / r["n"]) if r and r["n"] else None


def _speed_from_row(row, default_kmh):
    """Скорость из строки дня: сначала гео, иначе темп доставок. None — нет данных."""
    if row["geo_s"] >= _SPEED_MIN_GEO_S:
        kmh = row["geo_m"] / row["geo_s"] * 3.6
        if kmh > 0.5:
            return min(_SPEED_KMH_BOUNDS[1], max(_SPEED_KMH_BOUNDS[0], kmh)), "geo"
    if row["del_n"] >= _SPEED_MIN_DEL_N:
        mine = row["del_min"] / row["del_n"]
        fleet = _speed_fleet_cycle_avg(row["day"])
        if fleet and mine > 0:
            ratio = fleet / mine  # цикл длиннее среднего -> медленнее
            ratio = min(_SPEED_RATIO_BOUNDS[1], max(_SPEED_RATIO_BOUNDS[0], ratio))
            return min(_SPEED_KMH_BOUNDS[1],
                       max(_SPEED_KMH_BOUNDS[0], default_kmh * ratio)), "delivery"
    return None


def _courier_speed(courier, settings=None):
    """(км/ч, источник) индивидуальной скорости курьера.

    Лестница: сегодня (гео -> доставки) -> вчера -> самый свежий день с замером
    -> настройка speed_kmh (источник "default").
    """
    default_kmh = max(5.0, float((settings or STATE["settings"])
                                 .get("speed_kmh", 60)))
    if not courier or not courier.get("id"):
        return default_kmh, "default"
    for row in _speed_rows(courier["id"]):
        got = _speed_from_row(row, default_kmh)
        if got:
            return got
    return default_kmh, "default"
