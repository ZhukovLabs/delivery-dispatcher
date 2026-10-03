"""Имя участника закреплено: меняет только администратор."""
import contextlib
from types import SimpleNamespace

from dp import routes_notify as rn
from dp.shims_state import reset_request_ctx, set_request_ctx

import pytest


class _FakeCx:
    def __init__(self):
        self.queries = []

    def execute(self, q, p=()):
        self.queries.append((q, p))
        return self

    def fetchone(self):
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def env(monkeypatch):
    cx = _FakeCx()

    @contextlib.contextmanager
    def fake_db():
        yield cx

    monkeypatch.setattr(rn, "_db", fake_db)
    monkeypatch.setattr(rn, "_bump", lambda *a, **k: None)
    monkeypatch.setattr(rn, "_payload", lambda *a, **k: {"ok": True})
    req = SimpleNamespace(query_params={}, url=SimpleNamespace(path="/api/profile"),
                          method="POST", client=None)
    yield cx, req


def _upd_params(cx):
    q, p = next((q, p) for q, p in cx.queries if "UPDATE users" in q)
    return p


def test_member_cannot_rename(env, monkeypatch):
    monkeypatch.setattr(rn, "_me", lambda: {
        "id": "u1", "email": "n@x", "is_admin": 0, "name": "Настя", "phone": "+375291234560"})
    cx, req = env
    set_request_ctx(req, {"name": "Хакер", "phone": "+375291234567"}, {})
    out = rn.api_profile()
    assert isinstance(out, dict)  # успех: патченный _payload прошёл насквозь
    assert _upd_params(cx)[0] == "Настя"  # имя не изменилось
    assert _upd_params(cx)[1] == "+375291234567"  # телефон обновился


def test_member_without_name_gets_clear_error(env, monkeypatch):
    monkeypatch.setattr(rn, "_me", lambda: {
        "id": "u1", "email": "n@x", "is_admin": 0, "name": "", "phone": ""})
    cx, req = env
    set_request_ctx(req, {"name": "Хакер", "phone": "+375291234567"}, {})
    resp = rn.api_profile()
    assert resp.status_code == 400
    import json as _json
    assert "администратор" in _json.loads(resp.body)["error"]


def test_admin_can_rename_self(env, monkeypatch):
    monkeypatch.setattr(rn, "_me", lambda: {
        "id": "u2", "email": "a@x", "is_admin": 1, "name": "Старое", "phone": "+375291234560"})
    cx, req = env
    set_request_ctx(req, {"name": "Новое", "phone": "+375291234567"}, {})
    out = rn.api_profile()
    assert isinstance(out, dict)
    assert _upd_params(cx)[0] == "Новое"
