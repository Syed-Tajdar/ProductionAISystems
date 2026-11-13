"""HTTP app: the question page at /, the JSON API (/search, /ask, /health), and optionally the MCP
server over streamable HTTP at /mcp (rag.mcp_server).

Public demo: anyone can ask, within a per-address rate limit and a daily cap (rag.demo), kept in
Postgres so they hold across serverless instances. Repeated questions are answered from a cache
without counting against either. The public MCP endpoint shares the same limits and cache.
"""

import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from rag import __version__
from rag.answer import Answer
from rag.config import Settings, get_settings
from rag.demo import EXAMPLES, AnswerCache, DemoLimiter, LimitsUnavailable, PgAnswerCache, PgLimiter, client_address, message
from rag.mcp_server import BearerAuth, ClientAddress, DemoLimits, create_server
from rag.service import RagService, ServiceError, unavailable

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def width_class(value: float, total: float) -> str:
    """A CSS width class in 5% steps (w-0 ... w-100): the page's CSP allows no inline styles."""
    share = value / total if total else 0
    return f"w-{min(100, max(0, round(share * 20) * 5))}"


templates.env.filters["width_class"] = width_class
# Shared demo UI (demo-ui/ at the repository root): footer, "What this demo shows" card, empty and error states.
templates.env.globals["ui"] = templates.env.get_template("demo_ui.html").module
templates.env.globals["profile"] = json.loads((HERE / "profile.json").read_text(encoding="utf-8"))
templates.env.globals["demo_points"] = [
    "Answers come only from the documents, and every statement cites the passage it rests on.",
    "Each quote is checked against its passage; a statement whose quote is not there is dropped.",
    "Ask in English or Arabic: search covers both languages, and the answer comes in yours.",
]

# The document set, for the header chips: title and the languages it exists in.
DOCUMENTS = [
    ("Tenant handbook", "EN · AR"),
    ("Customer service FAQ", "EN · AR"),
    ("Leave policy", "EN · AR"),
    ("Amenities guide", "EN"),
    ("Travel and expenses policy", "AR"),
]
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}
PAGE_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    language: Literal["en", "ar"] | None = Field(default=None, description="Answer language; detected when empty")


class Limited(Exception):
    def __init__(self, verdict):
        self.verdict = verdict


LANGUAGE_NAMES = {"en": "English", "ar": "Arabic"}


def ago(when: datetime, now: datetime | None = None) -> str:
    """"just now", "5 minutes ago", "3 hours ago", "2 days ago"."""
    seconds = max(0, int(((now or datetime.now(UTC)) - when).total_seconds()))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return "just now"


TIMING_STEPS = [("embed", "Embed the question"), ("translate", "Translate for keyword search"), ("search", "Hybrid search"),
                ("answer", "Write the cited answer"), ("quote_check", "Check every quote")]


def timeline_view(trace: dict) -> list[dict]:
    """Steps as bars on one time axis: start offset and length as shares of the whole run, and steps that overlap
    another (the question's embedding and translation) marked as running in parallel. Traces cached before the
    timeline existed get one bar per step, relative to the slowest."""
    labels = dict(TIMING_STEPS)
    line = [s for s in trace.get("timeline", []) if s["step"] in labels]
    if not line:
        timings = trace.get("timings_ms", {})
        slowest = max([timings[k] for k in labels if k in timings] or [1]) or 1
        return [{"label": label, "ms": timings[key], "offset": "o-0", "width": width_class(timings[key], slowest), "parallel": False}
                for key, label in TIMING_STEPS if key in timings]
    end = max(s["start"] + s["ms"] for s in line) or 1
    overlaps = lambda a, b: a is not b and a["start"] < b["start"] + b["ms"] and b["start"] < a["start"] + a["ms"]  # noqa: E731
    return [{"label": labels[s["step"]], "ms": s["ms"], "offset": width_class(s["start"], end).replace("w-", "o-"),
             "width": width_class(s["ms"], end), "parallel": any(overlaps(s, o) for o in line)} for s in line]


def trace_view(trace: dict, sources: list[dict]) -> dict:
    """The trace as the "How this answer was built" panel shows it: passages marked with the [n] they are cited as,
    the quote checks with readable source names, and the steps on a timeline."""
    chosen = trace.get("chosen", [])
    shown = {s.get("chunk_id"): s["n"] for s in sources}
    by_source = {c["source"]: c for c in chosen}
    citations = (trace.get("answer") or {}).get("checks", [])
    checks = group_checks(citations, by_source)
    timings = trace.get("timings_ms", {})
    return {**trace, "chosen": [{**c, "cited_as": shown.get(c["chunk_id"])} for c in chosen], "checks": checks,
            "checks_ok": sum(c["ok"] for c in checks), "citations": len(citations), "steps": timeline_view(trace),
            "total_ms": timings.get("total")}


def group_checks(citations: list[dict], by_source: dict) -> list[dict]:
    """One row per distinct quote check: the same quote from the same source, cited by several statements, is shown
    once with how many statements use it."""
    groups: dict[tuple, dict] = {}
    for c in citations:
        key = (c["source"], c["quote"], c["ok"], c["reason"])
        if key not in groups:
            groups[key] = {**c, "passage": by_source.get(c["source"]), "statements": []}
        if c["statement"] not in groups[key]["statements"]:
            groups[key]["statements"].append(c["statement"])
    return [{**g, "used_by": len(g["statements"])} for g in groups.values()]


def snippet(source: dict, limit: int = 140) -> str:
    """A short line for a source card: the first quote the answer relies on, else the passage start."""
    text = " ".join(((source.get("quotes") or [""])[0] or source.get("passage", "")).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def highlight(passage: str, quotes: list[str]) -> list[tuple[str, bool]]:
    """Split a passage into (text, is_quote) parts, marking each quote where it appears verbatim."""
    spans = []
    for q in quotes:
        i = passage.find(q)
        if q and i != -1 and not any(i < e and s < i + len(q) for s, e in spans):
            spans.append((i, i + len(q)))
    parts, pos = [], 0
    for s, e in sorted(spans):
        if s > pos:
            parts.append((passage[pos:s], False))
        parts.append((passage[s:e], True))
        pos = e
    if pos < len(passage):
        parts.append((passage[pos:], False))
    return parts


def create_app(settings: Settings | None = None, *, service: RagService | None = None,
               limiter=None, cache=None) -> FastAPI:
    settings = settings or get_settings()
    service = service or RagService(settings)
    shared = settings.demo_store == "postgres" or (settings.demo_store == "auto" and bool(settings.database_url.get_secret_value()))
    if limiter is None:
        args = (settings.demo_questions_per_client, settings.demo_window_seconds, settings.demo_daily_cap)
        limiter = PgLimiter(service.connection, *args) if shared else DemoLimiter(*args)
    if cache is None:
        cache = (PgAnswerCache(service.connection, settings.answer_cache_size, settings.answer_cache_seconds,
                               decode=lambda data: Answer(**data)) if shared
                 else AnswerCache(settings.answer_cache_size, settings.answer_cache_seconds))

    # MCP at /mcp: private with a bearer token, or a public read-only demo under the same limits.
    mcp_app = mcp_asgi = public_mcp = None
    token = settings.mcp_bearer_token.get_secret_value()
    if token or settings.mcp_public:
        if token and len(token) < 24:
            raise ValueError("MCP_BEARER_TOKEN must be at least 24 characters")
        server = create_server(service, limits=None if token else DemoLimits(limiter, cache))
        # Stateless with JSON responses: any instance can answer any request. host 0.0.0.0: no localhost-only
        # Host check, since the public hostname is not known here.
        mcp_app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
        mcp_asgi = ClientAddress(BearerAuth(mcp_app, token) if token else mcp_app, settings.client_ip_header)
        public_mcp = None if token else server
    tools_cache: list[dict] = []

    async def mcp_panel(request: Request) -> dict | None:
        """The "Use with MCP" panel: only for the public endpoint, with the tools read from the server itself."""
        if public_mcp is None:
            return None
        if not tools_cache:
            for t in await public_mcp.list_tools():
                schema = t.input_schema or {}
                params = [{"name": name, "required": name in schema.get("required", []), "description": spec.get("description", "")}
                          for name, spec in schema.get("properties", {}).items()]
                tools_cache.append({"name": t.name, "title": t.title or t.name,
                                    "summary": (t.description or "").strip().split("\n")[0], "params": params})
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        url = f"{scheme}://{request.headers.get('host', request.url.netloc)}/mcp"
        config = json.dumps({"mcpServers": {public_mcp.name: {"type": "http", "url": url}}}, indent=2)
        return {"url": url, "name": public_mcp.name, "config": config, "tools": tools_cache}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            if mcp_app is not None:  # starts the MCP session manager
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield
        await service.close()

    app = FastAPI(title="Document Q&A", version=__version__, lifespan=lifespan)
    app.state.limiter, app.state.cache = limiter, cache
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.exception_handler(Limited)
    async def limited(request: Request, exc: Limited):
        return JSONResponse({"detail": message(exc.verdict), "limit": exc.verdict.reason}, status_code=429,
                            headers={"Retry-After": str(exc.verdict.retry_after)})

    def client(request: Request) -> str:
        return client_address(dict(request.headers), request.client.host if request.client else None,
                              settings.client_ip_header)

    async def charge(request: Request) -> None:
        """Checks the limits and counts the question in one step."""
        try:
            verdict = await limiter.take(client(request))
        except LimitsUnavailable as e:
            raise ServiceError(str(e)) from e
        if not verdict.allowed:
            raise Limited(verdict)

    async def answer(request: Request, question: str, language: str | None = None):
        """(answer, from_cache): a cached answer, or a new one that counts against the limits. New answers record how
        they were built (the trace), which is cached with them."""
        cached = await cache.lookup(f"{language or ''}|{question}")
        if cached is not None:
            return cached, True
        await charge(request)
        result = await service.ask(question, language=language, trace={})
        await cache.store(f"{language or ''}|{question}", result)
        return result, False

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__, "questions_left_today": await limiter.left_today(),
                "mcp": "private" if token else "public" if settings.mcp_public else "off",
                "configured": {"database": bool(settings.database_url.get_secret_value()) or service.pool is not None,
                               "embeddings": bool(settings.gemini_api_key.get_secret_value()) or service.embedder is not None,
                               "answers": bool(settings.anthropic_api_key.get_secret_value()) or service.llm is not None}}

    @app.post("/search")
    async def search_endpoint(body: SearchRequest, request: Request) -> dict:
        try:
            await charge(request)
            hits = await service.search(body.query, top_k=body.top_k)
        except ServiceError as e:
            raise HTTPException(503, unavailable("POST /search", e)) from e
        return {"query": body.query, "results": [h.to_dict() for h in hits]}

    @app.post("/ask")
    async def ask_endpoint(body: AskRequest, request: Request,
                           trace: bool = Query(False, description="Also return how the answer was built: the query as "
                                               "searched, candidates, chosen passages with fused scores, quote checks and "
                                               "timings")) -> dict:
        try:
            a, from_cache = await answer(request, body.question, body.language)
        except ServiceError as e:
            raise HTTPException(503, unavailable("POST /ask", e)) from e
        data = a.to_dict(trace=trace)
        if trace and data.get("trace") is not None:
            data["trace"] = {**data["trace"], "from_cache": from_cache}
        return data

    async def shell(request: Request) -> dict:
        """What the shared layout (base.html) needs on every page."""
        return {"portfolio_url": settings.portfolio_url, "per_client": settings.demo_questions_per_client,
                "window_minutes": settings.demo_window_seconds // 60, "daily_cap": settings.demo_daily_cap,
                "left_today": await limiter.left_today()}

    async def page(request: Request, *, question: str = "", result=None, error: str = "", error_kind: str = "",
                   from_cache: bool = False, status: int = 200) -> HTMLResponse:
        sources = [{**s, "parts": highlight(s.get("passage", ""), s["quotes"]), "snippet": snippet(s)}
                   for s in (result.sources if result else [])]
        built = trace_view(result.trace, result.sources) if result is not None and result.trace else None
        response = templates.TemplateResponse(request, "index.html", {
            **await shell(request), "question": question, "result": result, "sources": sources, "error": error,
            "error_kind": error_kind, "examples": EXAMPLES, "documents": DOCUMENTS, "trace": built,
            "from_cache": from_cache, "mcp": await mcp_panel(request)}, status_code=status)
        response.headers["Content-Security-Policy"] = PAGE_CSP
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return await page(request)

    @app.post("/", response_class=HTMLResponse)
    async def ask_page(request: Request, q: str = Form("", max_length=1000)):
        question = q.strip()
        if not question:
            return await page(request, error="Type a question first.", status=422)
        try:
            result, from_cache = await answer(request, question)
        except Limited as e:
            return await page(request, question=question, error=message(e.verdict), error_kind="limit", status=429)
        except ServiceError as e:
            unavailable("the question page", e)
            return await page(request, question=question, status=503, error_kind="error",
                              error="The answer service is not available right now. Please try again later.")
        return await page(request, question=question, result=result, from_cache=from_cache)

    @app.get("/documents", response_class=HTMLResponse)
    async def documents_page(request: Request):
        rows, error = [], ""
        try:
            rows = [{**d, "language_name": LANGUAGE_NAMES.get(d["language"], d["language"]), "ago": ago(d["ingested_at"]),
                     "indexed": d["ingested_at"].isoformat(timespec="minutes")} for d in await service.document_overview()]
        except ServiceError as e:
            unavailable("the documents page", e)
            error = "The document list could not be loaded just now. Please try again in a moment."
        response = templates.TemplateResponse(request, "documents.html", {
            **await shell(request), "active": "documents", "rows": rows, "error": error,
            "totals": {"documents": len(rows), "sections": sum(r["sections"] for r in rows),
                       "passages": sum(r["chunk_count"] for r in rows)}}, status_code=503 if error else 200)
        response.headers["Content-Security-Policy"] = PAGE_CSP
        return response

    if mcp_asgi is not None:
        # Last, so every route above wins; the MCP app answers /mcp (and 404 for anything else).
        app.mount("/", mcp_asgi)
    return app


app = create_app()
