"""Адаптер: сетевой примитив «каскад с подстраховкой» (hedged request).

Провайдеры — коллбэки без аргументов; каждый возвращает результат, ложное
значение/исключение = промах. Ступени стартуют по одной; если очередная не
ответила за hedge_s, параллельно запускается следующая. Побеждает первый
зафиксировавшийся результат (при одновременном ответе — более приоритетный),
остальные игнорируются (потоки-демоны дорабатывают вхолостую). None = все молчат."""
import threading
import time

from ..config import log


def hedged_first(providers, hedge_s, final_wait):
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
