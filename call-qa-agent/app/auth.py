"""One passcode for uploads, scoring and the dashboard: login page, signed session cookie,
X-Passcode header for API clients, and a rate limit on failed attempts."""

import hashlib
import hmac
import logging
import time
from collections import deque
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import Settings, get_settings
from app.db import Supabase, SupabaseError
from app.ui import templates

log = logging.getLogger(__name__)
COOKIE = "callqa_session"
router = APIRouter()


class RateLimiter:
    """Failed attempts per client and overall within a sliding window. The overall cap still
    holds when a client rotates addresses. In memory: one limiter per running instance."""

    def __init__(self, per_client: int = 5, overall: int = 50, window: int = 900, clock=time.monotonic):
        self.per_client, self.overall, self.window, self.clock = per_client, overall, window, clock
        self.clients: dict[str, deque] = {}
        self.all: deque = deque()

    def _trim(self, q: deque) -> deque:
        while q and q[0] <= self.clock() - self.window:
            q.popleft()
        return q

    def blocked(self, client: str) -> int:
        """Seconds until the client may try again, 0 if allowed."""
        waits = [int(q[0] + self.window - self.clock()) + 1
                 for q, limit in ((self._trim(self.clients.get(client, deque())), self.per_client),
                                  (self._trim(self.all), self.overall)) if len(q) >= limit]
        return max(waits, default=0)

    def fail(self, client: str) -> None:
        self.clients.setdefault(client, deque()).append(self.clock())
        self.all.append(self.clock())

    def reset(self, client: str) -> None:
        self.clients.pop(client, None)

    def take(self, client: str) -> int:
        """Counts one request. Returns the seconds to wait when the client is over the limit, else 0."""
        if wait := self.blocked(client):
            return wait
        if len(self.clients) > 5000:  # forget clients whose window has passed
            self.clients = {c: q for c, q in self.clients.items() if self._trim(q)}
        self.fail(client)
        return 0


limiter = RateLimiter()
# Reads without the passcode. Per instance: enough to stop one client hammering the database through the demo.
public_reads = RateLimiter(window=60)


class DatabaseRateLimiter:
    """The same limits kept in Postgres (login_failures and the login_* functions), so they hold
    across serverless instances. If the database cannot be reached, this instance's in-memory
    limiter takes over rather than letting attempts through unlimited."""

    def __init__(self, db: Supabase, per_client: int = 5, overall: int = 50, window: int = 900, fallback: RateLimiter | None = None):
        self.db, self.per_client, self.overall, self.window = db, per_client, overall, window
        self.fallback = fallback or limiter

    def blocked(self, client: str) -> int:
        try:
            return int(self.db.rpc("login_wait", p_client=client, p_per_client=self.per_client,
                                   p_overall=self.overall, p_window_seconds=self.window) or 0)
        except SupabaseError:
            log.warning("login limit: database unavailable, using the in-memory limit")
            return self.fallback.blocked(client)

    def fail(self, client: str) -> None:
        try:
            self.db.rpc("login_fail", p_client=client)
        except SupabaseError:
            self.fallback.fail(client)

    def reset(self, client: str) -> None:
        try:
            self.db.rpc("login_reset", p_client=client)
        except SupabaseError:
            self.fallback.reset(client)


def limiter_for(settings: Settings):
    store = settings.login_limit_store
    configured = bool(settings.supabase_url and settings.supabase_service_key.get_secret_value())
    if store == "database" or (store == "auto" and configured):
        return DatabaseRateLimiter(Supabase(settings.supabase_url, settings.supabase_service_key.get_secret_value()))
    return limiter


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def passcode_set(settings: Settings) -> bool:
    return bool(settings.app_passcode.get_secret_value())


def passcode_ok(settings: Settings, given: str | None) -> bool:
    """Constant-time; comparing digests also hides the passcode length."""
    return passcode_set(settings) and hmac.compare_digest(_digest(given or ""), _digest(settings.app_passcode.get_secret_value()))


def _signing_key(settings: Settings) -> bytes:
    """Changing the passcode signs everyone out either way. With SESSION_SECRET the key also depends on a random
    value the cookie never reveals, so a captured cookie cannot be used to test passcode guesses."""
    passcode = _digest(settings.app_passcode.get_secret_value())
    secret = settings.session_secret.get_secret_value()
    if secret:
        return hmac.new(_digest(secret), b"callqa-session-v2" + passcode, hashlib.sha256).digest()
    return hmac.new(passcode, b"callqa-session-v1", hashlib.sha256).digest()


def make_session(settings: Settings, now: float | None = None) -> str:
    expires = int((now or time.time()) + settings.session_hours * 3600)
    sig = hmac.new(_signing_key(settings), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def session_ok(settings: Settings, token: str | None, now: float | None = None) -> bool:
    if not token or not passcode_set(settings) or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    if not expires.isdigit():
        return False
    expected = hmac.new(_signing_key(settings), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected) and int(expires) > (now or time.time())


def client_id(request: Request, settings: Settings | None = None) -> str:
    """The client address: from the proxy's header when CLIENT_IP_HEADER is set, else the socket."""
    header = settings.client_ip_header if settings else ""
    if header and (value := request.headers.get(header)):
        return value.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


def is_owner(request: Request, settings: Settings) -> bool:
    """Valid session cookie, or a valid X-Passcode header (wrong headers count as failures)."""
    if session_ok(settings, request.cookies.get(COOKIE)):
        return True
    header = request.headers.get("x-passcode")
    if header is None:
        return False
    client, attempts = client_id(request, settings), limiter_for(settings)
    if wait := attempts.blocked(client):
        raise HTTPException(429, "too many failed attempts, try again later", headers={"Retry-After": str(wait)})
    if passcode_ok(settings, header):
        attempts.reset(client)
        return True
    attempts.fail(client)
    return False


def require_owner(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """For API routes: 401 without a valid session or X-Passcode, 503 when no passcode is configured."""
    if not passcode_set(settings):
        raise HTTPException(503, "APP_PASSCODE is not set")
    if not is_owner(request, settings):
        raise HTTPException(401, "passcode required: log in at /login or send an X-Passcode header")


def count_public_read(request: Request, settings: Settings) -> None:
    """429 when this client address has read more than PUBLIC_READS_PER_MINUTE times in the last minute."""
    limit = settings.public_reads_per_minute
    if limit <= 0:
        return
    public_reads.per_client, public_reads.overall = limit, limit * 20
    if wait := public_reads.take(client_id(request, settings)):
        raise HTTPException(429, "too many requests, try again shortly", headers={"Retry-After": str(wait)})


def call_reader(request: Request, settings: Settings = Depends(get_settings)) -> str:
    """For read-only call routes (JSON, audio): "owner", or "demo" in demo mode, which may only read demo calls."""
    if passcode_set(settings) and is_owner(request, settings):
        return "owner"
    if settings.demo_mode:
        count_public_read(request, settings)
        return "demo"
    if not passcode_set(settings):
        raise HTTPException(503, "APP_PASSCODE is not set")
    raise HTTPException(401, "passcode required: log in at /login or send an X-Passcode header")


def page_viewer(request: Request, settings: Settings = Depends(get_settings)) -> str:
    """For dashboard pages: "owner", "demo" (read-only demo calls) or a redirect to the login page."""
    if passcode_set(settings) and is_owner(request, settings):
        return "owner"
    if settings.demo_mode:
        count_public_read(request, settings)
        return "demo"
    raise HTTPException(303, headers={"Location": f"/login?next={quote(request.url.path)}"})


def safe_next(target: str | None) -> str:
    """Only local paths, so the login form cannot redirect elsewhere."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/dashboard"
    return target


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/dashboard", settings: Settings = Depends(get_settings)):
    if session_ok(settings, request.cookies.get(COOKIE)):
        return RedirectResponse(safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": safe_next(next), "error": None, "limited": 0,
                                                              "configured": passcode_set(settings),
                                                              "demo": settings.demo_mode})


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login(request: Request, passcode: str = Form(""), next: str = Form("/dashboard"),
          settings: Settings = Depends(get_settings)):
    target, client, attempts = safe_next(next), client_id(request, settings), limiter_for(settings)
    context = {"next": target, "configured": passcode_set(settings), "demo": settings.demo_mode}
    if wait := attempts.blocked(client):
        return templates.TemplateResponse(request, "login.html", {**context, "limited": wait},
                                          status_code=429, headers={"Retry-After": str(wait)})
    if not passcode_ok(settings, passcode):
        attempts.fail(client)
        return templates.TemplateResponse(request, "login.html", {**context, "error": "Wrong passcode."}, status_code=401)
    attempts.reset(client)
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(COOKIE, make_session(settings), max_age=settings.session_hours * 3600, httponly=True,
                        samesite="lax", secure=settings.app_env == "production", path="/")
    return response


@router.post("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response
