# -*- coding: utf-8 -*-
"""Основные ручки диспетчерской: состояние, точки, курьеры, заказы, план, solve."""
import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta

import requests
from fastapi import APIRouter

from .core import (CFG, MAX_POINTS, PALETTE, STATE, STATUSES, _approach_map,
                   _archive_order, _attach_geometry, _bump, _courier_plan,
                   _courier_speed, _db, _db_path, _db_lock, _deadline_rel_min,
                   _check_user_contact, _depot_view, _esc, _eta_pass, _ev, _flip_return_route,
                   _history_period, _home_point, _HOURLY_TRAFFIC,
                   _invalidate_plan, _me,
                   _my_point, _now, _obj_point, _payload, _persist_couriers,
                   _persist_meta, _persist_orders, _plan_for, _plural,
                   _tg_callback, _tg_handle_update, _tg_send, _valid_latlng,
                    build_time_matrix, haversine_km, log, routing_geometry,
                    solve_plan)
from .geocode import reverse_geocode
from .shims import _json, flaskish, jsonify, request, send_file, session

r = APIRouter()

_solving_lock = threading.Lock()


def _begin_solving(pid):
    """Атомарный захват «расчёт идёт» для депо. True — успели, False — уже идёт.

    Check-then-set без лока был гонкой потоков FastAPI (два запроса могли
    пройти проверку одновременно и запустить два solve одного депо).
    """
    with _solving_lock:
        if STATE.setdefault("solving", {}).get(pid):
            return False
        STATE["solving"][pid] = True
        return True


@r.get("/api/state")
@flaskish
def get_state():
    return _payload()


@r.get("/api/route")
@flaskish
def get_route():
    """Геометрия маршрута по дорогам для отрисовки на карте (клик по курьеру).
    coords=lat,lng;lat,lng... — 2..50 точек. Каскад тот же, что у расчётов:
    локальный OSRM → FOSSGIS → демо → ORS."""
    coords = (request.args.get("coords") or "").strip()
    pts = []
    try:
        for pair in coords.split(";"):
            la, ln = pair.split(",")
            pts.append({"lat": float(la), "lng": float(ln)})
    except ValueError:
        return jsonify({"error": "coords=lat,lng;lat,lng..."}), 400
    if not 2 <= len(pts) <= 50 or not all(_valid_latlng(p["lat"], p["lng"]) for p in pts):
        return jsonify({"error": "нужно 2..50 корректных точек"}), 400
    geom = routing_geometry(pts)
    if not geom:
        return jsonify({"error": "роутер недоступен"}), 502
    return jsonify({"geometry": geom})


def _points_changed(persist_couriers=False):
    """После любой правки точек выдачи: обновить совместимый вид депо,
    сохранить мета (или курьеров) и сбросить рассчитанный план."""
    STATE["depot"] = _depot_view()
    if persist_couriers:
        _persist_couriers()
    else:
        _persist_meta()
    _invalidate_plan(drop_plan=True)


@r.post("/api/depot")
@flaskish
def set_depot():
    """Совместимость со старым фронтом: двигает ПЕРВУЮ точку выдачи."""
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны координаты точки (lat, lng)"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    if not STATE.get("points"):
        return jsonify({"error": "Список точек пуст"}), 400
    address = (data.get("address") or "").strip() or reverse_geocode(lat, lng) or "Точка выдачи"
    STATE["points"][0].update({"address": address, "lat": lat, "lng": lng})
    _points_changed()
    return _payload()


@r.post("/api/points")
@flaskish
def add_point():
    """Добавить место выдачи заказов (точка, откуда курьеры забирают заказы)."""
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны координаты точки (lat, lng)"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    if len(STATE.get("points") or []) >= MAX_POINTS:
        return jsonify({"error": f"Максимум {MAX_POINTS} точек выдачи"}), 400
    address = (data.get("address") or "").strip() or reverse_geocode(lat, lng) or "Точка выдачи"
    name = (data.get("name") or "").strip() or f"Точка {len(STATE['points']) + 1}"
    STATE["points"].append({"id": uuid.uuid4().hex[:8], "name": name[:40],
                            "address": address, "lat": lat, "lng": lng})
    _points_changed()
    return _payload()


@r.post("/api/points/{pid}")
@flaskish
def upd_point(pid):
    """Переименовать ({name}) или передвинуть ({address,lat,lng}) точку выдачи."""
    data = _json()
    p = next((x for x in STATE.get("points") or [] if x["id"] == pid), None)
    if not p:
        return jsonify({"error": "Точка не найдена"}), 404
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Имя точки не может быть пустым"}), 400
        p["name"] = name[:40]
    if data.get("lat") is not None or data.get("lng") is not None:
        try:
            lat, lng = float(data["lat"]), float(data["lng"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Нужны обе координаты (lat, lng)"}), 400
        if not _valid_latlng(lat, lng):
            return jsonify({"error": "Координаты вне диапазона"}), 400
        address = (data.get("address") or "").strip() or p["address"] \
            or reverse_geocode(lat, lng) or "Точка выдачи"
        p.update({"address": address, "lat": lat, "lng": lng})
    _points_changed()
    return _payload()


@r.delete("/api/points/{pid}")
@flaskish
def del_point(pid):
    pts = STATE.get("points") or []
    p = next((x for x in pts if x["id"] == pid), None)
    if not p:
        return jsonify({"error": "Точка не найдена"}), 404
    if len(pts) == 1:
        return jsonify({"error": "Должна остаться хотя бы одна точка выдачи"}), 400
    attached = [c["name"] for c in STATE["couriers"] if c.get("point_id") == pid]
    if attached:
        return jsonify({"error": "К точке привязаны курьеры: "
                                 + ", ".join(attached)
                                 + ". Сначала перевесьте их на другую точку"}), 400
    pts.remove(p)
    _points_changed()
    return _payload()


@r.post("/api/couriers/{cid}/point")
@flaskish
def set_courier_point(cid):
    """Перекинуть курьера на другое место выдачи."""
    data = _json()
    pid = (data.get("point_id") or "").strip()
    if not any(x["id"] == pid for x in STATE.get("points") or []):
        return jsonify({"error": "Точка выдачи не найдена"}), 404
    c = next((x for x in STATE["couriers"] if x["id"] == cid), None)
    if not c:
        return jsonify({"error": "Курьер не найден"}), 404
    if c.get("point_id") == pid:
        return _payload()
    c["point_id"] = pid
    STATE["depot"] = _depot_view()
    _persist_couriers()
    # его маршруты убираем из планов точечно (мог быть помощником в чужом
    # депо), планы остальных курьеров сохраняются с пометкой «устарел»
    _invalidate_plan(courier_id=cid)
    return _payload()


@r.post("/api/couriers")
@flaskish
def add_courier():
    name = (_json().get("name") or "").strip()
    if not name:
        return jsonify({"error": "Введите имя курьера"}), 400
    color = PALETTE[STATE["color_seq"] % len(PALETTE)]
    STATE["color_seq"] += 1
    STATE["couriers"].append({"id": uuid.uuid4().hex[:8], "name": name,
                              "status": "base", "color": color, "back_min": 15,
                              "tg_chat_id": "",
                              "point_id": (STATE.get("points") or [{}])[0].get("id", "")})
    _persist_couriers(), _persist_meta()
    _invalidate_plan()
    return _payload()


@r.patch("/api/couriers/{cid}")
@flaskish
def upd_courier(cid):
    data = _json()
    for c in STATE["couriers"]:
        if c["id"] == cid:
            if _home_point(c)["id"] != _my_point():
                return jsonify({"error": "Курьер другого депо — управлять может "
                                         "только диспетчер его точки"}), 403
            if "name" in data and data["name"].strip():
                c["name"] = data["name"].strip()
            if data.get("status") in STATUSES:
                c["status"] = data["status"]
                if c["status"] == "off":
                    # выключенный курьер не участвует в расчётах: его
                    # закрепления остались бы вечными unassigned
                    for o in STATE["orders"]:
                        if o.get("pin") == cid:
                            o["pin"] = ""
                    _persist_orders()
            if "back_min" in data:
                try:
                    c["back_min"] = min(480, max(0, int(data["back_min"])))
                except (TypeError, ValueError):
                    pass
            if "tg_chat_id" in data:
                new_tg = str(data["tg_chat_id"]).strip()[:64]
                if new_tg and not new_tg.isdigit():
                    return jsonify({"error": "ID Telegram должен быть числом"}), 400
                c["tg_chat_id"] = new_tg
            _persist_couriers()
            # курьер может быть помощником в чужом плане — но его маршрут
            # убираем точечно, чужие маршруты остаются с пометкой «устарел»
            _invalidate_plan(courier_id=cid)
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@r.post("/api/couriers/{cid}/bind")
@flaskish
def bind_courier(cid):
    """Привязка курьера к Telegram-пользователю (по chat_id из бота)."""
    data = _json()
    chat_id = str(data.get("chat_id") or "").strip()
    if not chat_id.isdigit():
        return jsonify({"error": "ID Telegram должен быть числом"}), 400
    for c in STATE["couriers"]:
        if c["id"] == cid:
            if _home_point(c)["id"] != _my_point():
                return jsonify({"error": "Курьер другого депо — управлять может "
                                         "только диспетчер его точки"}), 403
            # гео не должна утекать к двум курьерам сразу
            for other in STATE["couriers"]:
                if other is not c and other.get("tg_chat_id") == chat_id:
                    other["tg_chat_id"] = ""
                    other["tg_login"] = ""
            c["tg_chat_id"] = chat_id
            c["tg_login"] = (data.get("login")
                             or STATE["tg_seen"].get(chat_id, {}).get("login")
                             or "").strip()[:64]
            _persist_couriers()
            # приветствие — в фоне: сеть Telegram не должна держать запрос
            # диспетчера (таймаут 5 с × каскад — ощутимо на живом боте)
            msg = (f"Готово! Вы привязаны: курьер «{_esc(c['name'])}».\n\n"
                   "Включите <b>живую геолокацию</b>:\n"
                   "скрепка → «Геолокация» → «Поделиться моей геолокацией» → "
                   "время <b>«Пока не отключу»</b>.\n\n"
                   "Диспетчер увидит вас на карте.")
            threading.Thread(target=_tg_send, args=(chat_id, msg),
                             daemon=True).start()
            _bump()
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@r.post("/api/couriers/{cid}/unbind")
@flaskish
def unbind_courier(cid):
    for c in STATE["couriers"]:
        if c["id"] == cid:
            STATE["tg_pos"].pop(c.get("tg_chat_id") or "", None)
            c["tg_chat_id"] = ""
            c["tg_login"] = ""
            _persist_couriers()
            _bump()
            return _payload()
    return jsonify({"error": "Курьер не найден"}), 404


@r.post("/api/sim/geo")
@flaskish
def sim_geo():
    """Симулятор live-гео: подсунуть поллеру апдейт Telegram от курьера.

    Только администратор. Синтетический апдейт проходит тот же конвейер,
    что и настоящая геолокация (_tg_handle_update): сглаживание медианой,
    замер скорости, трекеры выдачи/доставки/авто-статусов, пинок картам.
    """
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор"}), 403
    data = _json()
    try:
        chat_id = str(int(str(data.get("chat_id"))))
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны chat_id (числом), lat, lng"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    u = {"edited_message": {
        "chat": {"id": int(chat_id)},
        "from": {"username": str(data.get("login") or f"sim_{chat_id[-4:]}")},
        "location": {"latitude": lat, "longitude": lng,
                     "live_period": 31536000,
                     "horizontal_accuracy": 12},
        "date": int(time.time())}}
    _tg_handle_update(u)
    return jsonify({"ok": True})


@r.post("/api/sim/tgcb")
@flaskish
def sim_tgcb():
    """Симулятор кнопок бота: «курьер нажал» инлайн-кнопку (для демо без TG).

    Только администратор. Пример: {"chat_id": 9100000, "data": "dlv:<oid>:y"}.
    Проходит через тот же _tg_callback, что и настоящие нажатия.
    """
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор"}), 403
    data = _json()
    try:
        chat_id = str(int(str(data.get("chat_id"))))
        cb_data = str(data["data"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Нужны chat_id (числом) и data"}), 400
    _tg_callback({"id": f"sim{int(time.time() * 1000) % 10 ** 9}",
                  "from": {"username": str(data.get("login") or "sim")},
                  "message": {"chat": {"id": int(chat_id)}, "message_id": 0},
                  "data": cb_data})
    return jsonify({"ok": True})


@r.delete("/api/couriers/{cid}")
@flaskish
def del_courier(cid):
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if courier and _home_point(courier)["id"] != _my_point():
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    # его развозимые заказы возвращаются в очередь, чтобы не зависли;
    # закреплённые за ним — тоже: курьера больше нет, pin остался бы
    # в решателе пустым allowed и вечным unassigned
    for o in STATE["orders"]:
        if o.get("assigned") == cid and o.get("status") == "out":
            o["status"] = "ready"
            o["assigned"] = ""
            o["out_at"] = ""
        if o.get("pin") == cid:
            o["pin"] = ""
    STATE["couriers"] = [c for c in STATE["couriers"] if c["id"] != cid]
    _persist_orders()
    _persist_couriers()
    _invalidate_plan(courier_id=cid)
    return _payload()


@r.post("/api/orders")
@flaskish
def add_order():
    data = _json()
    try:
        lat, lng = float(data["lat"]), float(data["lng"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Укажите точку заказа на карте или через поиск"}), 400
    if not _valid_latlng(lat, lng):
        return jsonify({"error": "Координаты вне диапазона"}), 400
    deadline = (data.get("deadline") or "").strip()
    if deadline and not _deadline_rel_min(deadline, 0) and deadline != "00:00":
        return jsonify({"error": "Дедлайн должен быть в формате ЧЧ:ММ"}), 400
    oid = uuid.uuid4().hex[:8]
    # заказ создаётся в депо диспетчера (селектор «Место работы»)
    pid = _my_point() or (data.get("point_id") or "").strip()
    pids = {p["id"] for p in STATE.get("points") or []}
    if pid not in pids:
        pid = (STATE["points"][0]["id"] if STATE.get("points") else "")
    STATE["orders"].append({
        "id": oid,
        "address": (data.get("address") or "").strip() or reverse_geocode(lat, lng) or f"Заказ {oid[:4]}",
        "lat": lat, "lng": lng,
        "created_at": _now().isoformat(timespec="seconds"),
        "prio": 1 if data.get("prio") else 0,
        "deadline": deadline if _deadline_rel_min(deadline, 0) is not None else "",
        "point_id": pid,
        "status": "ready",
        "assigned": "",
    })
    _persist_orders()
    _invalidate_plan()
    _ev("disp", "добавил заказ «" +
        ((data.get("address") or "").strip() or f"без адреса ({oid[:4]})") + "»")
    return _payload()


@r.patch("/api/orders/{oid}")
@flaskish
def patch_order(oid):
    """Переключение приоритета или дедлайна заказа."""
    data = _json()
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if "prio" in data:
        order["prio"] = 1 if data.get("prio") else 0
    if "deadline" in data:
        deadline = (data.get("deadline") or "").strip()
        if deadline and _deadline_rel_min(deadline, 0) is None:
            return jsonify({"error": "Дедлайн должен быть в формате ЧЧ:ММ"}), 400
        order["deadline"] = deadline
    _persist_orders()
    _invalidate_plan()
    return _payload()


@r.delete("/api/orders/{oid}")
@flaskish
def del_order(oid):
    """Отмена заказа (из очереди или из развозки) с записью в историю."""
    body = _json()
    outcome = body.get("outcome") if body.get("outcome") in ("delivered", "cancelled") \
        else "cancelled"
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if order:
        if _obj_point(order) != _my_point():
            return jsonify({"error": "Заказ другого депо"}), 403
        courier_name, courier_id = "", order.get("assigned") or ""
        if order.get("assigned"):
            c = next((c for c in STATE["couriers"] if c["id"] == order["assigned"]), None)
            courier_name = c["name"] if c else order["assigned"]
        elif outcome == "delivered":
            for r in (_plan_for(_obj_point(order)) or {}).get("routes", []):
                if any(s["order_id"] == oid for s in r["stops"]):
                    courier_name = r["courier_name"]
                    courier_id = courier_id or r.get("courier_id") or ""
                    break
        opid = _obj_point(order)
        _archive_order(order, outcome, courier_name, courier_id)
        _ev("disp", ("закрыл как доставленный «" if outcome == "delivered"
                     else "отменил «") + (order.get("address") or oid) + "»")
    else:
        opid = None
    STATE["orders"] = [o for o in STATE["orders"] if o["id"] != oid]
    # доставлен последний заказ развозки — курьер едет домой по улицам
    if outcome == "delivered" and courier_id:
        _flip_return_route(courier_id)
    _persist_orders()
    _invalidate_plan(drop_plan=True, pid=opid)
    return _payload()


def _retiming_bases(routes):
    """Точки выдачи для маршрутов: (список домов, {courier_id: узел дома}).

    Берёт home_point из плана (падение в текущую точку курьера для старых планов).
    """
    homes, home_idx, home_of = [], {}, {}
    for r in routes:
        hp = r.get("home_point")
        if not hp:
            c = next((x for x in STATE["couriers"] if x["id"] == r["courier_id"]), None)
            hp = _home_point(c) if c else (STATE.get("points") or [None])[0]
        key = hp["id"] if hp else ""
        if key not in home_idx:
            home_idx[key] = len(homes)
            homes.append(hp)
        home_of[r["courier_id"]] = home_idx[key]
    return homes, home_of


def _retiming_matrix(plan, pid, routes):
    """Матрица для пересчёта ETA/переносов в ГОТОВОМ плане.

    Основной путь — тот же набор точек, что при расчёте плана
    (plan.matrix_ctx): ключ кэша матрицы совпадает, сеть не трогаем.
    Без контекста (старые планы до обновления) или при исчезнувших заказах —
    свежая матрица по своему депо: раньше сюда замешивались ВСЕ готовые
    заказы всех депо (и даже выданные) — набор точек не совпадал с расчётным,
    кэш промахивался и каждая выдача ходила за матрицей в сеть.

    Возвращает (matrix_сек, node: order_id -> индекс, appr_home, home_of).
    """
    homes, home_of = _retiming_bases(routes)
    ctx = (plan or {}).get("matrix_ctx")
    if ctx and ctx.get("order_ids") and ctx.get("homes"):
        ctx_homes = [{"lat": la, "lng": ln} for la, ln in ctx["homes"]]
        K = ctx.get("k") or len(ctx_homes)
        by_id = {o["id"]: o for o in STATE["orders"]}
        ctx_orders = [by_id.get(oid) for oid in ctx["order_ids"]]
        if all(o is not None for o in ctx_orders):
            pos = {(round(h["lat"], 5), round(h["lng"], 5)): i
                   for i, h in enumerate(ctx_homes)}
            h_of = {}
            for cid, hi in home_of.items():
                key = (round(homes[hi]["lat"], 5), round(homes[hi]["lng"], 5))
                if key not in pos:
                    h_of = None
                    break
                h_of[cid] = pos[key]
            if h_of is not None:
                points = ctx_homes + ctx_orders
                try:
                    matrix, _, _, _ = build_time_matrix(points, STATE["settings"],
                                                        k_homes=K)
                except Exception:
                    log.warning("retiming: матрица из matrix_ctx не собралась, "
                                "берём свежую по своему депо", exc_info=True)
                    matrix = None
                if matrix is not None:
                    node = {oid: K + i for i, oid in enumerate(ctx["order_ids"])}
                    appr = [_approach_map(points, h, K, STATE["settings"])
                            for h in ctx_homes]
                    return matrix, node, appr, h_of
    # фоллбек: точки маршрутов + готовые заказы ТОЛЬКО своего депо
    K = len(homes)
    ready = [o for o in STATE["orders"]
             if (o.get("status") or "ready") == "ready" and _obj_point(o) == pid]
    points = homes + ready
    matrix, _, _, _ = build_time_matrix(points, STATE["settings"], k_homes=K)
    node = {o["id"]: K + i for i, o in enumerate(ready)}
    appr = [_approach_map(points, h, K, STATE["settings"]) for h in homes]
    return matrix, node, appr, home_of


def _patch_plan_after_assign(oids, cid=None):
    """Убрать выданные заказы из плана и пересчитать ETA оставшихся курьеров.

    План остаётся рабочим: диспетчер сразу выдаёт маршруты следующим курьерам,
    не дожидаясь полного пересчёта. Матрица берётся из кэша (набор точек тот же,
    что при расчёте), ETA пересчитываются от текущего момента.
    """
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    plan = _courier_plan(courier) if courier else _plan_for(_my_point())
    pid = (_home_point(courier)["id"] if courier and _home_point(courier)
           else _my_point())
    if not plan or not plan.get("routes"):
        return False
    oidset = set(oids)
    touched = set()
    # порядок выданных остановок — в сами заказы, а дорожную геометрию выданной
    # части трипа (до последней выданной остановки) сохраняем курьеру: по ней
    # карта рисует активную развозку по дорогам, когда маршрут ушёл из плана
    order_by_id = {o["id"]: o for o in STATE["orders"]}
    k = 0
    out_geom = []
    if courier:
        courier.pop("ret_geom", None)  # новая выдача отменяет возврат
        for r in plan["routes"]:
            if r["courier_id"] != courier["id"]:
                continue
            for tr in r.get("trips", []):
                geom = tr.get("geometry") or []
                last_idx = -1
                for s in tr["stops"]:
                    if s["order_id"] not in oidset:
                        continue
                    o = order_by_id.get(s["order_id"])
                    if o is not None:
                        o["out_no"] = k
                        k += 1
                    if geom:
                        gi = min(range(len(geom)),
                                 key=lambda i: (geom[i][0] - s["lat"]) ** 2
                                               + (geom[i][1] - s["lng"]) ** 2)
                        last_idx = max(last_idx, gi)
                if last_idx >= 0:
                    out_geom.extend(geom[:last_idx + 1])
    if out_geom:
        courier["out_geom"] = (courier.get("out_geom") or []) + out_geom
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            before = len(tr["stops"])
            tr["stops"] = [s for s in tr["stops"] if s["order_id"] not in oidset]
            if len(tr["stops"]) != before:
                touched.add(r["courier_id"])
        r["trips"] = [tr for tr in r.get("trips", []) if tr["stops"]]
    if not touched:
        return False
    now = _now()
    try:
        # матрица и ретайминг — по ВСЕМ маршрутам плана: ETA нетронутых
        # курьеров тоже должны быть «от сейчас», а не от момента расчёта
        matrix, node, appr_home, home_of = _retiming_matrix(plan, pid,
                                                            plan["routes"])
        for r in plan["routes"]:
            h = home_of[r["courier_id"]]
            _retime_route(r, matrix, node, STATE["settings"], now, appr_home[h], h)
        # задержки плана перепривязаны к «сейчас» — фиксируем новый якорь,
        # иначе _refresh_plan_delays досдвигал бы их ещё раз от solved_at
        plan["anchored_at"] = now.isoformat(timespec="seconds")
    except Exception:
        log.warning("plan retime after assign failed, агрегаты пересобраны без матрицы")
        for r in plan["routes"]:
            r["stops"] = [s for tr in r.get("trips", []) for s in tr["stops"]]
            r["count"] = len(r["stops"])
    plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
    if not plan["routes"]:
        # всё выдано: мёртвый пустой план никому не нужен, а фоновый
        # пересчёт подхватит заказы, которые могли остаться вне маршрутов
        STATE["plans"].pop(pid, None)
        _persist_meta()
        _invalidate_plan(pid=pid)
        return True
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» больше не соответствуют плану
    plan.pop("moved", None)
    _persist_meta()
    _bump()  # выдача видна всем консолям сразу
    return True


@r.post("/api/plan/pin")
@flaskish
def plan_pin():
    """Закрепить готовый заказ за курьером и пересчитать план.

    Заказ остаётся ready («в развозку» уходит только по явной выдаче),
    но при расчёте его повезёт именно этот курьер.
    """
    data = _json()
    oid, cid = data.get("order_id"), data.get("courier_id")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if (order.get("status") or "ready") != "ready":
        return jsonify({"error": "Заказ уже в развозке"}), 400
    if courier["status"] == "off":
        return jsonify({"error": f"{courier['name']} недоступен: включите его статусом"}), 400
    opid = _obj_point(order)
    if opid != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if opid and opid != _home_point(courier)["id"]:
        pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == opid),
                  "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — закрепить можно только "
                                 f"за курьером этой точки"}), 400
    order["pin"] = cid
    _persist_orders()
    if not _begin_solving(opid):
        order["pin"] = ""
        _persist_orders()
        return jsonify({"error": "Расчёт развозки уже идёт — подождите окончания"}), 409
    try:
        try:
            plan = solve_plan(point_id=opid)
        except (ValueError, RuntimeError) as e:
            order["pin"] = ""
            _persist_orders()
            return jsonify({"error": str(e)}), 400
        # закрепление должно попасть в план; если курьер не смог взять заказ
        # (лимиты заказов/заездов исчерпаны) — откатываем и говорим прямо,
        # раньше pin молча оставался, а заказ выпадал из маршрутов
        if not any(s["order_id"] == oid
                   for r in (plan or {}).get("routes", [])
                   for s in r.get("stops", [])):
            order["pin"] = ""
            _persist_orders()
            return jsonify({"error": f"«{courier['name']}» не может взять заказ "
                                     "(лимит заказов/заездов исчерпан) — закрепление "
                                     "отменено"}), 400
        log.info("pin: %s -> %s", oid, courier["name"])
        return _payload()
    finally:
        STATE["solving"][opid] = False
        _bump()


@r.post("/api/plan/help")
@flaskish
def plan_help():
    """Разовая помощь: курьер подъезжает к чужой точке и берёт один заказ.

    Точка и статус курьера НЕ меняются — только этот расчёт плана.
    """
    data = _json()
    cid, pid = data.get("courier_id"), data.get("point_id")
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    point = next((p for p in STATE.get("points") or [] if p["id"] == pid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if not point:
        return jsonify({"error": "Точка выдачи не найдена"}), 404
    first_pid = STATE["points"][0]["id"] if STATE.get("points") else ""
    if not any((o.get("status") or "ready") == "ready"
               and (o.get("point_id") or first_pid) == pid
               for o in STATE["orders"]):
        return jsonify({"error": "У этой точки нет готовых заказов — помогать не с чем"}), 400
    helpers = {cid: pid} if _home_point(courier)["id"] != pid else {}
    if not _begin_solving(pid):
        return jsonify({"error": "Расчёт развозки уже идёт — подождите окончания"}), 409
    try:
        try:
            solve_plan(helpers=helpers, point_id=pid)
        except (ValueError, RuntimeError) as e:
            return jsonify({"error": str(e)}), 400
        log.info("plan help: %s -> точка %s", courier["name"], point["name"])
        return _payload()
    finally:
        STATE["solving"][pid] = False
        _bump()


def _tg_route_message(courier_name, stops, me, origin=None):
    """Текст+клавиатура TG-сообщения о выдаче: адреса с ETA, у каждого —
    ссылки «Маршрут: Яндекс | Google» с новой строки, внизу — кнопки
    «Весь маршрут» друг под другом. origin="lat,lng" — точка старта
    (Google без origin спрашивает начальную точку сам и в браузере
    внутри Telegram геолокации не имеет — поэтому ставим её сами).
    Используется и при отправке (assign), и при редактировании (return)."""
    def _ya_link(sp):
        if sp.get("lat") is None or sp.get("lng") is None:
            return None
        return (f"https://yandex.ru/maps/?rtext=~{sp['lat']},{sp['lng']}"
                "&rtt=auto")
    def _gg_link(sp):
        if sp.get("lat") is None or sp.get("lng") is None:
            return None
        return (f"https://www.google.com/maps/dir/?api=1"
                f"&destination={sp['lat']},{sp['lng']}&travelmode=driving")
    z_word = _plural(len(stops), ("заказ", "заказа", "заказов"))
    lines = [f"🛵 <b>{_esc(courier_name)}, в развозку</b>: {len(stops)} {z_word}"]
    for i, s in enumerate(stops, start=1):
        head = f"{i}. {_esc(s['address'])}"
        if s.get("eta_clock"):
            head += f" · ≈{s['eta_clock']}"
        lines.append(head)
        ya, gg = _ya_link(s), _gg_link(s)
        if ya and gg:
            lines.append(f"    Маршрут: <a href=\"{ya}\">Яндекс</a>"
                         f" | <a href=\"{gg}\">Google</a>")
    lines.append("Время приблизительное, следите за сообщениями.")
    if (me or {}).get("name") and (me or {}).get("phone"):
        lines.append(f"\nЕсть вопросы? - {_esc(me['name'])}, {me['phone']}")
    payload = {"text": "\n".join(lines), "parse_mode": "HTML"}
    pts = [f"{s['lat']},{s['lng']}" for s in stops
           if s.get("lat") is not None and s.get("lng") is not None]
    if len(pts) >= 2:  # маршрут строим минимум по двум точкам
        gg = ("https://www.google.com/maps/dir/?api=1"
              + (f"&origin={origin}" if origin else "")
              + "&destination=" + pts[min(len(pts), 10) - 1]
              + "&waypoints=" + "%7C".join(pts[:min(len(pts), 10) - 1])
              + "&travelmode=driving")
        kb = [[{"text": "Яндекс | Весь маршрут",
                "url": "https://yandex.ru/maps/?rtext=~"
                       + "~".join(pts[:10]) + "&rtt=auto"}],
              [{"text": "Google | Весь маршрут", "url": gg}]]
        payload["reply_markup"] = {"inline_keyboard": kb}
    return payload


@r.post("/api/orders/assign")
@flaskish
def assign_orders():
    """Выдать заказы курьеру: статус ready -> out (в развозке)."""
    data = _json()
    oids = data.get("order_ids") or []
    cid = data.get("courier_id")
    if not isinstance(oids, list) or not oids:
        return jsonify({"error": "Не указаны заказы"}), 400
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if courier["status"] == "off":
        return jsonify({"error": f"{courier['name']} недоступен: включите его статусом"}), 400
    courier_pid = _home_point(courier)["id"]
    if courier_pid != _my_point():
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    bad = [o for o in STATE["orders"]
           if o["id"] in oids and (o.get("status") or "ready") == "ready"
           and _obj_point(o) != courier_pid]
    if bad:
        pt = next((p["name"] for p in STATE.get("points", [])
                   if p["id"] == _obj_point(bad[0])), "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — выдать может только "
                                 f"курьер этой точки"}), 400
    now = _now().isoformat(timespec="seconds")
    # снимаем адреса/координаты/ETA выданных стопов ДО патча плана —
    # для авто-сообщения курьеру (с кнопками маршрута в Яндекс Картах)
    me = _me()
    oid_set = set(oids)
    by_oid = {o["id"]: o for o in STATE["orders"]}

    def _stop_snapshot(s):
        o = by_oid.get(s.get("order_id") or "")
        return {"address": s.get("address") or (o or {}).get("address", ""),
                "eta_clock": s.get("eta_clock"),
                "lat": (o or {}).get("lat"), "lng": (o or {}).get("lng")}

    route = next((r for r in (_courier_plan(courier) or {}).get("routes", [])
                  if r["courier_id"] == cid), None)
    given_stops = ([_stop_snapshot(s)
                    for tr in (route or {}).get("trips", [])
                    for s in tr["stops"] if s["order_id"] in oid_set]) if route else []
    if not given_stops:  # выдача мимо плана — хотя бы адреса
        given_stops = [{"address": o["address"], "eta_clock": None,
                        "lat": o.get("lat"), "lng": o.get("lng")}
                       for o in STATE["orders"] if o["id"] in oid_set]
    given = 0
    for o in STATE["orders"]:
        if o["id"] in oids and (o.get("status") or "ready") == "ready":
            o["status"] = "out"
            o["assigned"] = cid
            o["out_at"] = now
            o["pin"] = ""  # выдан — закрепление больше не нужно
            given += 1
    if not given:
        return jsonify({"error": "Заказы уже выданы или не найдены"}), 400
    _persist_orders()
    if not _patch_plan_after_assign(oids, cid=cid):
        _invalidate_plan(drop_plan=True, pid=courier_pid)
    log.info("assign: %d заказ(ов) -> %s", given, courier["name"])
    _ev("disp", f"выдал {given} заказ(ов) → {courier['name']}")

    # маршрут уходит курьеру в Telegram автоматически — раньше была
    # отдельная кнопка; шлём в фоне, выдача не ждёт сеть Telegram
    chat = (courier.get("tg_chat_id") or "").strip()
    if chat and CFG["tg_bot_token"] and given_stops:
        home = _home_point(courier)

        def _tg_assign():
            payload = _tg_route_message(courier["name"], given_stops, me,
                                        origin=f"{home['lat']},{home['lng']}"
                                        if home.get("lat") is not None else None)
            payload["chat_id"] = chat
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
                    json=payload, timeout=10)
                data = resp.json()
                if not data.get("ok"):
                    log.warning("assign tg: не ушло курьеру %s: %s",
                                courier["name"], resp.text[:200])
                else:
                    # запоминаем сообщение: при возврате заказа отредактируем его
                    STATE.setdefault("tg_assign", {})[cid] = {
                        "chat": chat, "mid": data.get("result", {}).get("message_id")}
                    log.info("telegram sent (assign): %s", courier["name"])
            except (requests.RequestException, ValueError) as e:
                log.warning("assign tg: %s", e)
        threading.Thread(target=_tg_assign, daemon=True).start()
    return _payload()


@r.post("/api/orders/{oid}/return")
@flaskish
def return_order(oid):
    """Вернуть заказ из развозки в очередь готовых."""
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if (order.get("status") or "ready") != "out":
        return jsonify({"error": "Заказ не в развозке"}), 400
    cid = order.get("assigned") or ""
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    order["status"] = "ready"
    order["assigned"] = ""
    order["out_at"] = ""
    _persist_orders()
    _invalidate_plan(pid=_obj_point(order))
    _ev("disp", f"вернул «{order.get('address') or oid}» в очередь")

    # TG-сообщение курьера должно жить вместе с планом: пересобираем его
    # по оставшимся заказам и редактируем (ссылки и кнопки обновятся)
    ref = STATE.get("tg_assign", {}).get(cid) if cid else None
    if ref and courier and CFG["tg_bot_token"]:
        stops = [{"address": o["address"], "eta_clock": None,
                  "lat": o.get("lat"), "lng": o.get("lng")}
                 for o in STATE["orders"]
                 if o.get("assigned") == cid and (o.get("status") or "ready") == "out"]
        me = _me()
        home = _home_point(courier)
        origin = (f"{home['lat']},{home['lng']}"
                  if home.get("lat") is not None else None)

        def _tg_edit():
            if stops:
                payload = _tg_route_message(courier["name"], stops, me,
                                            origin=origin)
            else:
                payload = {"text": f"📦 {_esc(courier['name'])}: все заказы"
                                   " сняты с развозки", "parse_mode": "HTML"}
            payload.update({"chat_id": ref["chat"], "message_id": ref["mid"]})
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{CFG['tg_bot_token']}"
                    "/editMessageText", json=payload, timeout=10)
                desc = resp.json().get("description", "")
                if "not modified" not in desc.lower():
                    if not resp.json().get("ok"):
                        log.warning("return tg: не отредактировано (%s): %s",
                                    courier["name"], desc[:200])
                if "message to edit not found" in desc.lower():
                    STATE.get("tg_assign", {}).pop(cid, None)
            except (requests.RequestException, ValueError) as e:
                log.warning("return tg: %s", e)
        threading.Thread(target=_tg_edit, daemon=True).start()
    return _payload()


@r.post("/api/couriers/{cid}/returned")
@flaskish
def courier_returned(cid):
    """Курьер вернулся на базу: все его развозимые заказы доставлены (факт)."""
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    if not courier:
        return jsonify({"error": "Курьер не найден"}), 404
    if _home_point(courier)["id"] != _my_point():
        return jsonify({"error": "Курьер другого депо — управлять может "
                                 "только диспетчер его точки"}), 403
    delivered = 0
    for o in STATE["orders"]:
        if o.get("assigned") == cid and o.get("status") == "out":
            _archive_order(o, "delivered", courier["name"], courier_id=cid)
            delivered += 1
    STATE["orders"] = [o for o in STATE["orders"]
                       if not (o.get("assigned") == cid and o.get("status") == "out")]
    courier["status"] = "base"
    courier["back_min"] = 0
    courier.pop("out_geom", None)  # развозка завершена — трассу больше не рисуем
    courier.pop("ret_geom", None)
    _persist_orders()
    _persist_couriers()
    _invalidate_plan(courier_id=cid)
    log.info("courier returned: %s, доставлено %d", courier["name"], delivered)
    _ev("cour", f"{courier['name']} вернулся на базу" +
        (f" — доставлено {delivered}" if delivered else ""))
    return _payload()


@r.post("/api/settings")
@flaskish
def set_settings():
    data = _json()
    s = STATE["settings"]
    try:
        s["speed_kmh"] = min(120, max(5, int(data.get("speed_kmh", s["speed_kmh"]))))
        s["handover_min"] = min(60, max(0, int(data.get("handover_min", s["handover_min"]))))
        s["max_orders"] = min(50, max(1, int(data.get("max_orders", s["max_orders"]))))
        s["traffic"] = round(min(3.0, max(1.0, float(data.get("traffic", s["traffic"])))), 2)
        s["lights_sec_per_km"] = round(min(60.0, max(0.0, float(data.get("lights_sec_per_km", s["lights_sec_per_km"])))), 0)
        s["auto_prio_min"] = min(240, max(0, int(data.get("auto_prio_min", s.get("auto_prio_min", 0)))))
        s["reload_min"] = min(120, max(0, int(data.get("reload_min", s.get("reload_min", 10)))))
        s["hour_traffic"] = 1 if data.get("hour_traffic", s.get("hour_traffic", 1)) else 0
        s["approach_center_min"] = min(15, max(0, int(data.get("approach_center_min", s.get("approach_center_min", 4)))))
        s["approach_far_min"] = min(15, max(0, int(data.get("approach_far_min", s.get("approach_far_min", 2)))))
    except (TypeError, ValueError):
        return jsonify({"error": "Параметры должны быть числами"}), 400
    _persist_meta()
    _invalidate_plan(drop_plan=True)
    return _payload()


# Пороги «стоит ли ждать возвращающегося курьера»: ожидание выгодно при
# выигрыше ≥5 мин до последней доставки или ≥2 мин к среднему времени доставки.
_GAIN_LAST_MIN = 5
_GAIN_AVG_MIN = 2


def _advice_side(plan):
    counts = ", ".join(f'{r["courier_name"]}: {r["count"]}' for r in plan["routes"])
    return {"counts": counts or "–",
            "last_clock": plan["last_delivery_clock"],
            "last_min": plan["last_delivery_min"],
            "avg_min": plan["avg_delivery_min"]}


@r.post("/api/solve")
@flaskish
def solve():
    body = _json()
    mode = body.get("mode") if body.get("mode") in ("auto", "split", "now") else "auto"
    force = [x for x in (body.get("force") or []) if isinstance(x, str)]
    myp = _my_point()
    # один расчёт на депо: второй диспетчер получает отлуп, все клиенты депо
    # видят блокирующий оверлей, пока расчёт идёт
    if not _begin_solving(myp):
        return jsonify({"error": "Расчёт развозки уже идёт — подождите окончания"}), 409
    _bump()
    try:
        if force:
            for cid in force:
                c = next((c for c in STATE["couriers"] if c["id"] == cid), None)
                if not c:
                    return jsonify({"error": "Курьер не найден"}), 404
                pid = _home_point(c)["id"]
                if pid != myp:
                    pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == pid),
                              "другой точки")
                    return jsonify({"error": f"«{c['name']}» работает с точкой «{pt}» — "
                                             f"он не может участвовать в плане вашего депо"}), 400
                if not any((o.get("status") or "ready") == "ready"
                           and _obj_point(o) == pid
                           for o in STATE["orders"]):
                    pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == pid),
                              "его точки")
                    return jsonify({"error": f"У точки «{pt}» нет готовых заказов — "
                                             f"«{c['name']}» не сможет участвовать в плане"}), 400
        try:
            t0 = time.time()
            plan = _compute_plan(mode, force=force, point_id=myp)
            log.info("solve[%s]: %d routes, provider=%s, scenario=%s, %.1fs",
                     myp[:6], len(plan["routes"]), plan.get("provider"),
                     (plan.get("advice") or {}).get("chosen", "no-away"), time.time() - t0)
        except (ValueError, RuntimeError) as e:
            log.warning("solve failed: %s", e)
            return jsonify({"error": str(e)}), 400
        _persist_meta()
        counts = ", ".join(f'{r["courier_name"]}: {r["count"]}'
                           for r in plan["routes"]) or "нечего везти"
        _ev("disp", f"рассчитал развозку — {counts}")
        return _payload()
    finally:
        STATE["solving"][myp] = False
        _bump()


def _compute_plan(mode="auto", advice=True, force=None, point_id=None):
    """Ядро расчёта (без HTTP): план + совет «ждать/не ждать» своего депо.

    Вызывается ТОЛЬКО вручную: кнопка «Рассчитать» и выбор сценария совета.
    Ручной выбор («Не ждать»/«Ждать») помнится до смены обстановки.
    """
    point_id = point_id or _my_point()
    if mode in ("now", "split"):
        STATE["advice_modes"][point_id] = mode
    mode = STATE["advice_modes"].get(point_id) or mode
    plan_split = solve_plan(force=force, point_id=point_id)
    advice_obj = None
    mine = [c for c in STATE["couriers"] if _obj_point(c) == point_id]
    away = [c for c in mine
            if c["status"] == "away" and int(c.get("back_min", 15) or 0) > 0]
    has_base = any(c["status"] == "base" for c in mine)
    ready_n = sum(1 for o in STATE["orders"]
                  if (o.get("status") or "ready") == "ready" and _obj_point(o) == point_id)
    scenario = away and has_base and ready_n >= 2
    if not scenario:
        STATE["advice_modes"].pop(point_id, None)  # выбирать больше не из чего
    plan_now = None
    if (advice or mode == "now") and scenario:
        try:
            plan_now = solve_plan(include_away=False, with_geometry=False,
                                  force=force, point_id=point_id)
        except (ValueError, RuntimeError):
            plan_now = None
    if plan_now is not None:
        g_last = plan_now["last_delivery_min"] - plan_split["last_delivery_min"]
        g_avg = plan_now["avg_delivery_min"] - plan_split["avg_delivery_min"]
        recommend = ("split" if (g_last >= _GAIN_LAST_MIN or
                                 g_avg >= _GAIN_AVG_MIN) else "now")
        chosen = {"split": plan_split, "now": plan_now}[
            mode if mode != "auto" else recommend]
        if chosen is plan_now:
            _attach_geometry(plan_now)
        STATE["plans"][point_id] = chosen
        if advice:
            advice_obj = {
                "recommend": recommend, "mode": mode,
                "chosen": "split" if chosen is plan_split else "now",
                "wait_couriers": [{"name": c["name"],
                                   "back_min": int(c.get("back_min", 15) or 0),
                                   "back_clock": (_now() + timedelta(
                                       minutes=int(c.get("back_min", 15) or 0))
                                                  ).strftime("%H:%M")} for c in away],
                "now": _advice_side(plan_now),
                "split": _advice_side(plan_split),
                "gain_last_min": g_last, "gain_avg_min": g_avg,
                "held": [{"courier": r["courier_name"], "address": s["address"]}
                         for r in plan_split["routes"] if r["status"] == "away"
                         for s in r["stops"]],
            }
            chosen["advice"] = advice_obj
    _bump()  # план готов — сообщаем всем открытым консолям
    return STATE["plans"].get(point_id)


# ---------- инвалидация плана (расчёт — только вручную, по кнопке) ----------


def _days_param():
    """?days=1..31 из запроса (по умолчанию 1)."""
    try:
        return min(31, max(1, int(request.args.get("days", 1))))
    except ValueError:
        return 1


@r.get("/api/history")
@flaskish
def api_history():
    return jsonify(_history_period(_days_param(), point_id=_my_point()))


@r.get("/api/history/export")
@flaskish
def api_history_export():
    """CSV за период (BOM — чтобы Excel сразу открыл кириллицу)."""
    days = _days_param()
    rows = _history_period(days, point_id=_my_point())["rows"]
    csv = ["Время закрытия;Адрес;Курьер;Исход;Цикл, мин"]
    for r in rows:
        csv.append(";".join(str(x if x is not None else "")
                            for x in (r["closed_at"].replace("T", " "), r["address"],
                                      r["courier"],
                                      "выдан" if r["outcome"] == "delivered" else "отменён",
                                      r["cycle_min"])))
    body = "\ufeff" + "\n".join(csv) + "\n"
    return (body, 200, {"Content-Type": "text/csv; charset=utf-8",
                        "Content-Disposition": f"attachment; filename=history-{days}d.csv"})


@r.get("/api/backup")
@flaskish
def api_backup():
    """Снимок базы данных (только админ) — скачивание файла."""
    me = _me()
    if not me or not me["is_admin"]:
        return jsonify({"error": "Только администратор может делать резервную копию"}), 403
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    try:
        with sqlite3.connect(_db_path, timeout=10) as src, \
                sqlite3.connect(tmp.name) as dst:
            src.backup(dst)
    except Exception:
        os.unlink(tmp.name)
        raise
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    return FileResponse(tmp.name, filename=f"dispatcher-backup-"
                       f"{_now().strftime('%Y%m%d-%H%M')}.db",
                       background=BackgroundTask(_unlink_quiet, tmp.name))


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass




# ---------- ручные правки плана: перенести остановку другому курьеру ----------

@r.post("/api/plan/move")
@flaskish
def plan_move():
    """Перенос заказа к другому курьеру в готовом плане (без полного пересчёта).

    Остановка снимается со своего места и вставляется в лучшую позицию лучшего
    заезда целевого курьера (минимальное удлинение маршрута). ETA обоих курьеров
    пересчитываются от текущего момента.
    """
    data = _json()
    oid, target = data.get("order_id"), data.get("to_courier")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    plan = _plan_for(_my_point())
    if not plan or not plan.get("routes"):
        return jsonify({"error": "Сначала рассчитайте план"}), 400
    dst = next((r for r in plan["routes"] if r["courier_id"] == target), None)
    if not dst:
        return jsonify({"error": "Курьер отсутствует в плане"}), 400
    dst_courier = next((c for c in STATE["couriers"] if c["id"] == target), None)
    opid = _obj_point(order)
    if dst_courier and opid and opid != _home_point(dst_courier)["id"]:
        pt = next((p["name"] for p in STATE.get("points", []) if p["id"] == opid),
                  "другой точки")
        return jsonify({"error": f"Заказ из точки «{pt}» — перенести можно только "
                                 f"курьеру этой точки"}), 400

    stop, src_id = None, None
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
            if hit:
                stop, src_id = hit, r["courier_id"]
                tr["stops"].remove(hit)
    if stop is None:
        return jsonify({"error": "Заказа нет в текущем плане"}), 400

    touched_routes = [r for r in plan["routes"]
                      if r["courier_id"] in {src_id, dst["courier_id"]}]
    matrix, node, appr_home, home_of = _retiming_matrix(plan, _my_point(),
                                                        touched_routes)
    h_dst = home_of[dst["courier_id"]]
    if oid not in node:
        return jsonify({"error": "Заказа нет в наборе точек плана — "
                                 "пересчитайте план"}), 400
    g_x = node[oid]

    best = None  # (удлинение, индекс заезда, позиция вставки)
    now = _now()
    hour_on = bool(int(STATE["settings"].get("hour_traffic", 1)))
    base_traffic = max(1.0, float(STATE["settings"].get("traffic", 1.3)))
    handover_s = max(0, int(STATE["settings"]["handover_min"])) * 60
    n_homes = len(appr_home)

    def _travel(u, w, f):
        """Дорожная часть дуги (сек) в масштабе часа f: базовый traffic
        из матрицы заменяется коэффициентом часа подъезда — вставка в
        разные позиции попадает в разные часы пик. Вручение/подъезд —
        константа вставки, на РАНГ позиций не влияют, не добавляем."""
        t = matrix[u][w]
        if w >= n_homes:
            t -= handover_s
        return t / base_traffic * f

    for ti, tr in enumerate(dst.get("trips", [])):
        # лимит max_orders соблюдает только решатель; ручной перенос из
        # «Готовых адресов» разрешён и сверх лимита — диспетчер видит,
        # что делает (ограничение вернётся при следующем пересчёте)
        seq = [h_dst] + [node[s["order_id"]] for s in tr["stops"]] + [h_dst]
        for pos in range(1, len(seq)):
            a, b = seq[pos - 1], seq[pos]
            if hour_on:
                # час подъезда к месту вставки — по ETA предыдущей остановки;
                # ETA плана привязаны к моменту своего последнего пересчёта
                # (eta_at) — добавляем возраст, иначе час пик занижается
                # на возраст плана
                if pos == 1:
                    t_prev = tr.get("start_delay_min") or 0
                else:
                    t_prev = tr["stops"][pos - 2].get("eta_min") or 0
                try:
                    eta_at = datetime.fromisoformat(
                        tr.get("eta_at") or plan.get("anchored_at")
                        or plan["solved_at"])
                    age_min = (now - eta_at).total_seconds() / 60.0
                except (ValueError, TypeError):
                    age_min = 0.0
                hour = (now + timedelta(minutes=t_prev + age_min)).hour
                f = _HOURLY_TRAFFIC.get(hour, 1.0)
            else:
                f = 1.0
            delta = _travel(a, g_x, f) + _travel(g_x, b, f) - _travel(a, b, f)
            if best is None or delta < best[0]:
                best = (delta, ti, pos - 1)
    if best is None and not dst.get("trips"):
        # у цели не было заездов — создаём первый
        dst["trips"] = [{"stops": [], "total_min": 0, "start_delay_min": 0,
                         "start_clock": "", "end_clock": "", "distance_km": None}]
        best = (0, 0, 0)
    if best is None:  # недостижимо: заезды без лимита всегда принимают вставку
        return jsonify({"error": f"У «{dst['courier_name']}» нет заездов — "
                                 "перенесите другому или пересчитайте план"}), 400
    dst["trips"][best[1]]["stops"].insert(best[2], stop)
    order["pin"] = target  # закрепляем и на будущие пересчёты плана
    _persist_orders()  # pin — часть заказа, без этого перенос терялся бы
                       # при рестарте (persist_meta хранит только планы)

    # пересчёт ETA затронутых курьеров от текущего момента
    touched = {src_id, dst["courier_id"]}
    try:
        for r in touched_routes:
            if r["courier_id"] in touched:
                h = home_of[r["courier_id"]]
                _retime_route(r, matrix, node, STATE["settings"], now,
                              appr_home[h], h)
    except Exception:
        log.warning("plan move retime failed, ETA оставлены как были")
    else:
        plan["anchored_at"] = now.isoformat(timespec="seconds")
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» после переноса не актуальны
    plan["moved"] = True
    log.info("plan move: %s -> %s (удлинение +%.0f мин)",
             oid, dst["courier_name"], best[0] / 60)
    _persist_meta()
    _bump()
    return _payload()


@r.post("/api/plan/unassign")
@flaskish
def plan_unassign():
    """Снять заказ с маршрута (drag остановки наружу, не к другому курьеру).

    Остановка убирается из заезда, pin заказа сбрасывается (иначе пересчёт
    вернул бы его тому же курьеру), ETA оставшихся курьеров пересчитываются
    от текущего момента. Заказ возвращается в очередь готовых.
    """
    data = _json()
    oid = data.get("order_id")
    order = next((o for o in STATE["orders"] if o["id"] == oid), None)
    if not order:
        return jsonify({"error": "Заказ не найден"}), 404
    if _obj_point(order) != _my_point():
        return jsonify({"error": "Заказ другого депо"}), 403
    if (order.get("status") or "ready") != "ready":
        return jsonify({"error": "Заказ уже в развозке — сначала верните его в очередь"}), 400
    pid = _my_point()
    plan = _plan_for(pid)
    if not plan or not plan.get("routes"):
        return jsonify({"error": "Сначала рассчитайте план"}), 400
    src_id = None
    for r in plan["routes"]:
        for tr in r.get("trips", []):
            hit = next((s for s in tr["stops"] if s["order_id"] == oid), None)
            if hit:
                tr["stops"].remove(hit)
                src_id = r["courier_id"]
        r["trips"] = [tr for tr in r.get("trips", []) if tr["stops"]]
    if src_id is None:
        return jsonify({"error": "Заказа нет в текущем плане"}), 400
    plan["routes"] = [r for r in plan["routes"] if r.get("trips")]
    order["pin"] = ""  # заказ снова свободен: без этого пересчёт вернул бы его
    _persist_orders()
    now = _now()
    if not plan["routes"]:
        # весь план состоял из этого заказа — пустой план не нужен
        STATE["plans"].pop(pid, None)
        _persist_meta()
        _invalidate_plan(pid=pid)
        _ev("disp", f"снял «{order.get('address') or oid}» с маршрута")
        _bump()
        return _payload()
    try:
        matrix, node, appr_home, home_of = _retiming_matrix(plan, pid,
                                                            plan["routes"])
        for r in plan["routes"]:
            h = home_of[r["courier_id"]]
            _retime_route(r, matrix, node, STATE["settings"], now,
                          appr_home[h], h)
        plan["anchored_at"] = now.isoformat(timespec="seconds")
    except Exception:
        log.warning("plan unassign retime failed, ETA оставлены как были")
    all_etas = [s["eta_min"] for r in plan["routes"] for s in r["stops"]]
    plan["last_delivery_min"] = max(all_etas, default=0)
    plan["last_delivery_clock"] = ((now + timedelta(
        minutes=plan["last_delivery_min"])).strftime("%H:%M") if all_etas else None)
    plan["avg_delivery_min"] = round(sum(all_etas) / len(all_etas)) if all_etas else 0
    plan["advice"] = None  # сценарии «ждать/не ждать» после снятия не актуальны
    plan["moved"] = True
    log.info("plan unassign: %s снят с маршрута %s", oid, src_id)
    _persist_meta()
    _ev("disp", f"снял «{order.get('address') or oid}» с маршрута")
    _bump()
    return _payload()


def _retime_route(route, matrix, node, settings, now_dt, appr=None, home=0):
    """Пересчёт ETA всех заездов курьера от now_dt. Меняет route на месте."""
    now_hm = now_dt.hour * 60 + now_dt.minute
    courier = next((c for c in STATE["couriers"]
                    if c["id"] == route.get("courier_id")), None)
    default_kmh = max(5.0, float(settings.get("speed_kmh", 60)))
    kmh, _src = _courier_speed(courier, settings) if courier else (default_kmh, "default")
    spd_factor = max(0.25, min(4.0, default_kmh / kmh))
    route["speed_kmh"] = round(kmh, 1)
    route["speed_src"] = _src
    reload_min = max(0, int(settings.get("reload_min", 10)))
    prev_end = None  # конец предыдущего заезда: старт следующего цепочим
    for tr in route.get("trips", []):
        seq = [node[s["order_id"]] for s in tr["stops"]]
        delay = tr["start_delay_min"] or 0
        if prev_end is not None:
            # цепочка как при сборке плана: после plan_move заезд могли
            # удлинить — старый старт нарушал бы её и занижал ETA
            delay = max(delay, prev_end + reload_min)
        tr["start_delay_min"] = delay
        etas, total = _eta_pass(seq, delay, matrix, settings,
                                now_dt, appr, home, spd_factor=spd_factor)
        for s, eta in zip(tr["stops"], etas):
            s["eta_min"] = eta
            s["eta_clock"] = (now_dt + timedelta(minutes=eta)).strftime("%H:%M")
            rel = _deadline_rel_min(s.get("deadline"), now_hm)
            s["late_min"] = max(0, eta - rel) if rel is not None else 0
        tr["total_min"] = total
        prev_end = total
        tr["end_clock"] = (now_dt + timedelta(minutes=total)).strftime("%H:%M")
        tr["start_clock"] = (now_dt + timedelta(
            minutes=tr["start_delay_min"])).strftime("%H:%M")
        tr["eta_at"] = now_dt.isoformat(timespec="seconds")  # якорь этих ETA
    route["stops"] = [s for tr in route.get("trips", []) for s in tr["stops"]]
    route["count"] = len(route["stops"])
    if route["stops"]:
        route["total_min"] = max(tr["total_min"] for tr in route["trips"])


# ---------- Telegram-уведомление курьеру (заготовка) ----------

@r.post("/api/notify/courier/{cid}")
@flaskish
def notify_courier(cid):
    """Отправка маршрута курьеру в Telegram (нужны токен бота и chat_id)."""
    me = _me()
    if not CFG["tg_bot_token"]:
        return jsonify({"error": "tg_bot_token не задан в config.ini"}), 400
    courier = next((c for c in STATE["couriers"] if c["id"] == cid), None)
    route = next((r for r in (_courier_plan(courier) or {}).get("routes", [])
                  if r["courier_id"] == cid), None)
    if not courier or not route:
        return jsonify({"error": "Курьер или маршрут не найден"}), 404
    chat = (courier.get("tg_chat_id") or "").strip()
    if not chat:
        return jsonify({"error": f"У курьера {courier['name']} не указан Telegram chat_id"}), 400
    z_word = _plural(route["count"], ("заказ", "заказа", "заказов"))
    lines = [f"🛵 <b>{_esc(route['courier_name'])}, маршрут на смену</b>: "
             f"{route['count']} {z_word}"]
    for ti, tr in enumerate(route["trips"], start=1):
        if len(route["trips"]) > 1:
            lines.append(f"Заезд {ti}: старт ≈{tr['start_clock']}")
        for i, s in enumerate(tr["stops"], start=1):
            lines.append(f"{i}. {_esc(s['address'])} · ≈{s['eta_clock']}")
    lines.append("Время приблизительное, следите за сообщениями.")
    if (me or {}).get("name") and (me or {}).get("phone"):
        lines.append(f"\nЕсть вопросы? - {_esc(me['name'])}, {me['phone']}")
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines), "parse_mode": "HTML"}, timeout=10)
        data = resp.json()
    except requests.RequestException as e:
        return jsonify({"error": f"Telegram недоступен: {e}"}), 502
    if not data.get("ok"):
        return jsonify({"error": f"Telegram: {data.get('description', 'ошибка')}"}), 400
    log.info("telegram sent: %s (%s)", courier["name"], cid)
    return jsonify({"ok": True})


@r.post("/api/profile")
@flaskish
def api_profile():
    """Имя и телефон текущего диспетчера (для подписи в сообщениях курьерам)."""
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    data = _json()
    try:
        name, phone = _check_user_contact(data.get("name"), data.get("phone"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    with _db_lock, _db() as c:
        c.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, me["id"]))
    log.info("profile updated: %s -> %s / %s", me["email"], name, phone)
    _bump()
    return _payload()


@r.post("/api/notify/tg")
@flaskish
def notify_tg():
    """Произвольное сообщение от имени бота привязанному пользователю (админ)."""
    me = _me()
    if not me:
        return jsonify({"error": "Требуется вход"}), 401
    if not me["is_admin"]:
        return jsonify({"error": "Только администратор может отправлять сообщения"}), 403
    if not CFG["tg_bot_token"]:
        return jsonify({"error": "tg_bot_token не задан в config.ini"}), 400
    data = request.get_json(silent=True) or {}
    chat = str(data.get("chat_id") or "").strip()
    text = str(data.get("text") or "").strip()
    if not chat or not text:
        return jsonify({"error": "Нужны chat_id и текст"}), 400
    if len(text) > 3500:
        return jsonify({"error": "Сообщение слишком длинное (макс. 3500 символов)"}), 400
    if not me.get("name") or not me.get("phone"):
        return jsonify({"error": "Заполните имя и телефон в профиле: шестерёнка → Профиль"}), 400
    text = f"{text}\n\nЕсть вопросы? - {me['name']}, {me['phone']}"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{CFG['tg_bot_token']}/sendMessage",
            json={"chat_id": chat, "text": text}, timeout=10)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        return jsonify({"error": f"Telegram недоступен: {e}"}), 502
    if not data.get("ok"):
        return jsonify({"error": f"Telegram: {data.get('description', 'ошибка')}"}), 400
    return jsonify({"ok": True})


# ---------- статистика недели ----------

@r.get("/api/stats/week")
@flaskish
def stats_week():
    """Динамика за 7 дней: по дням и по курьерам + доля вовремя."""
    hist = _history_period(7, point_id=_my_point())
    days, couriers = {}, {}
    on_time = on_time_total = 0
    for r in hist["rows"]:
        day = (r["closed_at"] or "")[:10]
        d = days.setdefault(day, {"day": day, "delivered": 0, "cancelled": 0,
                                  "cycles": []})
        if r["outcome"] == "delivered":
            d["delivered"] += 1
            if r["cycle_min"] is not None:
                d["cycles"].append(r["cycle_min"])
            name = r["courier"] or "–"
            c = couriers.setdefault(name, {"courier": name, "delivered": 0,
                                           "cycles": []})
            c["delivered"] += 1
            if r["cycle_min"] is not None:
                c["cycles"].append(r["cycle_min"])
            dl = r.get("deadline")
            if dl:
                # дедлайн принадлежит дню создания заказа: закрытие после
                # полуночи против вечернего дедлайна — это вовремя,
                # а не «00:10 < 23:50» по голым часам
                try:
                    dl_dt = datetime.fromisoformat(
                        (r.get("created_at") or "")[:10] + "T" + dl)
                    closed_dt = datetime.fromisoformat(r["closed_at"])
                    on_time_total += 1
                    if closed_dt <= dl_dt:
                        on_time += 1
                except (ValueError, TypeError):
                    pass
        else:
            d["cancelled"] += 1
    out_days = [{"day": d["day"], "delivered": d["delivered"],
                 "cancelled": d["cancelled"],
                 "avg_cycle_min": round(sum(d["cycles"]) / len(d["cycles"]))
                 if d["cycles"] else None}
                for d in sorted(days.values(), key=lambda x: x["day"])]
    out_cour = [{"courier": c["courier"], "delivered": c["delivered"],
                 "avg_cycle_min": round(sum(c["cycles"]) / len(c["cycles"]))
                 if c["cycles"] else None}
                for c in sorted(couriers.values(), key=lambda x: -x["delivered"])]
    return jsonify({"days": out_days, "couriers": out_cour,
                    "on_time": on_time, "on_time_total": on_time_total})


@r.get("/api/report/day")
@flaskish
def api_report_day():
    """Данные отчёта дня (печатную версию рендерит фронт)."""
    hist = _history_period(1, point_id=_my_point())
    plan = _plan_for(_my_point()) or {}
    routes = [{"courier_name": r.get("courier_name"), "status": r.get("status"),
               "stops": [s.get("address") for s in (r.get("stops") or [])]}
              for r in (plan.get("routes") or [])]
    return jsonify({"rows": hist["rows"], "summary": hist.get("summary") or {},
                    "routes": routes,
                    "today": _now().strftime("%d.%m.%Y"),
                    "now": _now().strftime("%H:%M")})


