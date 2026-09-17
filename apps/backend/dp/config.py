"""Конфигурация: config.ini + окружение, каталог данных, логирование."""
import configparser
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

# Минск: UTC+3, без перехода на летнее время. Все «настенные» времена
# (часы плана, история, дедлайны, сообщения) пишем по Минску независимо
# от часового пояса сервера. Значения остаются наивными (без оффсета),
# чтобы не ломать сравнения с уже записанными данными.
_MN = timezone(timedelta(hours=3))


def _now() -> datetime:
    return datetime.now(_MN).replace(tzinfo=None)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # apps/backend

from itsdangerous import URLSafeTimedSerializer

# BASE_DIR задаётся в шапке модуля (родитель dp/, чтобы config.ini/db лежали как раньше)


def _pick_data_dir():
    """Первый каталог, доступный на запись: рядом с кодом -> DISPATCHER_DATA -> temp.
    На PaaS с read-only FS (Belmo) код лежит в /app только для чтения."""
    import tempfile
    cands = [BASE_DIR,
             os.environ.get("DISPATCHER_DATA", ""),
             os.path.join(tempfile.gettempdir(), "dispatcher")]
    for d in cands:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("1")
            os.remove(probe)
            return d
        except OSError:
            continue
    return BASE_DIR


DATA_DIR = _pick_data_dir()

# ---------- конфигурация (config.ini рядом с app.py) ----------

CFG = {"ors_key": "", "host": "127.0.0.1", "port": 5050,
        "admin_email": "admin@local", "admin_password": "admin",
        "tg_bot_token": "", "tg_poll": 1, "tz": "Europe/Minsk",
        "db_path": os.path.join(DATA_DIR, "dispatcher.db")}

_cp = configparser.ConfigParser()
if _cp.read(os.path.join(BASE_DIR, "config.ini"), encoding="utf-8"):
    _s = _cp["app"] if _cp.has_section("app") else {}
    CFG["ors_key"] = _s.get("ors_key", "").strip()
    CFG["host"] = _s.get("host", "127.0.0.1").strip()
    try:
        CFG["port"] = _s.getint("port", 5050)
    except ValueError:
        pass
    CFG["admin_email"] = (_s.get("admin_email", "") or "admin@local").strip().lower()
    CFG["admin_password"] = _s.get("admin_password", "") or "admin"
    CFG["tg_bot_token"] = _s.get("tg_bot_token", "").strip()
    CFG["tz"] = _s.get("tz", "Europe/Minsk").strip() or "Europe/Minsk"
    try:
        CFG["tg_poll"] = _s.getint("tg_poll", 1)
    except ValueError:
        pass
    _db = _s.get("db_path", "").strip()
    if _db:
        CFG["db_path"] = _db if os.path.isabs(_db) else os.path.join(BASE_DIR, _db)

# Окружение перекрывает config.ini (деплой в облако: Render и т.п.)
CFG["ors_key"] = os.environ.get("ORS_KEY", "").strip() or CFG["ors_key"]
CFG["tg_bot_token"] = os.environ.get("TG_BOT_TOKEN", "").strip() or CFG["tg_bot_token"]
# tg_poll=0 — этот инстанс НЕ слушает бота (когда бот занят другим сервером,
# например локальный + облачный одновременно; Telegram отдаёт getUpdates одному)
if os.environ.get("TG_POLL", "").strip():
    try:
        CFG["tg_poll"] = 1 if os.environ["TG_POLL"].strip() not in ("0", "false", "no") else 0
    except ValueError:
        pass
CFG["admin_email"] = (os.environ.get("ADMIN_EMAIL", "").strip() or CFG["admin_email"]).lower()
CFG["admin_password"] = os.environ.get("ADMIN_PASSWORD", "").strip() or CFG["admin_password"]
for _pn in ("PORT", "SERVER_PORT", "P_SERVER_PORT"):  # PaaS/Pterodactyl отдают порт по-разному
    _pv = os.environ.get(_pn, "").strip()
    if _pv:
        try:
            CFG["port"] = int(_pv)
            CFG["host"] = "0.0.0.0"
            break
        except ValueError:
            pass
if CFG.get("tz"):
    os.environ["TZ"] = CFG["tz"]
    if hasattr(time, "tzset"):
        time.tzset()


# ---------- логирование ----------

_handlers = [logging.StreamHandler()]  # всегда: stderr (доступен на любом PaaS)
try:
    _handlers.insert(0, RotatingFileHandler(os.path.join(DATA_DIR, "dispatcher.log"),
                                            maxBytes=1_000_000, backupCount=3,
                                            encoding="utf-8"))
except OSError:
    pass  # read-only FS: живём только в stderr
logging.basicConfig(
    handlers=_handlers,
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("waitress").setLevel(logging.WARNING)
log = logging.getLogger("dispatcher")

DEFAULT_DEPOT = {"address": "ул. Подгорная 12/1, Гомель", "lat": 52.44146, "lng": 31.01476}
