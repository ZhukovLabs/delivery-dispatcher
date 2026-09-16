"""FastAPI-приложение диспетчерской: сборка, middleware совместимости, lifespan.

Вход (app.py): python app.py → uvicorn.run(dp.main.app).

Замена Flask: обработчики перенесены без изменения логики (dp.core /
dp.routes_*); Flask-примитивы (request/session/jsonify/кортеж-ответ)
эмулируют dp.shims + middleware ниже. Живые обновления — socket.io
(dp.ws) вместо long-poll /api/rev.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import geocode, routes_auth, routes_dispatch, ws
from .core import (CFG, SESSION_SECRET, _me, _tg_start_polling, _touch_online,
                   ensure_default_admin, load_state, log)
from .geocode import _houses_disk_load, _warm_street_index
from .shims import (Session, init_serializer, reset_request_ctx, set_request_ctx,
                    sign_session, unsign_session)

_GUARD_EXEMPT = ("/login", "/api/login", "/health", "/")
_SESSION_COOKIE = "session"
_SESSION_MAX_AGE = 12 * 3600  # 12 часов, как PERMANENT_SESSION_LIFETIME

init_serializer(SESSION_SECRET)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    load_state()
    ensure_default_admin()
    _houses_disk_load()
    ws.start(asyncio.get_running_loop())
    threading.Thread(target=_warm_street_index, daemon=True).start()
    _tg_start_polling()
    log.info("api up (FastAPI + socket.io, auth=email)")
    yield


app = FastAPI(title="dispatcher-api", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)
# REST в проде ходит с фронта (vercel.app) напрямую на API-хост (funnel),
# в обход реврайтов облака: минус один сетевой хоп (~90 мс на клик).
# WS (socket.io) имеет собственный cors_allowed_origins.
_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "https://barak-dispatcher.vercel.app,http://localhost:3000,http://127.0.0.1:3000",
).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_CORS_ORIGINS, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"])
app.include_router(routes_auth.r)
app.include_router(routes_dispatch.r)
app.include_router(geocode.r)
# socket.io живёт на /socket.io/* — всё, что не совпало с API-роутами, уходит ему
app.mount("/", ws.socketio_app())


@app.middleware("http")
async def flask_compat(request: Request, call_next):
    # 1) тело запроса: читаем один раз (sync-обработчики не могут await)
    parsed = None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        raw = await request.body()
        if raw:
            try:
                parsed = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                parsed = None
    # 2) сессия: подписанная cookie (тот же секрет из БД — старые сессии живы)
    sess = Session()
    cookie = request.cookies.get(_SESSION_COOKIE)
    if cookie:
        try:
            sess.update(unsign_session(cookie))
        except Exception:  # noqa: BLE001 — битая/просроченная = аноним
            pass
    token = set_request_ctx(request, parsed, sess)

    # 3) guard: эквивалент before_request (вход/health открыты);
    #    /socket.io/* не пускаем через cookie-guard: handshake идёт с токеном
    #    в auth-payload (у WS нет cookie), валидирует сам ws-сервер
    path = request.url.path
    if path not in _GUARD_EXEMPT and not path.startswith("/socket.io/"):
        if not _me():
            reset_request_ctx(token)
            return JSONResponse({"error": "Требуется вход"}, status_code=401)
        _touch_online()  # любое действие админа продлевает его «онлайн»

    response = await call_next(request)

    # 4) cookie сессии кладём только при изменении (аналог
    #    SESSION_REFRESH_EACH_REQUEST=False: не затирать свежую старым ответом).
    #    За https-прокси (funnel) фронт живёт на другом домене — cookie
    #    должна быть SameSite=None; Secure, иначе браузер её не пошлёт.
    if sess.modified:
        behind_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
            or (request.headers.get("host") or "").lower().endswith(".ts.net")
        )
        if behind_https:
            response.set_cookie(_SESSION_COOKIE, sign_session(dict(sess)),
                                httponly=True, samesite="none", secure=True,
                                max_age=_SESSION_MAX_AGE, path="/")
        else:
            response.set_cookie(_SESSION_COOKIE, sign_session(dict(sess)),
                                httponly=True, samesite="lax",
                                max_age=_SESSION_MAX_AGE, path="/")
    reset_request_ctx(token)
    return response


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": f"Внутренняя ошибка: {exc}"}, status_code=500)


def serve() -> None:
    import uvicorn
    uvicorn.run(app, host=CFG["host"], port=CFG["port"], log_config=None,
                access_log=False)
