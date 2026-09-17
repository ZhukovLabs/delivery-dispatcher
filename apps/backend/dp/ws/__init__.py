"""WS-хаб живых обновлений (socket.io): румы по депо, бродкаст состояния.

Вместо long-poll /api/rev: клиенты подписываются на рум своей рабочей точки;
любое изменение состояния (_bump из любого потока) коалесится (гео летит
каждые 5 с — слать payload чаще раза в ~150 мс нет смысла) и рассылается
подписчикам payload'ом этого депо.

Авторизация на handshake: токен = подпись cookie-сессии (фронт получает его
в теле /api/login) — кросс-доменный WS cookie (SameSite) не полагаемся.
"""
from .hub import (_DEBOUNCE_S, _GEO_FULL_RESYNC_S, _GEO_MIN_INTERVAL_S,
                  _SESSION_RECHECK_S, _flusher, _flusher_started, _geo,
                  _last_flush, _last_full_flush, _loop, _next_recheck,
                  _notify_lock, _sessions, log, notify_changed, sio,
                  socketio_app, start)
from .broadcast import _broadcast, _broadcast_geo
from .sessions import (_alive_uids, _recheck_sessions, _verify_token,
                       connect, disconnect, workpoint)
