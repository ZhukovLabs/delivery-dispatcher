from datetime import timedelta

from .core import (STATE, _bump, _courier_plan, _courier_speed, _deadline_rel_min,
                   _eta_pass, _home_point, _invalidate_plan, _my_point, _now,
                   _persist_meta, _plan_for, _simplify_poly, log)
from .routes_retiming import _retiming_matrix

def _patch_plan_after_assign(oids, cid=None):
    """Убрать выданные заказы из плана и пересчитать ETA оставшихся курьеров.

    План остаётся рабочим: диспетчер сразу выдаёт маршруты следующим курьерам,
    не дожидаясь полного пересчёта. Матрица берётся из кэша (набор точек тот же,
    что при расчёте), ETA пересчитываются от текущего момента.
    """
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    plan = _courier_plan(courier) if courier else _plan_for(_my_point())
    pid = (_home_point(courier)["id"] if courier and _home_point(courier)
           else _my_point())
    if not plan or not plan.get("routes"):
        return False
    oidset = set(oids)
    touched = set()
    # порядок выданных остановок — в сами заказы, а дорожную геометрию выданной
    # части трипа (до последней выданной остановки) сохраняем курьеру: по ней
    # карта рисует активную развозку по дорогам, когда маршрут ушёл из плана
    order_by_id = {o["id"]: o for o in STATE["orders"]}
    k = 0
    out_geom = []
    if courier:
        courier.pop("ret_geom", None)  # новая выдача отменяет возврат
        for r in plan["routes"]:
            if r["courier_id"] != courier["id"]:
                continue
            for tr in r.get("trips", []):
                geom = tr.get("geometry") or []
                last_idx = -1
                for s in tr["stops"]:
                    if s["order_id"] not in oidset:
                        continue
                    o = order_by_id.get(s["order_id"])
                    if o is not None:
                        o["out_no"] = k
                        k += 1
                    if geom:
                        gi = min(range(len(geom)),
                                 key=lambda i: (geom[i][0] - s["lat"]) ** 2
                                               + (geom[i][1] - s["lng"]) ** 2)
                        last_idx = max(last_idx, gi)
                if last_idx >= 0:
                    out_geom.extend(geom[:last_idx + 1])
    if out_geom:
        # стыки упрощённых сегментов дают дубли точек — ужимаем слитую трассу
        courier["out_geom"] = _simplify_poly(
            (courier.get("out_geom") or []) + out_geom)
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            before = len(tr["stops"])
            tr["stops"] = [s for s in tr["stops"] if s["order_id"] not in oidset]
            if len(tr["stops"]) != before:
                touched.add(r["courier_id"])
        r["trips"] = [tr for tr in r.get("trips", []) if tr["stops"]]
    if not touched:
        return False
    now = _now()
    try:
        # матрица и ретайминг — по ВСЕМ маршрутам плана: ETA нетронутых
        # курьеров тоже должны быть «от сейчас», а не от момента расчёта
        matrix, node, appr_home, home_of = _retiming_matrix(plan, pid,
                                                            plan["routes"])
        for r in plan["routes"]:
            h = home_of[r["courier_id"]]
            _retime_route(r, matrix, node, STATE["settings"], now, appr_home[h], h)
        # задержки плана перепривязаны к «сейчас» — фиксируем новый якорь,
        # иначе _refresh_plan_delays досдвигал бы их ещё раз от solved_at
        plan["anchored_at"] = now.isoformat(timespec="seconds")
    except Exception:
        log.warning("plan retime after assign failed, агрегаты пересобраны без матрицы")
        for r in plan["routes"]:
            r["stops"] = [s for tr in r.get("trips", []) for s in tr["stops"]]
            r["count"] = len(r["stops"])
    plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
    if not plan["routes"]:
        # всё выдано: мёртвый пустой план никому не нужен, а фоновый
        # пересчёт подхватит заказы, которые могли остаться вне маршрутов
        STATE["plans"].pop(pid, None)
        _persist_meta()
        _invalidate_plan(pid=pid)
        return True
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» больше не соответствуют плану
    plan.pop("moved", None)
    _persist_meta()
    _bump()  # выдача видна всем консолям сразу
    return True


def _retime_route(route, matrix, node, settings, now_dt, appr=None, home=0):
    """Пересчёт ETA всех заездов курьера от now_dt. Меняет route на месте."""
    now_hm = now_dt.hour * 60 + now_dt.minute
    courier = next((c for c in STATE["couriers"]
                    if c["id"] == route.get("courier_id")), None)
    default_kmh = max(5.0, float(settings.get("speed_kmh", 60)))
    kmh, _src = _courier_speed(courier, settings) if courier else (default_kmh, "default")
    spd_factor = max(0.25, min(4.0, default_kmh / kmh))
    route["speed_kmh"] = round(kmh, 1)
    route["speed_src"] = _src
    reload_min = max(0, int(settings.get("reload_min", 10)))
    prev_end = None  # конец предыдущего заезда: старт следующего цепочим
    for tr in route.get("trips", []):
        seq = [node[s["order_id"]] for s in tr["stops"]]
        delay = tr["start_delay_min"] or 0
        if prev_end is not None:
            # цепочка как при сборке плана: после plan_move заезд могли
            # удлинить — старый старт нарушал бы её и занижал ETA
            delay = max(delay, prev_end + reload_min)
        tr["start_delay_min"] = delay
        etas, total = _eta_pass(seq, delay, matrix, settings,
                                now_dt, appr, home, spd_factor=spd_factor)
        for s, eta in zip(tr["stops"], etas):
            s["eta_min"] = eta
            s["eta_clock"] = (now_dt + timedelta(minutes=eta)).strftime("%H:%M")
            rel = _deadline_rel_min(s.get("deadline"), now_hm)
            s["late_min"] = max(0, eta - rel) if rel is not None else 0
        tr["total_min"] = total
        prev_end = total
        tr["end_clock"] = (now_dt + timedelta(minutes=total)).strftime("%H:%M")
        tr["start_clock"] = (now_dt + timedelta(
            minutes=tr["start_delay_min"])).strftime("%H:%M")
        tr["eta_at"] = now_dt.isoformat(timespec="seconds")  # якорь этих ETA
    route["stops"] = [s for tr in route.get("trips", []) for s in tr["stops"]]
    route["count"] = len(route["stops"])
    if route["stops"]:
        route["total_min"] = max(tr["total_min"] for tr in route["trips"])
