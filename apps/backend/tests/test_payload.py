import time

import pytest

from dp.adapters.telegram import TG_POS_TTL
from dp.online import ONLINE, ONLINE_WINDOW
from dp.payload_helpers import _geo_payload, _points_with_admins
from dp.state import STATE

_KEYS = ("couriers", "points", "orders", "tg_pos")


@pytest.fixture
def clean_state():
    """Подмена STATE/ONLINE изолированными данными с полным откатом после теста."""
    snap = {k: STATE[k] for k in _KEYS}
    online_snap = dict(ONLINE)
    STATE["points"] = [{"id": "p1", "name": "Депо", "address": "а",
                        "lat": 52.0, "lng": 31.0}]
    yield STATE
    STATE.update(snap)
    ONLINE.clear()
    ONLINE.update(online_snap)


def _couriers():
    STATE["couriers"] = [{"id": "c1", "name": "Иван", "status": "base",
                          "tg_chat_id": "chat1", "point_id": "p1"},
                         {"id": "c2", "name": "Пётр", "status": "base"}]
    STATE["orders"] = []
    STATE["tg_pos"] = {}


def test_geo_payload_skips_unlinked_courier(clean_state):
    _couriers()
    out = _geo_payload()
    assert [c["id"] for c in out["couriers"]] == ["c1"]
    assert out["t"] <= time.time()


def test_geo_payload_at_depot(clean_state):
    _couriers()
    ts = time.time()
    STATE["tg_pos"] = {"chat1": {"lat": 52.0, "lng": 31.0, "ts": ts,
                                 "live": False}}
    item = _geo_payload()["couriers"][0]
    assert item["pos"] == {"lat": 52.0, "lng": 31.0, "ts": ts,
                           "live": False, "acc": 0}
    assert item["geo"]["at_depot"] is True
    assert item["geo"]["back_min"] == 0
    assert item["geo"]["has_out"] is False
    assert "cur_kmh" not in item


def test_geo_payload_stale_pos(clean_state):
    _couriers()
    STATE["tg_pos"] = {"chat1": {"lat": 52.0, "lng": 31.0,
                                 "ts": time.time() - TG_POS_TTL - 3600,
                                 "live": True}}
    assert _geo_payload()["couriers"] == [{"id": "c1"}]


def test_points_with_admins_dedup_and_prune(clean_state):
    now = time.time()
    ONLINE.clear()
    ONLINE.update({
        "s1": {"uid": 1, "email": "a@x", "point_id": "p1", "last": now},
        "s2": {"uid": 1, "email": "a@x", "point_id": "p1", "last": now - 1},
        "s3": {"uid": 2, "email": "b@x", "point_id": "p2", "last": now - 5},
        "s4": {"uid": 3, "email": "c@x", "point_id": "p1",
               "last": now - ONLINE_WINDOW * 10}})
    pts = _points_with_admins([{"id": "p1"}, {"id": "p2"}])
    assert pts[0]["admins"] == ["a@x"]
    assert pts[1]["admins"] == ["b@x"]
    assert "s4" not in ONLINE
    assert "s3" in ONLINE
