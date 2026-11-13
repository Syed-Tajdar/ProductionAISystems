"""The MCP server through the SDK's own client: in-process, over stdio, and over streamable HTTP."""

import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from rag.config import Settings
from rag.db import PgCache
from rag.embeddings import CachedEmbedder
from rag.ingest import ingest_folder
from rag.mcp_server import create_server, http_app
from rag.service import RagService

from .test_answer import FakeLLM, st
from .test_db import DOCS, count

pytestmark = pytest.mark.anyio
TOKEN = "test-token-0123456789abcdef"


@pytest.fixture
async def loaded(conn, fake_embedder):
    await ingest_folder(conn, CachedEmbedder(fake_embedder, PgCache(conn)), DOCS)
    return fake_embedder


def service(database_url, embedder, llm=None) -> RagService:
    return RagService(Settings(_env_file=None, database_url=database_url), embedder=embedder, llm=llm)


async def test_tools_are_described_and_read_only():
    async with Client(create_server(RagService(Settings(_env_file=None)))) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {"search_documents", "ask", "list_documents"}  # no ingestion or write tools
    for t in tools.values():
        assert t.description and len(t.description) > 60
        assert t.annotations.read_only_hint and not t.annotations.destructive_hint and not t.annotations.open_world_hint
        assert t.output_schema and t.output_schema["type"] == "object"
    search = tools["search_documents"].input_schema
    assert search["required"] == ["query"]
    assert search["properties"]["language"]["enum"] == ["any", "en", "ar"]
    assert (search["properties"]["top_k"]["minimum"], search["properties"]["top_k"]["maximum"]) == (1, 10)
    assert tools["ask"].input_schema["required"] == ["question"]
    assert tools["list_documents"].input_schema["properties"] == {}


async def test_search_documents(database_url, loaded, conn):
    async with Client(create_server(service(database_url, loaded))) as c:
        r = await c.call_tool("search_documents", {"query": "returned cheque fee", "top_k": 3})
        ar = await c.call_tool("search_documents", {"query": "returned cheque fee", "language": "ar", "top_k": 4})
        bad = await c.call_tool("search_documents", {"query": "x", "top_k": 50})
    assert not r.is_error
    chunks = r.structured_content["chunks"]
    assert len(chunks) == 3
    top = chunks[0]
    assert (top["document"], top["section"], top["page"], top["language"]) == (
        "en/tenant-handbook.pdf", "3. Rent and payments > 3.3 Late and returned payments", 2, "en")
    assert "AED 500" in top["text"] and top["score"] > 0
    assert {c["language"] for c in ar.structured_content["chunks"]} == {"ar"}
    assert bad.is_error  # top_k is limited to 10 by the schema
    assert await count(conn, "chunks") == 111  # nothing written


async def test_ask_english_and_arabic(database_url, loaded):
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("A returned cheque costs AED 500.", (1, "A returned cheque costs AED 500"))]})
    async with Client(create_server(service(database_url, loaded, llm))) as c:
        en = (await c.call_tool("ask", {"question": "What does a returned cheque cost?"})).structured_content
        llm.data = {"answerable": False, "statements": [], "not_covered": "لا تذكر المصادر ذلك."}
        ar = (await c.call_tool("ask", {"question": "هل تقدمون خدمات تنظيف؟"})).structured_content
    assert en["answerable"] and en["answer"] == "A returned cheque costs AED 500. [1]"
    assert (en["language"], en["direction"]) == ("en", "ltr")
    assert en["sources"] == [{"n": 1, "document": "en/tenant-handbook.pdf", "title": "Tenant Handbook",
                              "section": "3. Rent and payments > 3.3 Late and returned payments", "page": 2,
                              "quotes": ["A returned cheque costs AED 500"]}]
    assert (ar["answerable"], ar["answer"], ar["direction"]) == (False, "لا تحتوي المستندات على إجابة لهذا السؤال.", "rtl")
    assert ar["not_covered"] == "لا تذكر المصادر ذلك." and ar["sources"] == []


async def test_list_documents(database_url, loaded):
    async with Client(create_server(service(database_url, loaded))) as c:
        docs = (await c.call_tool("list_documents", {})).structured_content["documents"]
    assert len(docs) == 8
    pdf = next(d for d in docs if d["path"] == "en/tenant-handbook.pdf")
    assert (pdf["title"], pdf["language"], pdf["format"], pdf["chunk_count"]) == ("Tenant Handbook", "en", "pdf", 23)
    assert {d["language"] for d in docs} == {"en", "ar"}


async def test_errors_are_tool_errors_with_a_generic_text(caplog):
    from rag.service import UNAVAILABLE
    with caplog.at_level("WARNING", logger="rag.service"):
        async with Client(create_server(RagService(Settings(_env_file=None)))) as c:
            r = await c.call_tool("ask", {"question": "anything"})
            s = await c.call_tool("search_documents", {"query": "anything"})
            d = await c.call_tool("list_documents", {})
    for res in (r, s, d):  # a tool error, not a crash, and nothing about the setup
        assert res.is_error and UNAVAILABLE in res.content[0].text
        assert "is not set" not in res.content[0].text
    logged = "\n".join(rec.getMessage() for rec in caplog.records)  # the reason is in the server log
    assert "MCP ask failed: ANTHROPIC_API_KEY is not set" in logged
    assert "MCP search_documents failed: DATABASE_URL is not set" in logged


# --- real transports ---------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(database_url, loaded):
    port = free_port()
    app = http_app(create_server(service(database_url, loaded)), TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(5)


async def test_http_needs_the_bearer_token(http_server):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    async with httpx.AsyncClient() as h:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}):
            res = await h.post(http_server, json=body, headers=headers)
            assert res.status_code == 401 and res.headers["www-authenticate"].startswith("Bearer")
    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"})
    async with Client(streamable_http_client(http_server, http_client=client)) as c:
        assert {t.name for t in (await c.list_tools()).tools} == {"search_documents", "ask", "list_documents"}
        docs = await c.call_tool("list_documents", {})
    assert len(docs.structured_content["documents"]) == 8


def test_http_token_must_be_long_enough():
    with pytest.raises(ValueError, match="24 characters"):
        http_app(create_server(RagService(Settings(_env_file=None))), "short")


def test_http_refuses_to_start_without_a_token(monkeypatch, capsys):
    from rag.mcp_server import main

    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    assert main(["--transport", "http"]) == 2
    assert "MCP_BEARER_TOKEN is not set" in capsys.readouterr().err


async def test_stdio_subprocess(database_url, loaded):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "rag.mcp_server"], cwd=str(Path(__file__).resolve().parents[1]),
        env={"PATH": os.environ.get("PATH", ""), "DATABASE_URL": database_url})
    async with Client(params) as c:
        assert len((await c.list_tools()).tools) == 3
        docs = await c.call_tool("list_documents", {})
    assert len(docs.structured_content["documents"]) == 8
