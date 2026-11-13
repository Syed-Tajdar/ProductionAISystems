"""The failed-login limit kept in Postgres, called through PostgREST functions."""

import json

import httpx
from fastapi.testclient import TestClient

from app import auth
from app.auth import DatabaseRateLimiter, RateLimiter, client_id, limiter_for
from app.config import Settings, get_settings
from app.db import Supabase, SupabaseError
from app.main import app


class FakeRPC:
    def __init__(self, wait=0, fail=False):
        self.wait, self.fail, self.calls = wait, fail, []

    def rpc(self, function, **args):
        self.calls.append((function, args))
        if self.fail:
            raise SupabaseError(503, "unavailable")
        return self.wait if function == "login_wait" else None


def test_database_limiter_calls_the_functions():
    db = FakeRPC(wait=42)
    rl = DatabaseRateLimiter(db, per_client=3, overall=9, window=60)
    assert rl.blocked("1.2.3.4") == 42
    rl.fail("1.2.3.4")
    rl.reset("1.2.3.4")
    assert db.calls == [
        ("login_wait", {"p_client": "1.2.3.4", "p_per_client": 3, "p_overall": 9, "p_window_seconds": 60}),
        ("login_fail", {"p_client": "1.2.3.4"}),
        ("login_reset", {"p_client": "1.2.3.4"}),
    ]


def test_database_outage_falls_back_to_the_memory_limit():
    fallback = RateLimiter(per_client=2, overall=50, window=60)
    rl = DatabaseRateLimiter(FakeRPC(fail=True), fallback=fallback)
    rl.fail("a")
    rl.fail("a")
    assert rl.blocked("a") > 0 and rl.blocked("b") == 0


def test_store_selection():
    plain = Settings(_env_file=None)
    assert limiter_for(plain) is auth.limiter
    configured = Settings(_env_file=None, supabase_url="https://p.supabase.co", supabase_service_key="sb_secret_x")
    assert isinstance(limiter_for(configured), DatabaseRateLimiter)
    assert limiter_for(configured.model_copy(update={"login_limit_store": "memory"})) is auth.limiter


def test_rpc_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=7)

    db = Supabase("https://p.supabase.co", "sb_secret_x", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert db.rpc("login_wait", p_client="c") == 7
    assert seen[0].method == "POST" and seen[0].url.path == "/rest/v1/rpc/login_wait"
    assert json.loads(seen[0].content) == {"p_client": "c"}


def test_client_address_from_the_proxy_header():
    class Req:
        headers = {"x-real-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.1, 10.0.0.1"}
        client = type("C", (), {"host": "10.0.0.2"})()

    assert client_id(Req()) == "10.0.0.2"
    assert client_id(Req(), Settings(_env_file=None)) == "10.0.0.2"
    assert client_id(Req(), Settings(_env_file=None, client_ip_header="x-real-ip")) == "203.0.113.9"
    assert client_id(Req(), Settings(_env_file=None, client_ip_header="x-forwarded-for")) == "198.51.100.1"


def test_login_uses_the_database_limit(monkeypatch):
    db = FakeRPC(wait=0)
    monkeypatch.setattr(auth, "limiter_for", lambda settings: DatabaseRateLimiter(db))
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="test", app_passcode="right-passcode")
    try:
        client = TestClient(app)
        assert client.post("/login", data={"passcode": "wrong"}, follow_redirects=False).status_code == 401
        assert client.post("/login", data={"passcode": "right-passcode"}, follow_redirects=False).status_code == 303
        db.wait = 120
        res = client.post("/login", data={"passcode": "right-passcode"}, follow_redirects=False)
        assert res.status_code == 429 and res.headers["retry-after"] == "120"
    finally:
        app.dependency_overrides.clear()
    assert [c[0] for c in db.calls] == ["login_wait", "login_fail", "login_wait", "login_reset", "login_wait"]
