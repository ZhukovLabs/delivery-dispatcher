"""История, экспорт, бэкап, статистика недели и дня.

Агрегатор ресурсных модулей routes_reports_* (контракт routes_dispatch.py
и импортёров `from .routes_reports import ...` сохранён)."""
from fastapi import APIRouter

from .routes_reports_hist import (api_backup, api_history,
                                  api_history_export, r as r_hist,
                                  _unlink_quiet)
from .routes_reports_stats import (api_report_day, r as r_stats,
                                   stats_couriers_day, stats_week)

r = APIRouter()
for _sub in (r_hist, r_stats):
    r.include_router(_sub)
