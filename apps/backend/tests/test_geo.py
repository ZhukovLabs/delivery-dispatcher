import math

from dp.domain.geo import _valid_latlng, haversine_km


def _p(lat, lng):
    return {"lat": lat, "lng": lng}


def test_same_point_is_zero():
    assert haversine_km(_p(52.0, 31.0), _p(52.0, 31.0)) == 0.0


def test_symmetric():
    a, b = _p(55.7558, 37.6173), _p(59.9311, 30.3609)
    assert haversine_km(a, b) == haversine_km(b, a)


def test_one_degree_latitude():
    assert abs(haversine_km(_p(52, 31), _p(53, 31)) - 111.1949266) < 1e-3


def test_one_degree_longitude_scales_with_cos():
    assert abs(haversine_km(_p(0, 30), _p(0, 31)) - 111.1949266) < 1e-3
    assert abs(haversine_km(_p(60, 30), _p(60, 31)) - 55.5969341) < 1e-3


def test_half_equator_is_pi_r():
    assert abs(haversine_km(_p(0, 0), _p(0, 180)) - math.pi * 6371.0) < 1e-6


def test_moscow_to_petersburg():
    d = haversine_km(_p(55.7558, 37.6173), _p(59.9311, 30.3609))
    assert 631.0 < d < 633.0


def test_valid_latlng():
    assert _valid_latlng(52.0, 31.0)
    assert _valid_latlng(0.0, -180.0)
    assert _valid_latlng(-90.0, 180.0)
    assert not _valid_latlng(0.0, 0.0)
    assert not _valid_latlng(90.1, 0.0)
    assert not _valid_latlng(0.0, -180.5)
