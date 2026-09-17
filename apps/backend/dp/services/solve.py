"""Расчёт плана развозки (OR-Tools): сценарии, приоритеты, параллель."""
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..config import _now, log
from ..adapters.geometry import routing_geometry
from ..adapters.matrix import build_time_matrix
from ..planstate import _plans_lock
from .solve_geom import _attach_geometry
from ..domain.model import _APPROACH_RADIUS_KM, _ASAP_WEIGHT, _DROP_PENALTY, _eta_pass, _HOURLY_TRAFFIC, _LATE_WEIGHT, _LOOP_EST_FACTOR, _MAX_TRIPS, _SPAN_WEIGHT, _approach_map, _deadline_rel_min
from ..adapters.speed import _courier_speed
from ..online import _my_point, _start_delay_min
from ..state import PALETTE, STATE, _PRIO_WEIGHT, _home_point, _obj_point
def solve_plan(include_away=True, with_geometry=True, helpers=None, force=None,
               point_id=None):
    """Развозка ОДНОГО депо (point_id; None = точка вызывающего).

    include_away=False — сценарий «не ждать»: только курьеры на базе.

    Все заезды всех курьеров решаются ОДНОЙ OR-Tools-задачей: у каждого
    курьера до _MAX_TRIPS виртуальных машин-копий (сейчас _MAX_TRIPS=1 —
    строим только первый заезд; копии связаны цепочкой по времени —
    старт заезда k+1 не раньше конца заезда k плюс перезагрузка —
    механика сохранена на случай возврата цепочек). Состав пачек и
    порядок объезда оптимизируются совместно. Задержку возврата
    away-курьера моделируем стартовым кумулятором его первой копии.

    Время внутри решателя — в СЕКУНДАХ (матрица build_time_matrix тоже):
    веса штрафов не менялись, вся цель масштабируется равномерно.

    helpers: {courier_id: point_id} — разовая «помощь»: курьер в этом расчёте
    стартует с чужой точки выдачи и берёт максимум один заказ. Его собственная
    точка и статус не меняются.
    """
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

    # Виртуальные машины: копия курьера = один его заезд. Риифицированные
    # цепочки «конец заезда k + перезагрузка <= старт заезда k+1» здесь
    # НЕ используются: нелинейные произведения ломают фильтры локального
    # поиска OR-Tools (GLS застревает, кумуляторы не минимизируются —
    # проверено на изолированном воспроизведении). Вместо них линейные
    # нижние границы старта копии k: возврат + k × (перезагрузка +
    # оценка заезда ×1.5). Истинные задержки/ETA восстанавливаются
    # цепочкой при сборке плана (см. ниже), здесь важна относительная цена
    # дуг в правильный час.
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

    def _solve_once(budget_ms):
        """Одна OR-Tools-модель по текущему состоянию veh + решение.

        Между проходами меняются только hour_f копий (обновление почасовых
        коэффициентов по фактическим стартам) — модель и ограничения
        идентичны, поэтому замыкание без параметров модели.
        """
        manager = pywrapcp.RoutingIndexManager(len(points), len(veh),
                                               [v["home"] for v in veh],
                                               [v["home"] for v in veh])
        routing = pywrapcp.RoutingModel(manager)

        def make_cb(v, start_index, with_trip_cost):
            def cb(from_index, to_index):
                i, j = (manager.IndexToNode(from_index),
                        manager.IndexToNode(to_index))
                arc = matrix[i][j]
                if j >= K and arc > handover_s:
                    # базовый трафик уже зашит в матрицу (traffic=1.3 к среднему);
                    # почасовой коэффициент ЗАМЕНЯЕТ его часовую часть, поэтому
                    # сначала делим на базу — как в _eta_pass. Иначе факторы
                    # перемножаются (1.3×0.9) и дедлайны сравниваются с завышенными
                    # кумуляторами: решатель «паникует» у дедлайнов.
                    arc = int(round((arc - handover_s) / base_traffic
                                    * v["factor"] * v["hour_f"])) + handover_s
                elif j < K and arc > 0:
                    # возвратная дуга (заказ -> дом): вручения нет, но трафик,
                    # час и скорость курьера действуют так же — раньше дуга шла
                    # по базовой матрице без пересчёта (решатель недооценивал
                    # возвраты в час пик и переоценивал ночью)
                    arc = int(round(arc / base_traffic
                                    * v["factor"] * v["hour_f"]))
                cost = arc
                if j >= K:
                    cost += v["appr"].get(j, 0) * 60  # парковка/подъезд: мин -> сек
                    if j not in v["allowed"]:      # чужая точка/чужой pin — везти нельзя
                        cost += 1_000_000_000
                if with_trip_cost and from_index == start_index and v["k"] > 0:
                    # фиксированная цена активации заезда k>0 (возврат + перезагрузка):
                    # без неё PCI не различает копии одного курьера и может посадить
                    # единственный заезд в k1/k2, завышая ETА на полчаса, а одиночные
                    # переносы не вытащат (промежуточное расщепление дороже).
                    # Только в ЦЕЛЕВУЮ функцию — в размерность времени надбавка
                    # не идёт (нижние границы стартов уже учитывают перезагрузку)
                    cost += reload_s
                return cost
            return cb

        cb_idxs = []      # транзиты времени (без цены активации)
        for vi, v in enumerate(veh):
            vi_start = routing.Start(vi)
            cb_idx = routing.RegisterTransitCallback(make_cb(v, vi_start, False))
            cb_idxs.append(cb_idx)
            routing.SetArcCostEvaluatorOfVehicle(
                routing.RegisterTransitCallback(make_cb(v, vi_start, True)), vi)

        # ёмкость размерности — с запасом под расширенные лимиты закреплений
        cap_extra = max(pinned_n.values()) if pinned_n else 0
        routing.AddConstantDimension(1, max_orders + 1 + cap_extra, True, "Orders")
        orders_dim = routing.GetDimensionOrDie("Orders")
        first_copy = {}   # courier_id -> индекс первой копии (заезд k = 0)
        for vi, v in enumerate(veh):
            cid = v["courier"]["id"]
            first = first_copy.setdefault(cid, vi)
            # базовый лимит max_orders; курьеру с закреплениями — плюс их число
            orders_dim.CumulVar(routing.End(vi)).SetMax(
                max_orders + 1 + pinned_n.get(cid, 0))
            if cid in helper_ids:
                # размерность считает дуги: простой = 1, ровно один заказ = 2
                orders_dim.CumulVar(routing.End(vi)).SetRange(2, 2)
            elif vi == first and cid in force_ids:
                # перетащен в план вручную: первый заезд обязан взять заказ
                orders_dim.CumulVar(routing.End(vi)).SetMin(2)

        routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 3600, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        # Давление на длительность — ПЕР-ВЕХИКЛЬНЫЙ span (конец − старт копии), а не
        # глобальный max(End) − min(Start): в модели с копиями-заездами глобальный
        # span ломает поиск — решатель выравнивает старты копий и прячет заказы в
        # поздние копии (воспроизведено изолированно: глобальный span -> первый
        # заезд пустой, старты +45 мин, просроченный дедлайн в хвосте; пер-
        # вехикльный -> первый заезд загружен, старты на нижних границах). Пустая
        # копия при этом даёт span 0 и не шумит.
        for vi in range(len(veh)):
            time_dim.SetSpanCostCoefficientForVehicle(_SPAN_WEIGHT, vi)

        # Нижние границы стартов копий: заезд k не раньше возврата курьера +
        # (перезагрузка + холостой заезд) × k. Старт ПЕРВОЙ копии фиксируем
        # жёстко на релизе — ETА и почасовой коэффициент считаются от него;
        # для остальных копий достаточно нижней границы: на старт нет давления
        # в цель, локальный поиск кладёт его на границу.
        for vi, v in enumerate(veh):
            start = time_dim.CumulVar(routing.Start(vi))
            if v["k"] == 0:
                start.SetRange(v["start_min_s"], v["start_min_s"])
            else:
                start.SetMin(v["start_min_s"])

        # Штрафы ожидания доставки: обычный заказ 1 мин, просрочка дедлайна 25 —
        # на размерности Time; приоритет 60/мин «поскорее» — на отдельной
        # размерности Urg, чтобы заказ с приоритетом И дедлайном давился ОБЕИМИ
        # (раньше elif терял приоритет у заказов с дедлайном).
        has_prio = any(eff_prio.values())
        if has_prio:
            routing.AddDimensionWithVehicleTransits(cb_idxs, 0, 24 * 3600, False, "Urg")
            urg_dim = routing.GetDimensionOrDie("Urg")
        for ln in range(K, len(points)):
            idx = manager.NodeToIndex(ln)
            if deadline_rel[ln] is not None:
                time_dim.SetCumulVarSoftUpperBound(idx, max(0, deadline_rel[ln]) * 60,
                                                   _LATE_WEIGHT)
            elif not eff_prio[ln]:
                time_dim.SetCumulVarSoftUpperBound(idx, 0, _ASAP_WEIGHT)
            if eff_prio[ln] and has_prio:
                urg_dim.SetCumulVarSoftUpperBound(idx, 0, _PRIO_WEIGHT)

        # Разрешаем оставить заказ вне плана: дроп-визит с подавляющим штрафом
        # (чужой заказ стоит миллиард и к курьеру другой точки не попадёт).
        # Вместимость кончилась — роняем самый «дешёвый» для бизнеса заказ:
        # приоритетный держится до последнего (×10), с дедлайном — предпоследним
        # (×5), иначе решателю выгоднее уронить именно «дорогие минуты».
        for ln in range(K, len(points)):
            pen = _DROP_PENALTY
            if eff_prio[ln]:
                pen *= 10
            elif deadline_rel[ln] is not None:
                pen *= 5
            routing.AddDisjunction([manager.NodeToIndex(ln)], pen)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
        params.time_limit.FromMilliseconds(budget_ms)
        solution = routing.SolveWithParameters(params)
        if solution is None:
            log.warning("solve: no solution (status=%s, veh=%d, nodes=%d)",
                        routing.status(), len(veh), len(points))
            raise RuntimeError("OR-Tools не нашёл решение, попробуйте ещё раз")
        return solution, routing, manager, time_dim

    def _make_plan(solution, routing, manager, time_dim):
        """Сборка плана из решения: цепочка заездов, честные ETA, агрегаты.

        Возвращает (plan, start_s): start_s — фактические (цепочкой) старты
        использованных копий в секундах; по ним второй проход обновляет
        почасовые коэффициенты копий.
        """
        trips_by_cid = {}
        visited = set()
        start_s = {}
        if log.isEnabledFor(logging.DEBUG):
            for vi, v in enumerate(veh):
                log.debug(
                    "copy %d %s k=%d floor=%dмин start=%dмин end=%dмин "
                    "factor=%.2f hour_f=%.2f allowed=%d",
                    vi, v["courier"]["name"], v["k"],
                    v["start_min_s"] // 60,
                    solution.Value(time_dim.CumulVar(routing.Start(vi))) // 60,
                    solution.Value(time_dim.CumulVar(routing.End(vi))) // 60,
                    v["factor"], v["hour_f"], len(v["allowed"]))
        for vi, v in enumerate(veh):
            idx, stops = routing.Start(vi), []
            while not routing.IsEnd(idx):
                nd = manager.IndexToNode(idx)
                if nd >= K:  # пропускаем свою точку выдачи (старт)
                    stops.append(nd)
                idx = solution.Value(routing.NextVar(idx))
            if not stops:
                continue
            c = v["courier"]
            delay_min = solution.Value(time_dim.CumulVar(routing.Start(vi))) / 60.0
            trips_by_cid.setdefault(c["id"], []).append(
                {"courier": c, "stops": stops, "delay": delay_min, "vi": vi})
            visited.update(stops)

        routes = []
        reload_min = reload_s // 60   # цепочка заездов: старт k+1 >= конец k + это
        for c in couriers:
            trips_raw = trips_by_cid.get(c["id"])
            if not trips_raw:
                continue
            h = home_of[c["id"]]
            home_view = {k: homes[h][k] for k in ("id", "name", "address", "lat", "lng")}
            trips, flat = [], []
            prev_end = None  # конец предыдущего заезда: цепочим старт следующего
            for tr in trips_raw:
                delay = tr["delay"]
                if prev_end is not None:
                    # решатель знает только нижнюю границу старта копии;
                    # истинное время — после возврата с предыдущего заезда
                    delay = max(delay, prev_end + reload_min)
                start_s[tr["vi"]] = delay * 60
                etas, total = _eta_pass(tr["stops"], delay, matrix, settings,
                                        solved_dt, appr_home[h], home=h,
                                        spd_factor=spd_factor.get(c["id"], 1.0))
                prev_end = total
                stops = []
                for g, eta in zip(tr["stops"], etas):
                    o = orders[g - K]
                    late = max(0, eta - deadline_rel[g]) if deadline_rel[g] is not None else 0
                    stops.append({
                        "order_id": o["id"], "address": o["address"],
                        "prio": eff_prio[g], "auto": auto_flag[g],
                        "deadline": o.get("deadline") or "", "late_min": late,
                        "lat": o["lat"], "lng": o["lng"], "eta_min": eta,
                        "eta_clock": (solved_dt + timedelta(minutes=eta)).strftime("%H:%M")})
                dist_m = None
                if distances:
                    dist_m = 0
                    seq = [h] + tr["stops"] + [h]
                    for a, b in zip(seq, seq[1:]):
                        dist_m += distances[a][b] or 0
                trips.append({
                    "stops": stops, "total_min": total,
                    "start_delay_min": int(round(delay)),
                    "start_clock": (solved_dt + timedelta(minutes=delay)).strftime("%H:%M"),
                    "end_clock": (solved_dt + timedelta(minutes=total)).strftime("%H:%M"),
                    "distance_km": round(dist_m / 1000.0, 1) if dist_m is not None else None})
                flat.extend(stops)
            routes.append({
                "courier_id": c["id"], "courier_name": c["name"], "status": c["status"],
                "color": c.get("color") or PALETTE[len(routes) % len(PALETTE)],
                "count": len(flat), "trips": trips, "stops": flat,
                "total_min": max(t["total_min"] for t in trips),
                "start_delay_min": trips[0]["start_delay_min"],
                "distance_km": (round(sum(t["distance_km"] for t in trips), 1)
                                if all(t["distance_km"] is not None for t in trips) else None),
                "tg_chat_id": c.get("tg_chat_id") or "",
                "home_point": home_view,
                "speed_kmh": round(speeds[c["id"]][0], 1),
                "speed_src": speeds[c["id"]][1]})

        # Сначала «отдать сейчас» (на базе), потом «следующим»
        routes.sort(key=lambda r: 0 if r["status"] == "base" else 1)
        all_etas = [s["eta_min"] for r in routes for s in r["stops"]]
        plan = {
            "solved_at": solved_dt.isoformat(timespec="seconds"),
            "routes": routes,
            "routing": "roads" if by_roads else "straight",
            "provider": provider,
            "last_delivery_min": max(all_etas, default=0),
            "last_delivery_clock": None,
            "avg_delivery_min": round(sum(all_etas) / len(all_etas)) if all_etas else 0,
            "unassigned": len([g for g in range(K, len(points)) if g not in visited]),
            # контекст матрицы: ретайминг после выдачи/переноса берёт ТОН ЖЕ
            # набор точек — кэш матрицы срабатывает без похода в сеть
            "matrix_ctx": {"k": K,
                           "homes": [[p["lat"], p["lng"]] for p in homes],
                           "order_ids": [o["id"] for o in orders]},
        }
        return plan, start_s

    # Бюджет оптимизации масштабируем от размера задачи: одна задача на все
    # заезды (раньше — до трёх моделей по раундам), поэтому берём бюджет
    # крупнее раундового, но меньше старой суммы. DP_TIME_MS — override для
    # тестов/диагностики (задаёт бюджет ПРОХОДА); мусорное значение молча
    # игнорируем. Недетерминизм GLS (бюджет в стенных часах, состав пачек
    # немного плавает между прогонами) принят осознанно: детерминированные
    # альтернативы (solution_limit) дают менее предсказуемое от размера
    # задачи качество.
    big = len(orders) > 12
    try:
        budget_ms = int(os.environ.get("DP_TIME_MS", ""))
    except ValueError:
        budget_ms = 0
    per_pass_ms = budget_ms or (5000 if big else 1500)

    solution, routing, manager, time_dim = _solve_once(per_pass_ms)
    plan, start_s = _make_plan(solution, routing, manager, time_dim)

    # Второй проход решателя: hour_f копии закреплён по нижней границе
    # старта, а фактический старт (цепочка заездов, удлинение первых
    # заездов) может попасть в другой час — дуги поздних копий оценены не
    # тем часом (пик/межпик различаются до ×1.5). Обновляем hour_f по
    # фактическим стартам и решаем ещё раз с вдвое меньшим бюджетом;
    # принимаем только при лучшей ЧЕСТНОЙ метрике — (неразвезено,
    # суммарное опоздание, средняя, последняя доставка).
    if hourly_on:
        refreshed = {}
        for vi, v in enumerate(veh):
            s = start_s.get(vi)
            if s is None:
                continue
            hour = (solved_dt + timedelta(seconds=round(s))).hour
            f = _HOURLY_TRAFFIC.get(hour, 1.0)
            if abs(f - v["hour_f"]) > 1e-6:
                refreshed[vi] = f
        if refreshed:
            for vi, f in refreshed.items():
                veh[vi]["hour_f"] = f
            try:
                sol2, rout2, man2, td2 = _solve_once(max(1, per_pass_ms // 2))
                plan2, _ = _make_plan(sol2, rout2, man2, td2)

                def _quality(p):
                    return (p["unassigned"],
                            sum(st["late_min"] for r in p["routes"]
                                for st in r["stops"]),
                            p["avg_delivery_min"], p["last_delivery_min"])

                if _quality(plan2) < _quality(plan):
                    log.debug("solve: второй проход принят "
                              "(%d копий сменили почасовой коэффициент)",
                              len(refreshed))
                    plan = plan2
                else:
                    log.debug("solve: второй проход отклонён по честной метрике")
            except RuntimeError:
                log.warning("solve: второй проход не нашёл решение, "
                            "оставлен первый")

    warnings = []
    if plan["unassigned"]:
        warnings.append(f"Не поместились в маршруты: {plan['unassigned']} "
                        "заказ(ов) — лимит заездов на курьера исчерпан")
    if not by_roads:
        warnings.append("Роутеры недоступны — время и километры оценены по прямой")
    if warnings:
        plan["warnings"] = warnings
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    if all_etas:
        plan["last_delivery_clock"] = (solved_dt + timedelta(
            minutes=plan["last_delivery_min"])).strftime("%H:%M")
    with _plans_lock:  # установка плана атомарна с выдачами/возвратами
        STATE["plans"][point_id] = plan
    if with_geometry:
        _attach_geometry(plan)
    return plan
