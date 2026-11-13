"""Postgres access with psycopg 3 (async). Vectors travel as pgvector text literals."""

import json
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from rag.chunking import Chunk
from rag.documents import ParsedDocument


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def parse_vector(text: str) -> list[float]:
    return [float(x) for x in text.strip("[]").split(",")]


async def connect(dsn: str) -> psycopg.AsyncConnection:
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    # prepare_threshold=None: Supabase's transaction pooler does not support prepared statements.
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None)


class PgCache:
    """embedding_cache table, used by CachedEmbedder."""

    def __init__(self, conn: psycopg.AsyncConnection):
        self.conn = conn

    async def get(self, key: tuple, hashes: list[str]) -> dict[str, list[float]]:
        if not hashes:
            return {}
        provider, model, dim, task = key
        cur = await self.conn.execute(
            "select content_hash, embedding::text as embedding from embedding_cache "
            "where provider = %s and model = %s and dim = %s and task = %s and content_hash = any(%s)",
            (provider, model, dim, task, hashes))
        return {r["content_hash"]: parse_vector(r["embedding"]) for r in await cur.fetchall()}

    async def put(self, key: tuple, items: dict[str, list[float]]) -> None:
        provider, model, dim, task = key
        async with self.conn.cursor() as cur:
            await cur.executemany(
                "insert into embedding_cache (provider, model, dim, task, content_hash, embedding) "
                "values (%s, %s, %s, %s, %s, %s::vector) on conflict do nothing",
                [(provider, model, dim, task, h, vector_literal(v)) for h, v in items.items()])


async def document_hash(conn, path: str) -> str | None:
    cur = await conn.execute("select content_hash from documents where path = %s", (path,))
    row = await cur.fetchone()
    return row["content_hash"] if row else None


async def save_document(conn, doc: ParsedDocument, chunks: list[Chunk], hashes: list[str], vectors: list[list[float]]) -> None:
    """Replace the document and all its chunks in one transaction."""
    async with conn.transaction():
        cur = await conn.execute(
            "insert into documents (path, title, language, format, content_hash, chunk_count, meta) "
            "values (%s, %s, %s, %s, %s, %s, %s) "
            "on conflict (path) do update set title = excluded.title, language = excluded.language, format = excluded.format, "
            "content_hash = excluded.content_hash, chunk_count = excluded.chunk_count, meta = excluded.meta, ingested_at = now() "
            "returning id",
            (doc.path, doc.title, doc.language, doc.format, doc.content_hash, len(chunks), json.dumps(doc.meta)))
        doc_id = (await cur.fetchone())["id"]
        await conn.execute("delete from chunks where document_id = %s", (doc_id,))
        async with conn.cursor() as c:
            await c.executemany(
                "insert into chunks (document_id, chunk_index, section, heading_path, language, page, page_end, "
                "content, content_hash, embedding) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                [(doc_id, ch.index, ch.section, ch.heading_path, doc.language, ch.page, ch.page_end, ch.content, h,
                  vector_literal(v)) for ch, h, v in zip(chunks, hashes, vectors, strict=True)])


async def prune_documents(conn, keep_paths: list[str]) -> list[str]:
    cur = await conn.execute("delete from documents where not (path = any(%s)) returning path", (keep_paths,))
    return [r["path"] for r in await cur.fetchall()]


@dataclass
class Hit:
    chunk_id: int
    document_path: str
    title: str
    section: str
    heading_path: str
    language: str
    page: int | None
    page_end: int | None
    content: str
    vector_rank: int | None
    keyword_rank: int | None
    alt_keyword_rank: int | None
    similarity: float | None
    score: float

    def to_dict(self) -> dict:
        return dict(self.__dict__)


async def hybrid_search(conn, embedding: list[float], query: str, *, language: str = "en", match_count: int = 6,
                        candidates: int = 20, rrf_k: int = 60, only_language: str | None = None,
                        alt_query: str | None = None, alt_language: str | None = None,
                        max_per_document: int | None = None) -> list[Hit]:
    cur = await conn.execute(
        "select * from hybrid_search(%s::vector, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (vector_literal(embedding), query, language, match_count, candidates, rrf_k, only_language,
         alt_query, alt_language, max_per_document))
    return [Hit(**r) for r in await cur.fetchall()]
