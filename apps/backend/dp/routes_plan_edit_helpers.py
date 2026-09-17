from datetime import datetime, timedelta

from .core import _HOURLY_TRAFFIC


def _pop_stop(plan, oid):
    stop, src_id = None, None
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
            if hit:
                stop, src_id = hit, r["courier_id"]
                tr["stops"].remove(hit)
    return stop, src_id


def _best_insert(plan, dst, matrix, node, appr_home, h_dst, oid, now, settings):
    g_x = node[oid]
    best = None  # (удлинение, индекс заезда, позиция вставки)
    hour_on = bool(int(settings.get("hour_traffic", 1)))
    base_traffic = max(1.0, float(settings.get("traffic", 1.3)))
    handover_s = max(0, int(settings["handover_min"])) * 60
    n_homes = len(appr_home)

    def _travel(u, w, f):
        """Дорожная часть дуги (сек) в масштабе часа f: базовый traffic
        из матрицы заменяется коэффициентом часа подъезда — вставка в
        разные позиции попадает в разные часы пик. Вручение/подъезд —
        константа вставки, на РАНГ позиций не влияют, не добавляем."""
        t = matrix[u][w]
        if w >= n_homes:
            t -= handover_s
        return t / base_traffic * f

    for ti, tr in enumerate(dst.get("trips", [])):
        # лимит max_orders соблюдает только решатель; ручной перенос из
        # «Готовых адресов» разрешён и сверх лимита — диспетчер видит,
        # что делает (ограничение вернётся при следующем пересчёте)
        seq = [h_dst] + [node[s["order_id"]] for s in tr["stops"]] + [h_dst]
        for pos in range(1, len(seq)):
            a, b = seq[pos - 1], seq[pos]
            if hour_on:
                # час подъезда к месту вставки — по ETA предыдущей остановки;
                # ETA плана привязаны к моменту своего последнего пересчёта
                # (eta_at) — добавляем возраст, иначе час пик занижается
                # на возраст плана
                if pos == 1:
                    t_prev = tr.get("start_delay_min") or 0
                else:
                    t_prev = tr["stops"][pos - 2].get("eta_min") or 0
                try:
                    eta_at = datetime.fromisoformat(
                        tr.get("eta_at") or plan.get("anchored_at")
                        or plan["solved_at"])
                    age_min = (now - eta_at).total_seconds() / 60.0
                except (ValueError, TypeError):
                    age_min = 0.0
                hour = (now + timedelta(minutes=t_prev + age_min)).hour
                f = _HOURLY_TRAFFIC.get(hour, 1.0)
        else:
            f = 1.0
        delta = _travel(a, g_x, f) + _travel(g_x, b, f) - _travel(a, b, f)
        if best is None or delta < best[0]:
            best = (delta, ti, pos - 1)
    if best is None and not dst.get("trips"):
        # у цели не было заездов — создаём первый
        dst["trips"] = [{"stops": [], "total_min": 0, "start_delay_min": 0,
                         "start_clock": "", "end_clock": "", "distance_km": None}]
        best = (0, 0, 0)
    return best


def _recalc_plan_stats(plan, now):
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
