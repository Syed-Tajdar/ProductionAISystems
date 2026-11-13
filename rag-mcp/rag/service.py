"""Read-only operations shared by the MCP server: search, ask and list documents.

Connections, the embedder, the answer model and the translator are created on first use, so a
client can list tools without a database or keys. Nothing here writes documents or chunks; the
only writes are cached query embeddings.
"""

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rag.answer import Answer, AnswerError, ClaudeLLM, answer_question
from rag.config import Settings
from rag.db import Hit, PgCache
from rag.embeddings import CachedEmbedder, EmbeddingError, make_embedder
from rag.retrieval import make_translator, search

log = logging.getLogger(__name__)
UNAVAILABLE = "The document service is not available right now. Please try again later."


class ServiceError(RuntimeError):
    """Missing setup or a provider that is down. The text is for the log: it can quote an upstream
    service's answer, so callers outside get unavailable() instead."""


def unavailable(where: str, error: Exception) -> str:
    """Logs what went wrong and returns the one sentence API and MCP clients get."""
    log.warning("%s failed: %s", where, error)
    return UNAVAILABLE


@dataclass
class RagService:
    settings: Settings
    pool: AsyncConnectionPool | None = None
    embedder: object | None = None
    llm: object | None = None
    translator: object | None = None
    _ready: dict = field(default_factory=dict)

    async def _pool(self) -> AsyncConnectionPool:
        if self.pool is None:
            dsn = self.settings.database_url.get_secret_value()
            if not dsn:
                raise ServiceError("DATABASE_URL is not set")
            # No idle minimum: connections open on demand and close after a short idle time, which suits
            # serverless instances and Supabase's transaction pooler (which also rules out prepared statements).
            self.pool = AsyncConnectionPool(dsn, min_size=0, max_size=self.settings.db_pool_max,
                                            max_idle=self.settings.db_pool_max_idle_seconds, open=False,
                                            kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None})
        if self.pool.closed:
            await self.pool.open()
        return self.pool

    @asynccontextmanager
    async def connection(self):
        """One pooled connection for a short piece of work (the demo limits use it too)."""
        pool = await self._pool()
        async with pool.connection() as conn:
            yield conn

    def _embedder(self):
        if self.embedder is None:
            try:
                self.embedder = make_embedder(self.settings)
            except EmbeddingError as e:
                raise ServiceError(str(e)) from e
        return self.embedder

    def _llm(self):
        if self.llm is None:
            try:
                self.llm = ClaudeLLM(self.settings.anthropic_api_key.get_secret_value(), self.settings.answer_model,
                                     self.settings.answer_effort)
            except AnswerError as e:
                raise ServiceError(str(e)) from e
        return self.llm

    def _translator(self):
        if "translator" not in self._ready:
            self._ready["translator"] = True
            if self.translator is None:
                self.translator = make_translator(self.settings)
        return self.translator

    async def search(self, query: str, *, language: str | None = None, top_k: int | None = None,
                     trace: dict | None = None) -> list[Hit]:
        pool, base = await self._pool(), self._embedder()
        async with pool.connection() as conn:
            try:
                return await search(conn, CachedEmbedder(base, PgCache(conn)), query, top_k=top_k or self.settings.top_k,
                                    candidates=self.settings.search_candidates, rrf_k=self.settings.rrf_k,
                                    only_language=language, translator=self._translator(),
                                    max_per_document=self.settings.max_per_document, trace=trace)
            except EmbeddingError as e:
                raise ServiceError(f"embedding failed: {e}") from e

    async def ask(self, question: str, language: str | None = None, trace: dict | None = None) -> Answer:
        """trace: when a dict is given, it is filled with how the answer was built (rag.retrieval.record_search,
        rag.answer.answer_question) and attached to the answer; the answer itself does not change."""
        llm = self._llm()
        started = time.perf_counter()
        if trace is not None:
            trace["_origin"] = started  # timeline offsets count from here
        hits = await self.search(question, trace=trace)
        try:
            result = await answer_question(question, hits, llm, language, trace=trace)
        except AnswerError as e:
            raise ServiceError(f"answer failed: {e}") from e
        if trace is not None:
            trace.setdefault("timings_ms", {})["total"] = round((time.perf_counter() - started) * 1000)
            trace.pop("_origin", None)
        return result

    async def list_documents(self) -> list[dict]:
        pool = await self._pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select path, title, language, format, chunk_count, ingested_at from documents order by path")
            return [{**r, "ingested_at": r["ingested_at"].isoformat()} for r in await cur.fetchall()]

    async def document_overview(self) -> list[dict]:
        """Every document with its number of sections and passages and when it was last indexed (the Documents page)."""
        pool = await self._pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select d.path, d.title, d.language, d.format, d.chunk_count, d.ingested_at, "
                "count(distinct c.section) as sections from documents d left join chunks c on c.document_id = d.id "
                "group by d.id order by d.title, d.language")
            return list(await cur.fetchall())

    async def close(self) -> None:
        if self.pool is not None and not self.pool.closed:
            await self.pool.close()
