"""OpenRouteService: основной провайдер, счётчик квоты, отключение при 429."""
import threading
import time
from datetime import datetime, timezone

import requests

from ..config import CFG, log

# OpenRouteService: основной источник матриц/геометрии (по ключу, бесплатный тариф).
# Квота суток ограничена, поэтому: считаем запросы сами, при приближении
# к лимиту заранее уходим на OSRM, а при 429/403 отключаем ORS до
# восстановления (сутки — до полуночи UTC, минутный лимит — на 5 минут).
ORS_KEY = CFG["ors_key"]
ORS_BASE = "https://api.openrouteservice.org"
ORS_SOFT_LIMIT = 1800   # запас до паспортных 2000/сутки: дальше не тратим квоту
ORS_MAX_POINTS = 50     # лимит бесплатного тарифа на размер матрицы
ORS_STATE = {"day": None, "used": 0, "disabled_until": None, "last_error": None}
# ---------- OpenRouteService (с защитой от исчерпания квоты) ----------

def _seconds_to_utc_midnight():
    now = datetime.now(timezone.utc)
    return 24 * 3600 - (now.hour * 3600 + now.minute * 60 + now.second)


def _ors_available():
    """Смена суток обнуляет счётчик и снимает отключение."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if ORS_STATE["day"] != day:
        ORS_STATE.update(day=day, used=0, disabled_until=None, last_error=None)
    if ORS_STATE["disabled_until"] and time.time() < ORS_STATE["disabled_until"]:
        return False
    return ORS_STATE["used"] < ORS_SOFT_LIMIT


def ors_post(path, body):
    """POST к ORS. None => ORS недоступен или квота исчерпана (уходим на OSRM)."""
    if not _ors_available():
        return None
    try:
        resp = requests.post(f"{ORS_BASE}{path}", json=body,
                             headers={"Authorization": ORS_KEY}, timeout=15)
        if resp.status_code in (401, 402, 403, 429):
            try:
                msg = str(resp.json().get("error", ""))[:160]
            except Exception:  # noqa: BLE001
                msg = resp.text[:160]
            daily = any(w in msg.lower() for w in ("daily", "quota", "exceeded"))
            ORS_STATE["disabled_until"] = (time.time() + _seconds_to_utc_midnight()
                                           if daily else time.time() + 300)
            ORS_STATE["last_error"] = f"HTTP {resp.status_code}: {msg}"
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # сеть/таймаут — короткая пауза и фолбэк
        ORS_STATE["disabled_until"] = time.time() + 60
        ORS_STATE["last_error"] = f"{type(exc).__name__}: {exc}"[:160]
        return None
    ORS_STATE["used"] += 1
    return data


def ors_matrix(points):
    body = {"locations": [[p["lng"], p["lat"]] for p in points],
            "metrics": ["duration", "distance"]}
    data = ors_post("/v2/matrix/driving-car", body)
    try:
        durations = data["durations"]
        if any(v is None for row in durations for v in row):
            return None, None
        return durations, data.get("distances")
    except (AttributeError, KeyError, TypeError):
        return None, None


def ors_geometry(points):
    data = ors_post("/v2/directions/driving-car/geojson",
                    {"coordinates": [[p["lng"], p["lat"]] for p in points]})
    try:
        return [[c[1], c[0]] for c in data["features"][0]["geometry"]["coordinates"]]
    except (AttributeError, KeyError, IndexError, TypeError):
        return None


def ors_status():
    _ors_available()  # обновляет счётчики при смене суток
    paused = bool(ORS_STATE["disabled_until"] and time.time() < ORS_STATE["disabled_until"])
    return {"used": ORS_STATE["used"], "soft_limit": ORS_SOFT_LIMIT,
            "paused": paused, "last_error": ORS_STATE["last_error"]}
