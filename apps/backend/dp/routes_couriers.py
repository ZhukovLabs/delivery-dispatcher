"""Ручки курьеров: CRUD, привязка Telegram, симуляция бота.

Агрегатор ресурсных модулей routes_couriers_* (контракт routes_dispatch.py
и импортёров `from .routes_couriers import ...` сохранён)."""
import threading

from fastapi import APIRouter

from .routes_couriers_crud import (add_courier, del_courier, r as r_crud,
                                   set_courier_point, upd_courier)
from .routes_couriers_sim import r as r_sim, sim_geo, sim_tgcb, sim_tgtext
from .routes_couriers_tg import bind_courier, r as r_tg, unbind_courier

r = APIRouter()
_solving_lock = threading.Lock()
for _sub in (r_crud, r_tg, r_sim):
    r.include_router(_sub)
