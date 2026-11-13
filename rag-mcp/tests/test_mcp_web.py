"""/mcp on the web app (the Vercel deployment): public demo under the page's limits, private with a
bearer token, or off. Over real HTTP with the SDK's client."""

import socket
import threading
import time

import httpx
import httpx2
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from rag.api import create_app
from rag.config import Settings
from rag.demo import AnswerCache, DemoLimiter
from rag.service import RagService

from .test_api import CHEQUE, CitingLLM, loaded  # noqa: F401 (fixture)

pytestmark = pytest.mark.anyio
TOKEN = "test-token-0123456789abcdef"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture
def web(database_url, loaded):  # noqa: F811
    servers = []

    def start(**overrides):
        settings = Settings(_env_file=None, database_url=database_url, client_ip_header="x-real-ip", **overrides)
        service = RagService(settings, embedder=loaded, llm=CitingLLM(CHEQUE))
        limiter = DemoLimiter(settings.demo_questions_per_client, 600, settings.demo_daily_cap)
        server, thread, base = serve(create_app(settings, service=service, limiter=limiter, cache=AnswerCache()))
        servers.append((server, thread))
        return base, limiter

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(5)


def mcp_client(url, **headers):
    return Client(streamable_http_client(url, http_client=httpx2.AsyncClient(headers=headers)))


async def test_public_endpoint_needs_no_token_and_counts_against_the_demo_limits(web):
    base, limiter = web(mcp_public=True, demo_questions_per_client=2)
    async with mcp_client(f"{base}/mcp", **{"x-real-ip": "203.0.113.1"}) as c:
        assert {t.name for t in (await c.list_tools()).tools} == {"search_documents", "ask", "list_documents"}
        assert not (await c.call_tool("list_documents", {})).is_error  # free
        assert not (await c.call_tool("search_documents", {"query": "returned cheque"})).is_error
        first = await c.call_tool("ask", {"question": "What is the fee for a returned cheque?"})
        again = await c.call_tool("ask", {"question": "what is the fee for a returned cheque?"})  # from the cache
        assert not first.is_error and again.structured_content["answer"] == first.structured_content["answer"]
        limited = await c.call_tool("search_documents", {"query": "notice period"})
    assert limited.is_error and "Too many questions from your address" in limited.content[0].text
    async with mcp_client(f"{base}/mcp", **{"x-real-ip": "203.0.113.2"}) as other:  # another visitor
        assert not (await other.call_tool("search_documents", {"query": "notice period"})).is_error
    assert limiter.used_today == 3


async def test_page_and_mcp_share_the_daily_cap(web):
    base, _ = web(mcp_public=True, demo_daily_cap=1)
    with httpx.Client(base_url=base) as h:
        assert h.post("/", data={"q": "What is the fee for a returned cheque?"}).status_code == 200
        assert h.get("/health").json()["questions_left_today"] == 0
    async with mcp_client(f"{base}/mcp") as c:
        res = await c.call_tool("search_documents", {"query": "pool hours"})
    assert res.is_error and "limit for today" in res.content[0].text


async def test_private_endpoint_needs_the_token_and_has_no_demo_limits(web):
    base, limiter = web(mcp_bearer_token=TOKEN, demo_questions_per_client=1)
    with httpx.Client() as h:
        res = h.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert res.status_code == 401
        assert h.get(f"{base}/health").json()["mcp"] == "private"
    async with mcp_client(f"{base}/mcp", Authorization=f"Bearer {TOKEN}") as c:
        for query in ("returned cheque", "notice period", "pool"):
            assert not (await c.call_tool("search_documents", {"query": query})).is_error
    assert limiter.used_today == 0


def test_no_mcp_unless_configured():
    app = create_app(Settings(_env_file=None), service=RagService(Settings(_env_file=None)), limiter=DemoLimiter(), cache=AnswerCache())
    with TestClient(app) as c:
        assert c.post("/mcp", json={}).status_code in (404, 405)
        assert c.get("/health").json()["mcp"] == "off"


def test_short_token_is_refused():
    with pytest.raises(ValueError, match="24 characters"):
        create_app(Settings(_env_file=None, mcp_bearer_token="short"), service=RagService(Settings(_env_file=None)))


def test_stores_follow_the_database_setting():
    from rag.demo import PgAnswerCache, PgLimiter

    def stores(settings):
        app = create_app(settings, service=RagService(settings))
        return type(app.state.limiter), type(app.state.cache)

    with_db = Settings(_env_file=None, database_url="postgresql://u:p@localhost:1/db")
    assert stores(with_db) == (PgLimiter, PgAnswerCache)  # shared across serverless instances
    assert stores(Settings(_env_file=None)) == (DemoLimiter, AnswerCache)
    assert stores(with_db.model_copy(update={"demo_store": "memory"})) == (DemoLimiter, AnswerCache)


def test_page_has_the_privacy_note():
    app = create_app(Settings(_env_file=None), service=RagService(Settings(_env_file=None)), limiter=DemoLimiter(), cache=AnswerCache())
    with TestClient(app) as c:
        assert "processed by third-party AI services" in c.get("/").text
