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
    ws.start(asyncio.get_running_loop())
    _tg_start_polling()
    log.info("api up (FastAPI + socket.io, auth=email)")
    yield


app = FastAPI(title="dispatcher-api", docs_url=None, redoc_url=None,
              openapi_url=None, lifespan=lifespan)
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
    if not sess.get("uid"):
        # кросс-доменный фронт (vercel.app → ts.net): браузер может блокировать
        # third-party cookie даже с SameSite=None — принимаем ту же подписанную
        # сессию в Authorization: Bearer (фронт кладёт токен из /api/login)
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            try:
                sess.update(unsign_session(auth[7:].strip()))
                sess.modified = False  # сессия из заголовка — не эхоить Set-Cookie
            except Exception:  # noqa: BLE001 — битый токен = аноним
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
        # host в заголовке может нести порт (funnel на 8443) — для проверки
        # домена порт срезаем, иначе «...ts.net:8443» не матчится с .ts.net
        # и cookie уходит SameSite=Lax: кросс-доменный фронт её не пришлёт
        _host = (request.headers.get("host") or "").lower()
        _host = _host.rpartition(":")[0] if _host.rpartition(":")[2].isdigit() else _host
        behind_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
            or _host.endswith(".ts.net")
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
    # детали (тип, путь, стектрейс) — в лог; клиенту — генерик-текст,
    # чтобы наружу не утекали внутренности (пути, SQL и пр.)
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": "Внутренняя ошибка сервера"}, status_code=500)


# CORS регистрируем ПОСЛЕДНИМ (в конец файла): Starlette ставит последний
# добавленный middleware внешним. Он должен оборачивать flask_compat —
# иначе guard-ответы (401) и префлайты уходят без Access-Control-Allow-*.
# REST в проде ходит с фронта (vercel.app) напрямую на API-хост (funnel);
# WS (socket.io) имеет собственный cors_allowed_origins.

# gzip обязательна при прямых запросах мимо Vercel: полное состояние депо —
# десятки КБ JSON, без сжатия h1-канал funnel отдаёт его в разы дольше,
# чем облако (gzip + h2). Сжатие ~5-10×, окупает себя на любом payload >1КБ.
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
app.add_middleware(GZipMiddleware, minimum_size=1024)

_CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS",
    "https://barak-dispatcher.vercel.app,http://localhost:3000,http://127.0.0.1:3000",
).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_CORS_ORIGINS, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"])


def serve() -> None:
    import uvicorn
    uvicorn.run(app, host=CFG["host"], port=CFG["port"], log_config=None,
                access_log=False)
