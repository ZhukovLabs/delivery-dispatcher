"""Выдача заказов курьеру, возврат заказа/курьера, синк маршрута в Telegram.

Агрегатор ресурсных модулей routes_assign_* (контракт routes_dispatch.py
и импортёров `from .routes_assign import ...` сохранён)."""
import threading

from fastapi import APIRouter

from .routes_assign_give import assign_orders, r as r_give
from .routes_assign_helpers import _tg_sync_route
from .routes_assign_returns import courier_returned, r as r_returns, return_order

r = APIRouter()
_solving_lock = threading.Lock()
for _sub in (r_give, r_returns):
    r.include_router(_sub)
