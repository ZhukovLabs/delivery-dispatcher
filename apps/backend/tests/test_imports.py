import sys

import dp.core
import dp.main
import dp.shims
import dp.ws

_EXPECTED = {"Any", "FileResponse", "JSONResponse", "Optional", "Session",
             "annotations", "contextvars", "flaskish", "g", "get_ctx",
             "init_serializer", "jsonify", "request", "reset_request_ctx",
             "send_file", "session", "set_request_ctx", "sign_session",
             "unsign_session"}


def test_modules_import_offline():
    for name in ("dp.core", "dp.main", "dp.ws", "dp.shims"):
        assert sys.modules[name] is not None
    assert dp.main.app is not None
    assert dp.core.STATE["settings"]["speed_kmh"] > 0


def test_shims_public_api():
    names = {n for n in dir(dp.shims) if not n.startswith("_")}
    assert len(names) == 19
    assert names == _EXPECTED
