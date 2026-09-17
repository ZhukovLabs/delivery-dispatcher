"""Слой совместимости Flask → FastAPI.

Контекст запроса живёт в ContextVar: middleware (main.py) кладёт туда
starlette-запрос, распарсенный JSON и объект сессии; sync-обработчики
видят его в threadpool (anyio копирует контекст). Фоновые потоки
(TG-поллер) контекста не имеют — session.get просто вернёт None.
"""
from __future__ import annotations

import contextvars
from typing import Any, Optional

from fastapi.responses import FileResponse, JSONResponse

from .shims_state import (
    Session,
    _GProxy,
    _RequestProxy,
    _SessionProxy,
    _ctx,
    _serializer,
    g,
    get_ctx,
    init_serializer,
    request,
    reset_request_ctx,
    session,
    set_request_ctx,
    sign_session,
    unsign_session,
)
from .shims_routes import (
    _json,
    flaskish,
    jsonify,
    send_file,
)
