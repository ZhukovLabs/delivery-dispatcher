"""Фасад пакета dp: реэкспорт из специализированных модулей.

core.py сохраняет контракт `from .core import ...` для роутов, ws-хаба
и geocode; реализация разложена по одностраничным модулям dp/ (карта
зависимостей — в README). Импортировать внутренности нужно напрямую
из соответствующего модуля, а не отсюда."""

from .config import BASE_DIR, CFG, DATA_DIR, DEFAULT_DEPOT, _MN, _cp, _handlers, _now, _pick_data_dir, log

from .domain.text import _esc, _plural
from .domain.geo import _valid_latlng, haversine_km
from .adapters.http_hedge import hedged_first

from .state import MAX_POINTS, PALETTE, ROAD_FACTOR, STATE, STATUSES, _PRIO_WEIGHT, _depot_view, _home_point, _obj_point

from .adapters.sqlite_repo import _DB_MIGRATIONS, _DB_SCHEMA, _archive_order, _courier_day_stats, _db, _db_conn, _db_connect, _db_lock, _db_path, _history_period, _history_today, _persist_couriers, _persist_meta, _persist_orders, _speed_add

from .adapters.speed import _SPEED_CUR_MAX_AGE, _SPEED_CUR_WINDOW, _SPEED_KMH_BOUNDS, _SPEED_MAX_ACC_M, _SPEED_MIN_DEL_N, _SPEED_MIN_GEO_S, _SPEED_RATIO_BOUNDS, _SPEED_SEG_MIN_M, _courier_speed, _speed_current_kmh, _speed_fleet_cycle_avg, _speed_from_row, _speed_geo_sample, _speed_rows

from .bootstrap import SESSION_SECRET, _session_secret, load_state

from .adapters.osrm import OSRM_NAMES, OSRM_URLS, ROUTE_FINAL_S, ROUTE_HEDGE_S, _osrm_coords, _osrm_geometry_at, _osrm_matrix_at, osrm_get

from .adapters.ors import ORS_BASE, ORS_KEY, ORS_MAX_POINTS, ORS_SOFT_LIMIT, ORS_STATE, _ors_available, _seconds_to_utc_midnight, ors_geometry, ors_matrix, ors_post, ors_status

from .adapters.routing import _MATRIX_CACHE, _MATRIX_CACHE_MAX, _MATRIX_TTL, _cache_matrix, _matrix_key, _routing_providers, routing_table

from .adapters.geometry import _GEOM_CACHE, _GEOM_CACHE_MAX, _GEOM_TTL, _SIMPLIFY_MAX_IN, _geom_key, _simplify_poly, routing_geometry

from .adapters.matrix import build_time_matrix

from .domain.model import _APPROACH_RADIUS_KM, _ASAP_WEIGHT, _DROP_PENALTY, _HOURLY_TRAFFIC, _LATE_WEIGHT, _LOOP_EST_FACTOR, _MAX_TRIPS, _SPAN_WEIGHT, _approach_map, _deadline_rel_min, _eta_pass

from .services.solve import solve_plan

from .services.solve_geom import _attach_geometry

from .users import _LOGIN_FAILS, _LOGIN_LOCK_SEC, _LOGIN_MAX_FAILS, _PBKDF_ROUNDS, _admin_users, _check_user_contact, _check_user_email, _create_user, _hash_pwd, _me, _verify_pwd, ensure_default_admin

from .online import ONLINE, ONLINE_WINDOW, _ONLINE_LOCK, _courier_plan, _drop_online, _my_point, _plan_for, _refresh_plan_delays, _start_delay_min, _touch_online

from .adapters.telegram import TG_POS_TTL, TG_TEST_REDIRECT, _tg_answer_cb, _tg_api, _tg_edit_msg, _tg_out_chat, _tg_send, _tg_send_kb

from .bot_dwell import TG_GEO_AT_PLACE, TG_GEO_FRESH, _BOT_ASK_AFTER_S, _DELIVER_DWELL_S, _LOAD_DWELL_S, _courier_has_out, _courier_out_orders, _deliver_track, _load_track

from .bot_status import _AWAY_AUTO_KM, _AWAY_DWELL_S, _AWAY_ORDER_S, _BACK_DWELL_S, _auto_status_apply, _auto_status_track, _courier_geo

from .bot_flow import _CANCEL_REASONS, _bot_ask_kb, _bot_ask_text, _bot_close_delivered, _bot_keep_rolling, _flip_return_route, _pay_method_label, _pay_set, _tg_step_kb

from .bot_dialog import _tg_callback

from .bot_updates import _tg_handle_update

from .bot_poller import _tg_poll_loop, _tg_start_polling

from .payload import _build_payload_base, _geo_payload, _payload, _payload_base_cache, _payload_cache_lock, _points_with_admins

from .planstate import _bump, _ev, _invalidate_plan, _invalidate_plan_u, _plans_lock, _points_ids
