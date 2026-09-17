from __future__ import annotations

from typing import Any, Optional

from fastapi.responses import FileResponse, JSONResponse

from .shims_state import request


def jsonify(*args, **kwargs) -> Any:
    """Flask-семантика: jsonify(dict) / jsonify(k=v) → JSON-able тело.

    Возвращает как есть (dict/list): FastAPI сериализует сам. Ответ с кодом
    отличным от 200 — кортеж (jsonify(...), код), его разворачивает flaskish.
    """
    if args:
        body = args[0]
        return body if isinstance(body, (dict, list)) else {"data": body}
    return dict(kwargs)


def send_file(path: str, as_attachment: bool = False,
              download_name: Optional[str] = None,
              mimetype: Optional[str] = None, **_) -> FileResponse:
    return FileResponse(path, filename=download_name, media_type=mimetype)


def _json() -> dict:
    """Тело запроса как dict. Битый/пустой JSON => {} (валидацию делают ручки)."""
    return request.get_json(silent=True) or {}


def flaskish(fn):
    """Разворачивает Flask-кортежи (body, status) в JSONResponse.

    Надевается автоматом на каждый перенесённый обработчик.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*a, **kw):
        rv = fn(*a, **kw)
        if isinstance(rv, tuple):
            body = rv[0]
            code = int(rv[1]) if len(rv) > 1 and rv[1] is not None else 200
            headers = dict(rv[2]) if len(rv) > 2 and rv[2] else None
            if isinstance(body, (dict, list)):
                return JSONResponse(content=body, status_code=code,
                                    headers=headers)
            if isinstance(body, str):  # CSV и прочий текст
                from fastapi.responses import PlainTextResponse
                return PlainTextResponse(body, status_code=code, headers=headers)
            return body  # уже Response (FileResponse и т.п.)
        return rv

    return wrapper
