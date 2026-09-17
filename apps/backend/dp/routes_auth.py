"""Аутентификация, сессии, пользователи, служебные ручки.

Агрегатор ресурсных модулей routes_auth_* (контракт main.py и импортёров
`from .routes_auth import ...` сохранён)."""
from fastapi import APIRouter

from .routes_auth_helpers import _client_ip
from .routes_auth_service import health, r as r_service, root
from .routes_auth_session import (api_ws_token, api_workpoint, login,
                                  login_api, logout_api, r as r_session)
from .routes_auth_users import (api_add_user, api_change_password,
                                api_del_user, api_reset_user_pwd,
                                api_upd_user, r as r_users)

r = APIRouter()
for _sub in (r_session, r_users, r_service):
    r.include_router(_sub)
