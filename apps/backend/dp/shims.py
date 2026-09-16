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

_ctx: contextvars.ContextVar = contextvars.ContextVar("dp_request", default=None)

_serializer = None  # инициализируется из main после загрузки секрета


def init_serializer(secret: str) -> None:
    global _serializer
    from itsdangerous import URLSafeTimedSerializer
    _serializer = URLSafeTimedSerializer(secret, salt="dp-session")


def sign_session(data: dict) -> str:
    if _serializer is None:
        raise RuntimeError("serializer не инициализирован (init_serializer)")
    return _serializer.dumps(dict(data))


def unsign_session(token: str, max_age: int = 12 * 3600) -> dict:
    if _serializer is None:
        raise RuntimeError("serializer не инициализирован (init_serializer)")
    return _serializer.loads(token, max_age=max_age)


def set_request_ctx(req, parsed_json, session_obj) -> contextvars.Token:
    return _ctx.set({"req": req, "json": parsed_json, "session": session_obj})


def reset_request_ctx(token: contextvars.Token) -> None:
    _ctx.reset(token)


def get_ctx() -> Optional[dict]:
    return _ctx.get()


class _RequestProxy:
    """Достаточно Flask-request для нашего кода: json/args/path/method/addr."""

    @property
    def _r(self):
        c = _ctx.get()
        if c is None:
            raise RuntimeError("request context доступен только внутри запроса")
        return c["req"]

    def get_json(self, silent: bool = False) -> Any:
        c = _ctx.get()
        if c is None:
            return None if silent else {}
        return c["json"]

    @property
    def args(self):
        return self._r.query_params

    @property
    def path(self) -> str:
        return self._r.url.path

    @property
    def method(self) -> str:
        return self._r.method

    @property
    def remote_addr(self) -> Optional[str]:
        client = self._r.client
        return client.host if client else None

    @property
    def cookies(self):
        return self._r.cookies


request = _RequestProxy()


class Session(dict):
    """Подписанная cookie-сессия (itsdangerous), семантика Flask.

    modified — выставляется при любой записи; middleware кладёт новую
    cookie только тогда (SESSION_REFRESH_EACH_REQUEST=False).
    """

    permanent = False

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.modified = False

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self.modified = True

    def pop(self, k, *d):
        self.modified = True
        return super().pop(k, *d)

    def clear(self):
        self.modified = True
        super().clear()

    def setdefault(self, k, d=None):
        if k not in self:
            self.modified = True
        return super().setdefault(k, d)

    def update(self, *a, **kw):
        self.modified = True
        super().update(*a, **kw)


class _SessionProxy:
    """session[...] / session.get — из контекста текущего запроса."""

    def _s(self) -> dict:
        c = _ctx.get()
        return c["session"] if c else {}

    def __getitem__(self, k):
        return self._s()[k]

    def __setitem__(self, k, v):
        self._s()[k] = v

    def __contains__(self, k):
        return k in self._s()

    def get(self, k, d=None):
        return self._s().get(k, d)

    def keys(self):
        return self._s().keys()

    def values(self):
        return self._s().values()

    def items(self):
        return self._s().items()

    def __iter__(self):
        return iter(self._s())

    def pop(self, k, *d):
        return self._s().pop(k, *d)

    def clear(self):
        self._s().clear()

    @property
    def permanent(self):
        return self._s().permanent

    @permanent.setter
    def permanent(self, v: bool):
        self._s().permanent = v


session = _SessionProxy()


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


class _GProxy(dict):
    """flask.g — per-request кэш. Без контекста ведёт себя как пустой dict."""

    def __init__(self):
        super().__init__()
        c = _ctx.get()
        if c is not None:
            c.setdefault("g", {})
            self._live = c["g"]
        else:
            self._live = {}

    def __getitem__(self, k):
        return self._live[k]

    def get(self, k, d=None):
        return self._live.get(k, d)

    def __setitem__(self, k, v):
        self._live[k] = v


g = _GProxy()


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
