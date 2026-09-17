"""Инвалидация планов и рассылка изменений (WS-bump)."""
import sqlite3
import threading
import time

from .adapters.sqlite_repo import _persist_meta
from .state import STATE

# сериализует ВСЕ правки планов: две параллельные выдачи/возвраты otherwise
# перетирают друг другу trips в одном плане (last-writer-wins воскрешал
# уже выданные стопы). RLock: _patch_plan_after_assign держит его и зовёт
# _invalidate_plan вложенно
_plans_lock = threading.RLock()
# ---------- live-рассылка: WS-хаб (socket.io) вместо long-poll /api/rev ----------

def _bump(geo: bool = False):
    """Пометить состояние изменённым и разбудить подписчиков WS-хаба.

    Вызывается из любого потока (HTTP-обработчики, TG-поллер); хаб
    коалесит частые бампы. geo=True — обновление только отслеживания
    (движение курьера): хаб шлёт такое не чаще раза в секунду; любое
    событие (выдача, статус, заказ) доставляется сразу.
    """
    STATE["rev"] = STATE.get("rev", 0) + 1
    from . import ws  # поздний импорт: ws импортирует core — рвём цикл
    ws.notify_changed(geo=geo)


def _ev(actor, text):
    """Лента активности в UI: bot=бот, disp=диспетчер, cour=курьер, sys=система."""
    STATE["events"].append({"t": int(time.time()), "actor": actor,
                            "text": str(text)[:200]})
    del STATE["events"][:-60]  # храним только свежие
    _bump()


def _points_ids():
    """Все id точек выдачи (румы WS-хаба)."""
    return [p["id"] for p in STATE.get("points") or []]


# ---------- инвалидация плана (используется и HTTP-ручками, и TG-ботом) ----------
def _invalidate_plan(drop_plan=False, pid=None, courier_id=None, geo=False):
    # под замком: параллельные выдачи/возвраты правят те же планы
    with _plans_lock:
        return _invalidate_plan_u(drop_plan, pid, courier_id, geo)


def _invalidate_plan_u(drop_plan=False, pid=None, courier_id=None, geo=False):
    """План не пересчитываем в фоне — только помечаем/сбрасываем.

    pid — депо, чей план инвалидируем (None = все депо: правка точек/настроек).
    drop_plan=True — старый план точно невалиден (удаление заказа/курьера,
    смена депо): сбрасываем сразу. Иначе план показывается с пометкой
    «устарел», пока администратор не нажмёт «Рассчитать».
    courier_id — курьер-специфичная инвалидация (смена статуса, удаление,
    возврат на базу, перевод в другое депо): из всех планов убираются только
    маршруты этого курьера, маршруты остальных курьеров сохраняются
    с пометкой «устарел» — диспетчер может выдать их без пересчёта.
    """
    if courier_id is not None:
        changed = False
        for key, plan in list(STATE["plans"].items()):
            if plan is None:
                continue
            routes = plan.get("routes") or []
            kept = [r for r in routes if r.get("courier_id") != courier_id]
            if len(kept) == len(routes):
                continue  # этого курьера в плане нет — чужие маршруты не трогаем
            changed = True
            if kept:
                plan["routes"] = kept
                plan["stale"] = True
            else:
                STATE["plans"].pop(key, None)
        if changed:
            try:
                _persist_meta()
            except sqlite3.Error:
                pass
        # реальная правка планов — событие (доставляем сразу); плановое
        # сопровождение гео-тика — движение, хаб батчит его до 1/с
        _bump(geo=geo and not changed)
        return
    plans = STATE["plans"] if pid is None else {pid: STATE["plans"].get(pid)}
    for key, plan in list(plans.items()):
        if plan is None:
            continue
        if drop_plan:
            STATE["plans"].pop(key, None)
        else:
            plan["stale"] = True
    try:
        _persist_meta()
    except sqlite3.Error:
        pass
    _bump()  # состояние изменилось — консоли обновятся сами

