"""Embedding providers behind one interface, plus a cache keyed by content hash.

Add a provider by implementing Embedder (provider, model, dim, embed) and registering it in
make_embedder(). Vectors are L2-normalized so cosine distance in pgvector behaves the same for
every provider.
"""

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

import httpx

from rag.chunking import content_hash
from rag.config import Settings

log = logging.getLogger(__name__)
Task = Literal["document", "query"]


class EmbeddingError(RuntimeError):
    pass


class QuotaExceeded(EmbeddingError):
    """The provider asked us to wait longer than we are willing to (for example a daily quota)."""


class Embedder(Protocol):
    provider: str
    model: str
    dim: int

    async def embed(self, texts: list[str], task: Task, titles: list[str | None] | None = None) -> list[list[float]]: ...


def normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class GeminiEmbedder:
    """batchEmbedContents with task types (RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY) and reduced dimensions."""

    provider = "gemini"
    TASKS = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}

    def __init__(self, api_key: str, model: str = "gemini-embedding-2", dim: int = 768, *,
                 base_url: str = "https://generativelanguage.googleapis.com", batch_size: int = 100,
                 requests_per_minute: int = 10, max_wait: float = 60.0, max_attempts: int = 4,
                 client: httpx.AsyncClient | None = None, sleep=asyncio.sleep, clock=time.monotonic):
        if not api_key:
            raise EmbeddingError("GEMINI_API_KEY is not set")
        self.model, self.dim = model, dim
        self.api_key, self.base_url = api_key, base_url.rstrip("/")
        self.batch_size = min(batch_size, 100)
        self.interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
        self.max_wait, self.max_attempts = max_wait, max_attempts
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
        self.sleep, self.clock = sleep, clock
        self._next_slot = 0.0
        self.requests = 0

    async def _pace(self) -> None:
        now = self.clock()
        if now < self._next_slot:
            await self.sleep(self._next_slot - now)
        self._next_slot = max(now, self._next_slot) + self.interval

    async def embed(self, texts: list[str], task: Task, titles: list[str | None] | None = None) -> list[list[float]]:
        titles = titles or [None] * len(texts)
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            out += await self._batch(texts[start:start + self.batch_size], titles[start:start + self.batch_size], task)
        return out

    async def _batch(self, texts: list[str], titles: list[str | None], task: Task) -> list[list[float]]:
        requests = []
        for text, title in zip(texts, titles, strict=True):
            r = {"model": f"models/{self.model}", "content": {"parts": [{"text": text}]},
                 "taskType": self.TASKS[task], "outputDimensionality": self.dim}
            if title and task == "document":
                r["title"] = title
            requests.append(r)
        url = f"{self.base_url}/v1beta/models/{self.model}:batchEmbedContents"
        for attempt in range(1, self.max_attempts + 1):
            await self._pace()
            res = await self.client.post(url, json={"requests": requests}, headers={"x-goog-api-key": self.api_key})
            self.requests += 1
            if res.status_code == 200:
                vectors = [e["values"] for e in res.json()["embeddings"]]
                if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
                    raise EmbeddingError(f"expected {len(texts)} vectors of {self.dim} dimensions")
                return [normalize(v) for v in vectors]
            if res.status_code == 429 or res.status_code >= 500:
                wait = retry_delay(res) if res.status_code == 429 else None
                wait = wait if wait is not None else 2.0 ** attempt
                if wait > self.max_wait:
                    raise QuotaExceeded(f"Gemini quota exhausted, retry in {wait:.0f}s: {error_message(res)}")
                if attempt < self.max_attempts:
                    log.warning("Gemini returned HTTP %s, retrying in %.0fs", res.status_code, wait)
                    await self.sleep(wait)
                    continue
            raise EmbeddingError(f"Gemini returned HTTP {res.status_code}: {error_message(res)}")
        raise EmbeddingError("unreachable")


def error_message(res: httpx.Response) -> str:
    try:
        return str(res.json().get("error", {}).get("message", ""))[:300]
    except ValueError:
        return res.text[:300]


def retry_delay(res: httpx.Response) -> float | None:
    """Seconds from google.rpc.RetryInfo (e.g. "37s"), if the error carries one."""
    try:
        for d in res.json().get("error", {}).get("details", []):
            if d.get("@type", "").endswith("RetryInfo"):
                m = re.fullmatch(r"([\d.]+)s", d.get("retryDelay", ""))
                if m:
                    return float(m.group(1))
    except ValueError:
        pass
    return None


class CacheStore(Protocol):
    async def get(self, key: tuple, hashes: list[str]) -> dict[str, list[float]]: ...
    async def put(self, key: tuple, items: dict[str, list[float]]) -> None: ...


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0


@dataclass
class CachedEmbedder:
    """Looks every text up by sha256 before calling the provider; stores what it had to embed."""

    inner: Embedder
    store: CacheStore
    stats: CacheStats = field(default_factory=CacheStats)

    @property
    def provider(self) -> str:
        return self.inner.provider

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def dim(self) -> int:
        return self.inner.dim

    async def embed(self, texts: list[str], task: Task, titles: list[str | None] | None = None) -> list[list[float]]:
        titles = titles or [None] * len(texts)
        hashes = [content_hash(f"{title or ''}\n{text}") for text, title in zip(texts, titles, strict=True)]
        key = (self.provider, self.model, self.dim, task)
        found = await self.store.get(key, sorted(set(hashes)))
        missing = [i for i, h in enumerate(hashes) if h not in found]
        unique: dict[str, int] = {}
        for i in missing:
            unique.setdefault(hashes[i], i)
        self.stats.hits += len(texts) - len(missing)
        self.stats.misses += len(unique)
        if unique:
            idx = list(unique.values())
            vectors = await self.inner.embed([texts[i] for i in idx], task, [titles[i] for i in idx])
            new = {hashes[i]: v for i, v in zip(idx, vectors, strict=True)}
            await self.store.put(key, new)
            found.update(new)
        return [found[h] for h in hashes]


def make_embedder(settings: Settings, client: httpx.AsyncClient | None = None) -> Embedder:
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder(settings.gemini_api_key.get_secret_value(), settings.embedding_model, settings.embedding_dim,
                              base_url=settings.gemini_base_url, batch_size=settings.embedding_batch_size,
                              requests_per_minute=settings.embedding_requests_per_minute, client=client)
    raise EmbeddingError(f"unknown embedding provider {settings.embedding_provider!r}")
