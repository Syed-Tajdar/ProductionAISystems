"""MCP server: search_documents, ask and list_documents over the ingested documents. Read-only.

    rag-mcp                                   # stdio, for Claude Desktop and other local clients
    MCP_BEARER_TOKEN=... rag-mcp --transport http --host 0.0.0.0 --port 8765   # streamable HTTP at /mcp

Over HTTP every request needs "Authorization: Bearer <MCP_BEARER_TOKEN>"; the server refuses to
start without a token. Logs go to stderr, so stdout stays reserved for the stdio protocol.

The web app (rag.api) can also serve /mcp: privately with the same token, or as a public demo
without a token, where search_documents and ask count against the demo limits (DemoLimits).
"""

import argparse
import hmac
import json
import logging
import os
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from rag import __version__
from rag.config import Settings
from rag.demo import LimitsUnavailable, client_address, message
from rag.service import RagService, ServiceError, unavailable

INSTRUCTIONS = (
    "Company documents of Palm Grove Realty, a property company in Dubai: tenant handbook, customer service FAQ, "
    "amenities guide, working time and leave policy, travel and expenses policy, in English and Arabic. "
    "Use ask for a finished answer with numbered sources; use search_documents to read the matching passages "
    "yourself. Answers only ever come from these documents; when they do not cover a question, ask says so."
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

# The address of the HTTP client whose request is running, set by ClientAddress.
current_client: ContextVar[str] = ContextVar("mcp_client", default="unknown")


@dataclass
class DemoLimits:
    """Public endpoint: the web demo's limiter and answer cache (rag.demo), shared with the page."""
    limiter: object
    cache: object


class ClientAddress:
    """ASGI middleware: records the client address for the demo limits while the request runs."""

    def __init__(self, app, header: str = ""):
        self.app, self.header = app, header

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or []}
        token = current_client.set(client_address(headers, (scope.get("client") or (None,))[0], self.header))
        try:
            await self.app(scope, receive, send)
        finally:
            current_client.reset(token)


class Chunk(BaseModel):
    document: str = Field(description="Document path, e.g. en/tenant-handbook.pdf")
    title: str
    section: str = Field(description="Heading path, e.g. 3. Rent and payments > 3.3 Late and returned payments")
    page: int | None = Field(description="Page number for PDF documents, otherwise null")
    language: Literal["en", "ar"]
    text: str
    score: float = Field(description="Reciprocal rank fusion score; higher is better")


class SearchResult(BaseModel):
    query: str
    chunks: list[Chunk]


class Source(BaseModel):
    n: int = Field(description="The number used in the answer text, as in [1]")
    document: str
    title: str
    section: str
    page: int | None
    quotes: list[str] = Field(description="Exact words from this source that the answer relies on")


class AskResult(BaseModel):
    question: str
    answer: str = Field(description="Plain text with [n] citation markers, in the question's language")
    answerable: bool = Field(description="False when the documents do not contain the answer")
    language: Literal["en", "ar"]
    direction: Literal["ltr", "rtl"] = Field(description="Text direction for display; the text itself has no bidi marks")
    sources: list[Source]
    not_covered: str = Field(description="Part of the question the documents do not answer, or empty")


class Document(BaseModel):
    path: str
    title: str
    language: Literal["en", "ar"]
    format: Literal["markdown", "pdf", "docx"]
    chunk_count: int
    ingested_at: str


class DocumentList(BaseModel):
    documents: list[Document]


def create_server(service: RagService, limits: DemoLimits | None = None) -> MCPServer:
    server = MCPServer(name="palm-grove-docs", title="Palm Grove Realty documents", version=__version__,
                       instructions=INSTRUCTIONS)

    async def charge() -> None:
        """Public demo only: count the call against the visitor's limit and the daily cap."""
        if limits is None:
            return
        try:
            verdict = await limits.limiter.take(current_client.get())
        except LimitsUnavailable as e:
            raise ToolError(str(e)) from e
        if not verdict.allowed:
            raise ToolError(message(verdict))

    @server.tool(title="Search documents", annotations=READ_ONLY)
    async def search_documents(
        query: Annotated[str, Field(min_length=1, max_length=1000, description="What to look for, in English or Arabic")],
        language: Annotated[Literal["any", "en", "ar"], Field(
            description="Only return passages in this language; 'any' searches both")] = "any",
        top_k: Annotated[int, Field(ge=1, le=10, description="How many passages to return")] = 5,
    ) -> SearchResult:
        """Find the passages of the company documents that best match a query.

        Hybrid search: meaning (multilingual embeddings) plus keywords, with the query also
        translated so English and Arabic documents are both found. Returns passages with their
        document, section, page and text; it does not write an answer. Use ask for that.
        """
        await charge()
        try:
            hits = await service.search(query, language=None if language == "any" else language, top_k=top_k)
        except ServiceError as e:
            raise ToolError(unavailable("MCP search_documents", e)) from e
        return SearchResult(query=query, chunks=[
            Chunk(document=h.document_path, title=h.title, section=h.heading_path or h.section, page=h.page,
                  language=h.language, text=h.content, score=round(h.score, 6)) for h in hits])

    @server.tool(title="Ask the documents", annotations=READ_ONLY)
    async def ask(
        question: Annotated[str, Field(min_length=1, max_length=1000,
                                       description="A question in English or Arabic; the answer uses the same language")],
    ) -> AskResult:
        """Answer a question from the company documents, with a citation on every statement.

        Each statement in the answer ends with [n] markers that point to sources; every source
        lists the exact quotes that support the answer. If the documents do not contain the
        answer, answerable is false and the answer says so plainly instead of guessing.
        """
        # The same cache as the web page: repeated questions are free and do not count.
        a = await limits.cache.lookup(f"|{question}") if limits else None
        if a is None:
            await charge()
            try:
                a = await service.ask(question, trace={})  # the trace is cached with the answer for the web page
            except ServiceError as e:
                raise ToolError(unavailable("MCP ask", e)) from e
            if limits:
                await limits.cache.store(f"|{question}", a)
        return AskResult(question=question, answer=a.text, answerable=a.answerable, language=a.language,
                         direction=a.direction, not_covered=a.not_covered, sources=[
                             Source(n=s["n"], document=s["document"], title=s["title"], section=s["heading_path"] or s["section"],
                                    page=s["page"], quotes=s["quotes"]) for s in a.sources])

    @server.tool(title="List documents", annotations=READ_ONLY)
    async def list_documents() -> DocumentList:
        """List the documents that can be searched, with language, format and number of passages."""
        try:
            return DocumentList(documents=[Document(**d) for d in await service.list_documents()])
        except ServiceError as e:
            raise ToolError(unavailable("MCP list_documents", e)) from e

    return server


class BearerAuth:
    """ASGI middleware: every HTTP request must carry Authorization: Bearer <token>."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            scheme, _, given = headers.get(b"authorization", b"").partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(given.strip(), self.token):
                body = json.dumps({"error": "unauthorized", "message": "missing or wrong bearer token"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"), (b"www-authenticate", b'Bearer realm="mcp"'),
                    (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, token: str, host: str = "127.0.0.1"):
    if len(token) < 24:
        raise ValueError("MCP_BEARER_TOKEN must be at least 24 characters")
    # Stateless: each request stands alone, so the server can scale out or restart between calls.
    return BearerAuth(server.streamable_http_app(stateless_http=True, json_response=True, host=host), token)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    server = create_server(RagService(Settings()))
    if args.transport == "stdio":
        server.run("stdio")
        return 0
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if not token:
        print("MCP_BEARER_TOKEN is not set; refusing to serve HTTP without a token", file=sys.stderr)
        return 2
    import uvicorn

    uvicorn.run(http_app(server, token, args.host), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
