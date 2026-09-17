"""Пересчёт ETA после правок: базы матрицы и сама матрица."""
import json
from datetime import datetime, timedelta

from .core import (STATE, _HOURLY_TRAFFIC, _approach_map, _home_point,
                   _obj_point, build_time_matrix, haversine_km, log)

def _retiming_bases(routes):
    """Точки выдачи для маршрутов: (список домов, {courier_id: узел дома}).

    Берёт home_point из плана (падение в текущую точку курьера для старых планов).
    """
    homes, home_idx, home_of = [], {}, {}
    for r in routes:
        hp = r.get("home_point")
        if not hp:
            c = next((x for x in STATE["couriers"] if x["id"] == r["courier_id"]), None)
            hp = _home_point(c) if c else (STATE.get("points") or [None])[0]
        key = hp["id"] if hp else ""
        if key not in home_idx:
            home_idx[key] = len(homes)
            homes.append(hp)
        home_of[r["courier_id"]] = home_idx[key]
    return homes, home_of


def _retiming_matrix(plan, pid, routes):
    """Матрица для пересчёта ETA/переносов в ГОТОВОМ плане.

    Основной путь — тот же набор точек, что при расчёте плана
    (plan.matrix_ctx): ключ кэша матрицы совпадает, сеть не трогаем.
    Без контекста (старые планы до обновления) или при исчезнувших заказах —
    свежая матрица по своему депо: раньше сюда замешивались ВСЕ готовые
    заказы всех депо (и даже выданные) — набор точек не совпадал с расчётным,
    кэш промахивался и каждая выдача ходила за матрицей в сеть.

    Возвращает (matrix_сек, node: order_id -> индекс, appr_home, home_of).
    """
    homes, home_of = _retiming_bases(routes)
    ctx = (plan or {}).get("matrix_ctx")
    if ctx and ctx.get("order_ids") and ctx.get("homes"):
        ctx_homes = [{"lat": la, "lng": ln} for la, ln in ctx["homes"]]
        K = ctx.get("k") or len(ctx_homes)
        by_id = {o["id"]: o for o in STATE["orders"]}
        ctx_orders = [by_id.get(oid) for oid in ctx["order_ids"]]
        if all(o is not None for o in ctx_orders):
            pos = {(round(h["lat"], 5), round(h["lng"], 5)): i
                   for i, h in enumerate(ctx_homes)}
            h_of = {}
            for cid, hi in home_of.items():
                key = (round(homes[hi]["lat"], 5), round(homes[hi]["lng"], 5))
                if key not in pos:
                    h_of = None
                    break
                h_of[cid] = pos[key]
            if h_of is not None:
                points = ctx_homes + ctx_orders
                try:
                    matrix, _, _, _ = build_time_matrix(points, STATE["settings"],
                                                        k_homes=K)
                except Exception:
                    log.warning("retiming: матрица из matrix_ctx не собралась, "
                                "берём свежую по своему депо", exc_info=True)
                    matrix = None
                if matrix is not None:
                    node = {oid: K + i for i, oid in enumerate(ctx["order_ids"])}
                    appr = [_approach_map(points, h, K, STATE["settings"])
                            for h in ctx_homes]
                    return matrix, node, appr, h_of
    # фоллбек: точки маршрутов + готовые заказы ТОЛЬКО своего депо
    K = len(homes)
    ready = [o for o in STATE["orders"]
             if (o.get("status") or "ready") == "ready" and _obj_point(o) == pid]
    points = homes + ready
    matrix, _, _, _ = build_time_matrix(points, STATE["settings"], k_homes=K)
    node = {o["id"]: K + i for i, o in enumerate(ready)}
    appr = [_approach_map(points, h, K, STATE["settings"]) for h in homes]
    return matrix, node, appr, home_of


