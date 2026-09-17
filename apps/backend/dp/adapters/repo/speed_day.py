from ...config import _now
from .db import _db, _db_lock


def _speed_add(courier_id, day=None, geo_m=0.0, geo_s=0.0, del_n=0, del_min=0.0):
    day = day or _now().strftime("%Y-%m-%d")
    with _db_lock, _db() as c:
        c.execute(
            "INSERT INTO speed_day(courier_id, day, geo_m, geo_s, del_n, del_min) "
            "VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(courier_id, day) DO UPDATE SET "
            "geo_m = geo_m + excluded.geo_m, geo_s = geo_s + excluded.geo_s, "
            "del_n = del_n + excluded.del_n, del_min = del_min + excluded.del_min",
            (courier_id, day, geo_m, geo_s, del_n, del_min))
