"""Утилиты без состояния: гео-математика, текст, каскад запросов."""
import math
import threading
import time

from .config import log

def hedged_first(providers, hedge_s, final_wait):
    """Каскад с подстраховкой (hedged request). providers — коллбэки без
    аргументов; каждый возвращает результат, ложное значение/исключение =
    промах. Ступени стартуют по одной; если очередная не ответила за hedge_s,
    параллельно запускается следующая. Побеждает первый зафиксировавшийся
    результат (при одновременном ответе — более приоритетный), остальные
    игнорируются (потоки-демоны дорабатывают вхолостую). None = все молчат."""
    got, done, lock = {}, threading.Event(), threading.Lock()

    def _run(fn, fin):
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: %s", getattr(fn, "__name__", "?"), exc)
        else:
            if res:
                with lock:
                    if "res" not in got:  # первый зафиксировавшийся и выигрывает
                        got["res"] = res
                        done.set()
        finally:
            fin.set()

    for fn in providers:
        if done.is_set():
            break
        fin = threading.Event()
        threading.Thread(target=_run, args=(fn, fin), daemon=True).start()
        # ждём до hedge_s: результат, быстрый провал ступени (fin без res —
        # следующая стартует сразу) или таймаут молчания (следующая параллельно)
        deadline = time.time() + hedge_s
        while not done.is_set() and not fin.is_set() and time.time() < deadline:
            time.sleep(0.02)
    done.wait(final_wait)
    return got.get("res")
def haversine_km(a, b):
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))
def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def _plural(n, forms):
    """Русское склонение: _plural(3, ("заказ", "заказа", "заказов")) -> "заказа"."""
    n = abs(n)
    if n % 100 in (11, 12, 13, 14):
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if n % 10 in (2, 3, 4):
        return forms[1]
    return forms[2]
def _valid_latlng(lat, lng):
    return (-90 <= lat <= 90) and (-180 <= lng <= 180) and (lat != 0 or lng != 0)

