from .geo import DEG_PER_M, log


class Bot:
    """Конечный автомат: to_home -> stand -> deliver -> to_home -> ..."""

    def __init__(self, cid, name, chat, home):
        self.cid, self.name, self.chat, self.home = cid, name, chat, home
        self.mode = "init"
        self.path = []          # полилиния [(lat, lng), ...]
        self.pos = (0, 0.0)     # (индекс сегмента, доля)
        self.cur = home         # текущая точка (lat, lng)
        self.stops = {}         # oid -> (lat, lng)
        self.done_key = None    # frozenset(oid) текущей развозки
        self.visited = set()
        self.dwell_until = 0.0
        self.dwell_stop = None

    def ride(self, target_stops, osrm):
        """Построить трассу развозки: текущая позиция -> адреса -> домой."""
        pts = [self.cur] + [self.stops[oid] for oid in target_stops
                            if oid in self.stops] + [self.home]
        geom = [tuple(p) for p in pts]
        if len(geom) > 2:
            geom = osrm.route(pts)
        self.path = geom
        self.pos = (0, 0.0)
        self.mode = "deliver"
        self.visited = set()
        self.dwell_until = 0.0
        self.dwell_stop = None
        log(f"{self.name}: развозка, {len(self.stops)} заказ(ов), "
            f"трасса {len(self.path)} точек")

    def go_home(self, osrm):
        self.path = osrm.route([self.cur, self.home])
        self.pos = (0, 0.0)
        self.mode = "to_home"
        log(f"{self.name}: едет на базу ({len(self.path)} точек)")

    def step_deg(self, speed_kmh, tick):
        return speed_kmh / 3.6 * tick * DEG_PER_M
