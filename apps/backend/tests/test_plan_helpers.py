from datetime import datetime

from dp.routes_plan_edit_helpers import (_best_insert, _drop_order_stops,
                                         _pop_stop, _recalc_plan_stats)

NOW = datetime(2026, 9, 17, 10, 0, 0)


def _stop(oid, eta):
    return {"order_id": oid, "address": f"адрес {oid}", "eta_min": eta,
            "eta_clock": "10:00", "deadline": "", "late_min": 0,
            "lat": 52.0, "lng": 31.0}


def _trip(stops, delay=0, total=60):
    return {"stops": stops, "total_min": total, "start_delay_min": delay,
            "start_clock": "10:00", "end_clock": "11:00",
            "distance_km": 3.0, "eta_at": None}


def _route(cid, trips):
    flat = [s for t in trips for s in t["stops"]]
    return {"courier_id": cid, "courier_name": cid, "status": "base",
            "color": "#000000", "count": len(flat), "trips": trips,
            "stops": flat, "total_min": 60, "start_delay_min": 0}


def _plan(*routes):
    return {"solved_at": "2026-09-17T09:00:00", "routes": list(routes),
            "routing": "straight", "last_delivery_min": 0,
            "last_delivery_clock": None, "avg_delivery_min": 0}


def _insert(dst, matrix, node, oid, settings):
    return _best_insert(_plan(), dst, matrix, node, [4], 0, oid, NOW, settings)


def _flat_no_traffic():
    return {"hour_traffic": 0, "traffic": 1.0, "handover_min": 0}


def test_pop_stop_found_and_removed():
    p = _plan(_route("c1", [_trip([_stop("o1", 10), _stop("o2", 20)])]),
              _route("c2", [_trip([_stop("o3", 30)])]))
    stop, src = _pop_stop(p, "o3")
    assert (stop["order_id"], src) == ("o3", "c2")
    trip_stops = [s["order_id"] for r in p["routes"] for t in r["trips"]
                  for s in t["stops"]]
    assert trip_stops == ["o1", "o2"]
    assert len(p["routes"][0]["trips"]) == 1


def test_pop_stop_missing():
    p = _plan(_route("c1", [_trip([_stop("o1", 10)])]))
    assert _pop_stop(p, "zz") == (None, None)
    assert len(p["routes"][0]["stops"]) == 1


def test_drop_order_stops_prunes_empty_trip():
    p = _plan(_route("c2", [_trip([_stop("o3", 30)]), _trip([_stop("o4", 40)])]))
    assert _drop_order_stops(p, "o3") == "c2"
    trip_stops = [s["order_id"] for t in p["routes"][0]["trips"]
                  for s in t["stops"]]
    assert trip_stops == ["o4"]
    assert len(p["routes"][0]["trips"]) == 1
    assert _drop_order_stops(p, "o4") == "c2"
    assert p["routes"][0]["trips"] == []
    assert _drop_order_stops(p, "o4") is None


def test_best_insert_into_empty_plan():
    dst = {"courier_id": "c9", "trips": [], "stops": []}
    m = {0: {0: 0, 1: 10}, 1: {0: 10, 1: 0}}
    assert _insert(dst, m, {"a": 1}, "a", _flat_no_traffic()) == (0, 0, 0)
    tr = dst["trips"][0]
    assert tr["stops"] == [] and tr["distance_km"] is None
    assert tr["start_delay_min"] == 0 and tr["total_min"] == 0


def test_best_insert_traffic_and_handover_scales_delta():
    m = {0: {0: 0, 1: 2000, 2: 260},
         1: {0: 2000, 1: 0, 2: 260},
         2: {0: 200, 1: 320, 2: 0}}
    dst = _route("c9", [_trip([_stop("a", 30)])])
    best = _insert(dst, m, {"a": 1, "x": 2}, "x",
                   {"hour_traffic": 0, "traffic": 2.0, "handover_min": 1})
    assert best == (-800.0, 0, 1)


def test_best_insert_hour_traffic_applies_scale(monkeypatch):
    import dp.routes_plan_edit_helpers as mod
    m = {0: {0: 0, 1: 1000, 2: 100},
         1: {0: 1000, 1: 0, 2: 900},
         2: {0: 100, 1: 100, 2: 0}}
    dst = _route("c9", [_trip([_stop("a", 30)])])
    monkeypatch.setattr(mod, "_HOURLY_TRAFFIC", {11: 2.0})
    best = _insert(dst, m, {"a": 1, "x": 2}, "x",
                   {"hour_traffic": 1, "traffic": 1.0, "handover_min": 0})
    assert best == (-1600.0, 0, 0)


def test_best_insert_picks_cheaper_trip():
    m = {0: {0: 0, 1: 1000, 2: 1000, 3: 1100, 4: 200},
         1: {0: 1000, 1: 0, 2: 0, 3: 0, 4: 200},
         2: {0: 1000, 1: 0, 2: 0, 3: 0, 4: 0},
         3: {0: 1100, 1: 0, 2: 0, 3: 0, 4: 100},
         4: {0: 300, 1: 300, 2: 0, 3: 0, 4: 0}}
    dst = _route("c9", [_trip([_stop("a", 30)]),
                        _trip([_stop("b", 40), _stop("c", 50)])])
    best = _insert(dst, m, {"a": 1, "b": 2, "c": 3, "x": 4}, "x",
                   _flat_no_traffic())
    assert best == (-800.0, 1, 0)


def test_recalc_plan_stats():
    p = _plan(_route("c1", [_trip([_stop("o1", 60), _stop("o2", 100)])]),
              _route("c2", [_trip([_stop("o3", 40)])]))
    _recalc_plan_stats(p, NOW)
    assert p["last_delivery_min"] == 100
    assert p["last_delivery_clock"] == "11:40"
    assert p["avg_delivery_min"] == 67


def test_recalc_plan_stats_empty():
    p = _plan()
    _recalc_plan_stats(p, NOW)
    assert p["last_delivery_min"] == 0
    assert p["last_delivery_clock"] is None
    assert p["avg_delivery_min"] == 0
