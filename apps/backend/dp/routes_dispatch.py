"""Роутер диспетчерской: агрегирует ресурсные модули routes_*."""
from fastapi import APIRouter

from .routes_assign import r as r_assign
from .routes_couriers import r as r_couriers
from .routes_notify import r as r_notify
from .routes_orders import r as r_orders
from .routes_plan import r as r_plan
from .routes_plan_edit import r as r_plan_edit
from .routes_points import r as r_points
from .routes_reports import r as r_reports
from .routes_solve import r as r_solve

r = APIRouter()
for _sub in (r_points, r_couriers, r_orders, r_plan, r_assign,
             r_solve, r_reports, r_plan_edit, r_notify):
    r.include_router(_sub)
