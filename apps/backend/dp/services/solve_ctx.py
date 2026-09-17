# -*- coding: utf-8 -*-
"""Сценарий solve, фаза 1: сбор контекста расчёта.

_build_context поднимает всё, что нужно модели и сборке плана:
снимок заказов/курьеров, матрица времени, скорости, дедлайны,
виртуальные машины-копии (заезды). Результат — SimpleNamespace,
дальше фазы решателя работают только с ним (без глобального STATE).
"""
from datetime import datetime, timedelta

from ..adapters.matrix import build_time_matrix
from ..adapters.speed import _courier_speed
from ..config import _now
from ..domain.model import (_HOURLY_TRAFFIC, _LOOP_EST_FACTOR, _MAX_TRIPS,
                            _approach_map, _deadline_rel_min)
from ..online import _my_point, _start_delay_min
from ..state import STATE, _home_point, _obj_point
from types import SimpleNamespace


def _build_context(include_away=True, helpers=None, force=None, point_id=None):
    helpers = helpers or {}
    point_id = point_id or _my_point()
    force_ids = {cid for cid in (force or [])
                 if any(c["id"] == cid for c in STATE["couriers"])}
    settings = STATE["settings"]
    orders = [o for o in STATE["orders"]
              if (o.get("status") or "ready") == "ready" and _obj_point(o) == point_id]
    mine = [c for c in STATE["couriers"] if _obj_point(c) == point_id]
    active = [c for c in mine
              if c["status"] == "base" or (include_away and c["status"] == "away")]
    helper_ids = {cid for cid in helpers
                  if any(c["id"] == cid for c in STATE["couriers"])}
    couriers = active + [c for c in STATE["couriers"]
                         if c["id"] in helper_ids and c not in active]
    if not STATE.get("points"):
        raise ValueError("Сначала задайте место выдачи заказов (точку на карте)")
    if not orders:
        raise ValueError("Нет готовых заказов, добавьте хотя бы один")
    if not couriers:
        raise ValueError("Нет активных курьеров, добавьте курьера")

    def _eff_home(c):
        """Точка старта курьера в этом расчёте (помощник едет с чужой точки)."""
        hp = helpers.get(c["id"])
        if hp:
            p = next((p for p in STATE["points"] if p["id"] == hp), None)
            if p:
                return p
        return _home_point(c)

    # Узлы матрицы: 0..K-1 - уникальные точки выдачи активных курьеров, дальше заказы.
    # Каждый курьер стартует и финиширует в СВОЕЙ точке (RoutingIndexManager starts).
    homes, home_idx = [], {}
    for c in couriers:
        hp = _eff_home(c)
        if hp["id"] not in home_idx:
            home_idx[hp["id"]] = len(homes)
            homes.append(hp)
    K = len(homes)
    points = homes + orders
    matrix, by_roads, distances, provider = build_time_matrix(points, settings,
                                                              k_homes=K)
    appr_home = [_approach_map(points, h, K, settings) for h in homes]  # минуты
    solved_dt = _now()
    now_hm = solved_dt.hour * 60 + solved_dt.minute
    handover = max(0, int(settings["handover_min"]))
    handover_s = handover * 60
    auto_prio = int(settings.get("auto_prio_min", 0) or 0)
    max_orders = int(settings["max_orders"])
    reload_s = max(0, int(settings.get("reload_min", 10))) * 60
    hourly_on = bool(int(settings.get("hour_traffic", 1)))
    base_traffic = max(1.0, float(settings.get("traffic", 1.3)))

    # Индивидуальная скорость: дорожное время масштабируется на default/замер.
    default_kmh = max(5.0, float(settings.get("speed_kmh", 60)))
    speeds = {c["id"]: _courier_speed(c, settings) for c in couriers}
    spd_factor = {cid: max(0.25, min(4.0, default_kmh / kmh))
                  for cid, (kmh, _src) in speeds.items()}

    deadline_rel, eff_prio, auto_flag = {}, {}, {}
    for i, o in enumerate(orders):
        g = K + i
        deadline_rel[g] = _deadline_rel_min(o.get("deadline"), now_hm)
        age_min = 0
        try:
            age_min = int((solved_dt - datetime.fromisoformat(o["created_at"])
                           ).total_seconds() // 60)
        except (KeyError, ValueError, TypeError):
            pass
        auto_flag[g] = bool(auto_prio > 0 and age_min >= auto_prio)
        eff_prio[g] = bool(o.get("prio") or auto_flag[g])

    home_of = {c["id"]: home_idx[_eff_home(c)["id"]] for c in couriers}
    # Точка выдачи каждого заказа: везти его могут только курьеры этой точки.
    first_pid = STATE["points"][0]["id"]
    order_pid = {K + i: (o.get("point_id") or first_pid) for i, o in enumerate(orders)}
    home_pid = {c["id"]: _eff_home(c)["id"] for c in couriers}

    # Ручное закрепление (pin) — воля диспетчера: лимит max_orders для курьера
    # расширяется на число закреплённых за ним заказов. Решатель вправе
    # вытеснить обычный заказ в unassigned, но не закрепление.
    pinned_n = {}
    for o in orders:
        p = o.get("pin")
        if p:
            pinned_n[p] = pinned_n.get(p, 0) + 1

    # Виртуальные машины: копия курьера = один его заезд. Реефицированные
    # цепочки «конец заезда k + перезагрузка <= старт заезда k+1» здесь
    # НЕ используются: нелинейные произведения ломают фильтры локального
    # поиска OR-Tools (GLS застревает, кумуляторы не минимизируются —
    # проверено на изолированном воспроизведении). Вместо них линейные
    # нижние границы старта копии k: возврат + k × (перезагрузка +
    # оценка заезда ×1.5). Истинные задержки/ETA восстанавливаются
    # цепочкой при сборке плана (см. solve_build), здесь важна
    # относительная цена дуг в правильный час.
    veh = []
    for c in couriers:
        cid = c["id"]
        h = home_idx[home_pid[cid]]
        allowed = {K + i for i, o in enumerate(orders)
                   if order_pid[K + i] == home_pid[cid]
                   and (not o.get("pin") or o["pin"] == cid)}
        min_loop_s = (min(matrix[h][g] + matrix[g][h] for g in allowed)
                      if allowed else 0)
        est_loop_s = int(min_loop_s * _LOOP_EST_FACTOR)
        release_s = 0 if c["status"] != "away" else _start_delay_min(c) * 60
        trips_n = 1 if cid in helper_ids else _MAX_TRIPS
        for k in range(trips_n):
            start_min_s = release_s + k * (reload_s + est_loop_s)
            hour = (solved_dt + timedelta(seconds=round(start_min_s))).hour
            veh.append({"courier": c, "home": h, "allowed": allowed, "k": k,
                        "appr": appr_home[h], "factor": spd_factor.get(cid, 1.0),
                        "hour_f": (_HOURLY_TRAFFIC.get(hour, 1.0)
                                   if hourly_on else 1.0),
                        "release_s": release_s, "start_min_s": start_min_s})

    return SimpleNamespace(
        point_id=point_id, orders=orders, couriers=couriers,
        helper_ids=helper_ids, force_ids=force_ids, settings=settings,
        homes=homes, home_idx=home_idx, K=K, points=points, matrix=matrix,
        by_roads=by_roads, distances=distances, provider=provider,
        appr_home=appr_home, solved_dt=solved_dt, now_hm=now_hm,
        handover=handover, handover_s=handover_s, auto_prio=auto_prio,
        max_orders=max_orders, reload_s=reload_s, hourly_on=hourly_on,
        base_traffic=base_traffic, default_kmh=default_kmh, speeds=speeds,
        spd_factor=spd_factor, deadline_rel=deadline_rel, eff_prio=eff_prio,
        auto_flag=auto_flag, home_of=home_of, order_pid=order_pid,
        home_pid=home_pid, pinned_n=pinned_n, veh=veh)
