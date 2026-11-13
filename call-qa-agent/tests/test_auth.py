import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import RateLimiter, make_session, passcode_ok, safe_next, session_ok
from app.config import Settings, get_settings
from app.llm import LLMError, LLMResult
from app.main import app, get_db

SETTINGS = Settings(_env_file=None, app_env="test", app_passcode="correct-horse")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_passcode_and_sessions():
    assert passcode_ok(SETTINGS, "correct-horse") and not passcode_ok(SETTINGS, "wrong") and not passcode_ok(SETTINGS, None)
    assert not passcode_ok(Settings(_env_file=None), "")  # no passcode configured: nothing matches
    token = make_session(SETTINGS, now=1000)
    assert session_ok(SETTINGS, token, now=1001)
    assert not session_ok(SETTINGS, token, now=1000 + 12 * 3600 + 1)  # expired
    expires, sig = token.split(".")
    assert not session_ok(SETTINGS, f"{int(expires) + 999}.{sig}", now=1001)  # tampered expiry
    assert not session_ok(Settings(_env_file=None, app_passcode="new-passcode"), token, now=1001)  # passcode rotated
    assert not session_ok(SETTINGS, "garbage", now=1001) and not session_ok(SETTINGS, None)


def test_rate_limiter_per_client_overall_and_window():
    clock = Clock()
    rl = RateLimiter(per_client=3, overall=5, window=60, clock=clock)
    for _ in range(3):
        rl.fail("a")
    assert rl.blocked("a") == 61 and rl.blocked("b") == 0
    rl.fail("b")
    rl.fail("c")
    assert rl.blocked("d") > 0  # overall cap reached, even for a new client
    clock.t += 61
    assert rl.blocked("a") == 0 and rl.blocked("d") == 0
    rl.fail("a")
    rl.reset("a")
    assert rl.clients.get("a") is None


@pytest.mark.parametrize("target, expected", [
    ("/dashboard/calls/c1", "/dashboard/calls/c1"), ("https://evil.example", "/dashboard"),
    ("//evil.example", "/dashboard"), ("/\\evil.example", "/dashboard"), (None, "/dashboard")])
def test_safe_next(target, expected):
    assert safe_next(target) == expected


class FakeDB:
    calls = [
        {"id": "demo1", "is_demo": True, "filename": "demo_call_01.wav", "status": "done", "error": None, "duration_sec": 85,
         "language": "en", "created_at": "2026-11-06T10:00:00+00:00", "transcripts": {"utterances": []}, "scores": []},
        {"id": "real1", "is_demo": False, "filename": "customer_call.wav", "status": "done", "error": None, "duration_sec": 60,
         "language": "en", "created_at": "2026-11-06T11:00:00+00:00", "transcripts": {"utterances": []}, "scores": []},
    ]

    def list_calls(self, limit, offset, demo_only=False):
        return [c for c in self.calls if c["is_demo"] or not demo_only]

    def get_call(self, call_id):
        return next((c for c in self.calls if c["id"] == call_id), None)


@pytest.fixture
def client():
    def make(**settings):
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **{"app_env": "test", **settings})
        app.dependency_overrides[get_db] = lambda: FakeDB()
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()


def test_api_requires_the_passcode(client):
    c = client(app_passcode="correct-horse")
    assert c.get("/calls/real1").status_code == 401
    assert c.post("/calls/real1/score", json={"rubric": "us_insurance"}).status_code == 401
    assert c.post("/calls", data={"url": "https://example.com/a.wav"}).status_code == 401
    assert c.get("/calls/real1", headers={"X-Passcode": "wrong"}).status_code == 401
    assert c.get("/calls/real1", headers={"X-Passcode": "correct-horse"}).status_code == 200
    assert c.get("/health").status_code == 200  # stays public


def test_routes_stay_closed_without_a_configured_passcode(client):
    c = client()
    assert c.get("/calls/real1", headers={"X-Passcode": ""}).status_code == 503
    page = c.get("/dashboard", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/dashboard"
    assert "No passcode is configured" in c.get("/login").text


def test_login_flow_sets_a_safe_cookie(client):
    c = client(app_passcode="correct-horse")
    page = c.get("/dashboard/calls/real1", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/dashboard/calls/real1"
    assert 'type="password"' in c.get("/login?next=/dashboard/calls/real1").text

    wrong = c.post("/login", data={"passcode": "nope", "next": "/dashboard"})
    assert wrong.status_code == 401 and "Wrong passcode" in wrong.text and auth.COOKIE not in wrong.cookies

    ok = c.post("/login", data={"passcode": "correct-horse", "next": "/dashboard/calls/real1"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/dashboard/calls/real1"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=43200" in cookie and "secure" not in cookie
    assert c.get("/dashboard/calls/real1").status_code == 200  # cookie now set on the client
    assert c.get("/calls/real1").status_code == 200  # the session also works for the API
    assert "Log out" in c.get("/dashboard").text

    out = c.post("/logout", follow_redirects=False)
    assert out.status_code == 303 and 'callqa_session=""' in out.headers["set-cookie"]


def test_cookie_is_secure_in_production(client):
    c = client(app_passcode="correct-horse", app_env="production")
    ok = c.post("/login", data={"passcode": "correct-horse"}, follow_redirects=False)
    assert "secure" in ok.headers["set-cookie"].lower()


def test_failed_logins_are_rate_limited(client):
    c = client(app_passcode="correct-horse")
    for _ in range(5):
        assert c.post("/login", data={"passcode": "guess"}).status_code == 401
    blocked = c.post("/login", data={"passcode": "correct-horse"})  # even the right passcode waits
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert c.get("/calls/real1", headers={"X-Passcode": "correct-horse"}).status_code == 429
    assert '<div class="du-state du-limit" role="status">' in blocked.text and "Please try again in" in blocked.text
    page = c.get("/dashboard", headers={"X-Passcode": "correct-horse"})  # a page gets a friendly limit page
    assert page.status_code == 429 and '<p class="du-state-title">Too many requests</p>' in page.text
    assert page.headers["retry-after"] == blocked.headers["retry-after"]


def test_demo_mode_is_read_only_and_limited_to_demo_calls(client):
    c = client(app_passcode="correct-horse", demo_mode=True)
    page = c.get("/dashboard")
    assert page.status_code == 200 and "/dashboard/calls/demo1" in page.text and "/dashboard/calls/real1" not in page.text
    assert "Demo: sample calls with synthetic data, read-only." in page.text and "Owner login" in page.text
    assert c.get("/dashboard/calls/demo1").status_code == 200
    assert c.get("/dashboard/calls/real1").status_code == 404  # real calls stay hidden
    assert c.post("/calls/demo1/score", json={"rubric": "us_insurance"}).status_code == 401  # still read-only
    owner = c.get("/dashboard", headers={"X-Passcode": "correct-horse"})
    assert "/dashboard/calls/real1" in owner.text and "Demo:" not in owner.text


def test_security_headers(client):
    res = client(app_passcode="correct-horse").get("/health")
    assert res.headers["x-frame-options"] == "DENY" and res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "content-security-policy" not in res.headers  # JSON, not a page


def test_every_page_has_the_content_security_policy(client):
    c = client(app_passcode="correct-horse", supabase_url="https://abcd.supabase.co/")
    expected = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "media-src 'self' https://abcd.supabase.co; connect-src 'self' https://abcd.supabase.co; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
    for path in ("/login", "/dashboard", "/upload", "/dashboard/calls/missing"):
        res = c.get(path)
        assert res.headers["content-type"].startswith("text/html"), path
        assert res.headers["content-security-policy"] == expected, path
        assert res.headers["x-content-type-options"] == "nosniff", path
        assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin", path
    # Without a Supabase URL the media and connect sources are this site only.
    plain = client(app_passcode="correct-horse").get("/login").headers["content-security-policy"]
    assert "media-src 'self'; connect-src 'self';" in plain


def test_page_templates_have_no_inline_code():
    import re
    from pathlib import Path
    for path in (Path(__file__).resolve().parents[1] / "app" / "templates").glob("*.html"):
        html = path.read_text(encoding="utf-8")
        assert not re.search(r"\sstyle=|<style|\son[a-z]+=", html), path.name
        assert all("src=" in tag for tag in re.findall(r"<script[^>]*>", html)), path.name


def test_api_docs_pin_their_inline_code(client):
    import base64
    import hashlib
    import re
    c = client(app_passcode="correct-horse")
    res = c.get("/docs")
    csp = res.headers["content-security-policy"]
    script_src = csp.split("script-src ")[1].split(";")[0]
    assert res.status_code == 200 and script_src.startswith("'self' https://cdn.jsdelivr.net")
    for code in re.findall(r"<script>(.*?)</script>", res.text, re.S):
        assert "'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'" in script_src
    assert "unsafe-inline" not in csp  # scripts and styles are both pinned


def test_no_page_allows_inline_code(client):
    c = client(app_passcode="correct-horse", demo_mode=True)
    assert c.get("/redoc").status_code == 404  # ReDoc needs inline styles, so it is not served
    for path in ("/dashboard", "/dashboard/calls/demo1", "/login", "/docs"):
        assert "unsafe-inline" not in c.get(path).headers["content-security-policy"], path


def test_session_secret_changes_the_signing_key():
    plain = Settings(_env_file=None, app_passcode="correct-horse")
    secret = Settings(_env_file=None, app_passcode="correct-horse", session_secret="k3-long-random-value")
    other = Settings(_env_file=None, app_passcode="correct-horse", session_secret="another-random-value")
    token = make_session(secret)
    assert session_ok(secret, token)
    assert not session_ok(plain, token) and not session_ok(other, token)   # needs the same secret
    assert not session_ok(secret, make_session(plain))                     # a passcode-only cookie is not accepted
    assert session_ok(plain, make_session(plain))                          # without the secret it works as before
    changed = Settings(_env_file=None, app_passcode="new-passcode", session_secret="k3-long-random-value")
    assert not session_ok(changed, token)                                  # a new passcode still signs everyone out


def test_login_works_with_a_session_secret(client):
    c = client(app_passcode="correct-horse", session_secret="k3-long-random-value")
    ok = c.post("/login", data={"passcode": "correct-horse"}, follow_redirects=False)
    assert ok.status_code == 303 and c.get("/calls/real1").status_code == 200


# --- demo seeding -------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402


class SeedDB:
    def __init__(self):
        self.calls, self.n, self.rubrics = {}, 0, {}

    def insert_call(self, **f):
        self.n += 1
        self.calls[f"c{self.n}"] = {"id": f"c{self.n}", "error": None, "transcripts": None, **f}
        return self.calls[f"c{self.n}"]

    def update_call(self, cid, **f):
        self.calls[cid].update(f)

    def save_transcript(self, cid, utterances, provider="deepgram"):
        self.calls[cid]["transcripts"] = {"utterances": utterances}

    def get_call(self, cid):
        return self.calls.get(cid)

    def delete_call(self, cid):
        self.calls.pop(cid)

    def get_rubric(self, slug, version):
        return self.rubrics.get(slug)

    def insert_rubric(self, **f):
        self.rubrics[f["slug"]] = {"id": f"r-{f['slug']}", **f}
        return self.rubrics[f["slug"]]

    def insert_score(self, **f):
        return {"id": "s", **f}

    def list_calls(self, limit, offset, demo_only=False):
        return [c for c in self.calls.values() if c.get("is_demo") or not demo_only]


def test_seed_marks_demo_calls_and_removes_failures():
    def llm(settings, system, user, schema):
        if "Brightwave" in user:  # support calls: the provider is down
            raise LLMError("provider returned HTTP 503: high demand")
        return LLMResult({"items": []}, "fake", "m", 1, 1, 1, None)

    db = SeedDB()
    result = seed_demo.seed(db, Settings(_env_file=None), ["call_01", "call_02", "call_08", "call_09"], "text",
                            llm=llm, log=lambda *_: None)
    assert result == {"seeded": ["call_01", "call_02"], "failed": ["call_08"], "skipped": []}  # stopped at the outage
    assert all(c["is_demo"] and c["filename"].startswith("demo_call_0") for c in db.calls.values())
    assert len(db.calls) == 2  # the failed call was removed
    assert seed_demo.remove(db, log=lambda *_: None) == 2 and db.calls == {}


def test_seed_can_skip_samples_that_are_already_there():
    def llm(settings, system, user, schema):
        return LLMResult({"items": []}, "fake", "m", 1, 1, 1, None)

    db = SeedDB()
    first = seed_demo.seed(db, Settings(_env_file=None), ["call_01", "call_02"], "text", llm=llm, log=lambda *_: None)
    again = seed_demo.seed(db, Settings(_env_file=None), ["call_01", "call_02", "call_03"], "text", llm=llm,
                           log=lambda *_: None, skip_existing=True)
    assert first["seeded"] == ["call_01", "call_02"]
    assert again == {"seeded": ["call_03"], "failed": [], "skipped": ["call_01", "call_02"]}
    assert len(db.calls) == 3  # no duplicates
    titles = [c["title"] for c in db.calls.values()]
    assert titles[2] == "US insurance sales: pushy pitch, guaranteed approval"
    assert all(c["summary"] for c in db.calls.values())


def test_seed_backfills_titles_of_older_demo_calls():
    db = SeedDB()
    old = db.insert_call(filename="demo_call_04_us_insurance_dnc_ignored.wav", is_demo=True)
    seed_demo.seed(db, Settings(_env_file=None), ["call_04"], "text", log=lambda *_: None, skip_existing=True)
    assert len(db.calls) == 1 and old["title"] == "US insurance sales: DNC request ignored"
    assert old["summary"].startswith("Compliant opening")


class AudioFakeDB(FakeDB):
    calls = [{**FakeDB.calls[0], "storage_path": "demo/call_01.wav"}, {**FakeDB.calls[1], "storage_path": "abc/real.wav"},
             {**FakeDB.calls[0], "id": "demo2"}]
    signed = []

    def signed_download_url(self, bucket, path, seconds):
        self.signed.append((bucket, path, seconds))
        return f"https://storage.example/sign/{bucket}/{path}?token=t&expires={seconds}"


def test_demo_visitors_can_read_demo_calls_as_json_and_audio(client):
    c = client(app_passcode="correct-horse", demo_mode=True, audio_url_seconds=120)
    app.dependency_overrides[get_db] = lambda: AudioFakeDB()
    assert c.get("/calls/demo1").json()["id"] == "demo1"
    assert c.get("/calls/real1").status_code == 404  # real calls stay hidden, even as JSON
    audio = c.get("/calls/demo1/audio", follow_redirects=False)
    assert audio.status_code == 307 and audio.headers["cache-control"] == "no-store"
    assert audio.headers["location"] == "https://storage.example/sign/call-audio/demo/call_01.wav?token=t&expires=120"
    assert AudioFakeDB.signed[-1] == ("call-audio", "demo/call_01.wav", 120)
    assert c.get("/calls/real1/audio", follow_redirects=False).status_code == 404
    assert c.get("/calls/demo2/audio", follow_redirects=False).json() == {"detail": "this call has no stored audio"}
    owner = c.get("/calls/real1/audio", headers={"X-Passcode": "correct-horse"}, follow_redirects=False)
    assert owner.status_code == 307 and "abc/real.wav" in owner.headers["location"]


def test_call_json_and_audio_need_the_passcode_outside_demo_mode(client):
    c = client(app_passcode="correct-horse")
    app.dependency_overrides[get_db] = lambda: AudioFakeDB()
    assert c.get("/calls/demo1").status_code == 401 and c.get("/calls/demo1/audio").status_code == 401


def test_public_reads_are_rate_limited_per_client(client):
    c = client(app_passcode="correct-horse", demo_mode=True, public_reads_per_minute=3)
    app.dependency_overrides[get_db] = lambda: AudioFakeDB()
    assert [c.get(p, follow_redirects=False).status_code for p in ("/calls/demo1", "/dashboard", "/calls/demo1/audio")] == [200, 200, 307]
    limited = c.get("/calls/demo1")
    assert limited.status_code == 429 and 0 < int(limited.headers["retry-after"]) <= 61
    page = c.get("/dashboard")
    assert page.status_code == 429 and "Too many requests" in page.text
    assert c.get("/dashboard/calls/demo1").status_code == 429 and c.get("/calls/demo1/audio").status_code == 429
    # The owner is not counted, and other pages stay open.
    assert c.get("/calls/demo1", headers={"X-Passcode": "correct-horse"}).status_code == 200
    assert c.get("/health").status_code == 200 and c.get("/login").status_code == 200


def test_public_read_limit_counts_each_client_address_and_can_be_turned_off(client):
    c = client(app_passcode="correct-horse", demo_mode=True, public_reads_per_minute=1, client_ip_header="x-real-ip")
    assert c.get("/calls/demo1", headers={"x-real-ip": "203.0.113.5"}).status_code == 200
    assert c.get("/calls/demo1", headers={"x-real-ip": "203.0.113.5"}).status_code == 429
    assert c.get("/calls/demo1", headers={"x-real-ip": "203.0.113.6"}).status_code == 200
    off = client(app_passcode="correct-horse", demo_mode=True, public_reads_per_minute=0)
    assert all(off.get("/calls/demo1").status_code == 200 for _ in range(5))


class FailedCallDB(FakeDB):
    calls = [{**FakeDB.calls[0], "status": "failed",
              "error": "Deepgram returned HTTP 400: could not fetch https://project.supabase.co/storage/v1/object/sign/x?token=abc"}]


def test_demo_visitors_get_a_generic_failure_text(client):
    from app.pipeline import FAILED_TEXT
    c = client(app_passcode="correct-horse", demo_mode=True)
    app.dependency_overrides[get_db] = lambda: FailedCallDB()
    assert c.get("/calls/demo1").json()["error"] == FAILED_TEXT
    page = c.get("/dashboard/calls/demo1").text
    assert FAILED_TEXT in page and "supabase.co" not in page and "token=abc" not in page
    owner = {"X-Passcode": "correct-horse"}
    assert "token=abc" in c.get("/calls/demo1", headers=owner).json()["error"]
    assert "Deepgram returned HTTP 400" in c.get("/dashboard/calls/demo1", headers=owner).text


def test_a_call_id_that_is_not_a_uuid_is_a_404(client):
    import httpx

    from app.db import Supabase
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(400, json={"message": "invalid input syntax for type uuid"})

    c = client(app_passcode="correct-horse", demo_mode=True)
    app.dependency_overrides[get_db] = lambda: Supabase("https://project.supabase.co", "sb_secret_example",
                                                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    owner = {"X-Passcode": "correct-horse"}
    for path in ("/calls/abc", "/calls/abc/audio", "/calls/1%20or%201=1"):
        assert c.get(path, headers=owner).status_code == 404, path
    assert c.post("/calls/abc/score", json={"rubric": "us_insurance"}, headers=owner).status_code == 404
    page = c.get("/dashboard/calls/abc")
    assert page.status_code == 404 and "Call not found" in page.text
    assert seen == []  # nothing reached the database
