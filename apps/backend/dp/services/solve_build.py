# -*- coding: utf-8 -*-
"""Сценарий solve, фаза 3: сборка плана из решения OR-Tools.

Собирает цепочку заездов с ЧЕСТНЫМИ ETA (цепочка стартов, а не нижние
границы решателя) и агрегаты маршрутов. Возвращает (plan, start_s):
start_s — фактические старты использованных копий в секундах, по ним
оркестратор обновляет почасовые коэффициенты для второго прохода.
"""
import logging
from datetime import timedelta

from ..config import log
from ..domain.model import _eta_pass
from ..state import PALETTE


def _make_plan(ctx, solution, routing, manager, time_dim):
    veh, K = ctx.veh, ctx.K
    couriers, homes, home_of = ctx.couriers, ctx.homes, ctx.home_of
    reload_s, matrix, settings = ctx.reload_s, ctx.matrix, ctx.settings
    solved_dt, appr_home = ctx.solved_dt, ctx.appr_home
    spd_factor, deadline_rel = ctx.spd_factor, ctx.deadline_rel
    eff_prio, auto_flag, orders = ctx.eff_prio, ctx.auto_flag, ctx.orders
    distances, speeds = ctx.distances, ctx.speeds

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
        "routing": "roads" if ctx.by_roads else "straight",
        "provider": ctx.provider,
        "last_delivery_min": max(all_etas, default=0),
        "last_delivery_clock": None,
        "avg_delivery_min": round(sum(all_etas) / len(all_etas)) if all_etas else 0,
        "unassigned": len([g for g in range(K, len(ctx.points)) if g not in visited]),
        # контекст матрицы: ретайминг после выдачи/переноса берёт ТОТ ЖЕ
        # набор точек — кэш матрицы срабатывает без похода в сеть
        "matrix_ctx": {"k": K,
                       "homes": [[p["lat"], p["lng"]] for p in homes],
                       "order_ids": [o["id"] for o in orders]},
    }
    return plan, start_s
