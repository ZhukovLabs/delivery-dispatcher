import re
from datetime import timedelta

import pytest

from dp.config import _now
from dp.domain.model import _deadline_rel_min
from dp.online_helpers import (_courier_plan, _plan_for,
                               _refresh_plan_delays, _start_delay_min)
from dp.state import STATE

_KEYS = ("couriers", "points", "orders", "tg_pos", "plans", "settings")


@pytest.fixture
def clean_state():
    """Подмена STATE изолированными данными с полным откатом после теста."""
    snap = {k: STATE[k] for k in _KEYS}
    STATE["points"] = [{"id": "p1", "name": "Депо", "address": "а",
                        "lat": 52.0, "lng": 31.0}]
    STATE["couriers"] = [{"id": "c1", "name": "Иван", "status": "away",
                          "back_min": 40, "point_id": "p1", "color": "#111"}]
    STATE["orders"] = []
    STATE["tg_pos"] = {}
    STATE["plans"] = {}
    yield STATE
    STATE.update(snap)


def _trip(oid, eta, delay, total):
    return {"stops": [{"order_id": oid, "address": oid, "eta_min": eta,
                       "eta_clock": "", "deadline": "", "late_min": 0}],
            "total_min": total, "start_delay_min": delay,
            "start_clock": "08:00", "end_clock": "09:00",
            "distance_km": 5.0, "eta_at": None}


def test_start_delay_min_fallback(clean_state):
    assert _start_delay_min({}) == 15
    assert _start_delay_min({"back_min": 40}) == 40
    assert _start_delay_min({"back_min": -3}) == 0


def test_refresh_plan_delays_away_courier(clean_state):
    """away без гео: старт = back_min, цепочка заездов сдвигается с reload_min."""
    tr0, tr1 = _trip("o1", 60, 5, 90), _trip("o2", 130, 20, 150)
    route = {"courier_id": "c1", "trips": [tr0, tr1],
             "stops": tr0["stops"] + tr1["stops"],
             "total_min": 150, "start_delay_min": 5}
    plan = {"solved_at": (_now() - timedelta(minutes=120)
                          ).isoformat(timespec="seconds"), "routes": [route]}
    assert _refresh_plan_delays(plan) is plan
    assert (tr0["start_delay_min"], tr0["total_min"],
            tr0["stops"][0]["eta_min"]) == (40, 125, 95)
    assert (tr1["start_delay_min"], tr1["total_min"],
            tr1["stops"][0]["eta_min"]) == (135, 265, 245)
    assert (route["start_delay_min"], route["total_min"]) == (40, 265)
    assert re.match(r"^\d{2}:\d{2}$", tr0["start_clock"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}T", plan["anchored_at"])
    assert tr0["stops"][0]["late_min"] == 0


def test_refresh_plan_delays_noop(clean_state):
    assert _refresh_plan_delays(None) is None
    empty = {"routes": []}
    assert _refresh_plan_delays(empty) is empty
    assert "anchored_at" not in empty
    STATE["couriers"][0]["status"] = "off"
    tr = _trip("o1", 60, 5, 90)
    off = {"solved_at": "2026-09-17T08:00:00",
           "routes": [{"courier_id": "c1", "trips": [tr], "stops": tr["stops"]}]}
    _refresh_plan_delays(off)
    assert tr["start_delay_min"] == 5 and "anchored_at" not in off


def test_plan_for_and_courier_plan(clean_state):
    plan = {"solved_at": "2026-09-17T08:00:00", "routes": [],
            "routing": "straight"}
    STATE["plans"]["p1"] = plan
    assert _plan_for("p1") is plan
    assert _plan_for("zz") is None
    assert _courier_plan(STATE["couriers"][0]) is plan


def test_deadline_rel_min():
    assert _deadline_rel_min("10:30", 600) == 30
    assert _deadline_rel_min("09:30", 600) == -30
    assert _deadline_rel_min("9:05", 0) == 545
    assert _deadline_rel_min("25:00", 0) is None
    assert _deadline_rel_min("10:5", 0) is None
    assert _deadline_rel_min("", 0) is None
    assert _deadline_rel_min(None, 0) is None
