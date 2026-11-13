import json
import math

import httpx
import pytest

from rag.config import Settings
from rag.embeddings import CachedEmbedder, EmbeddingError, GeminiEmbedder, QuotaExceeded, make_embedder, normalize

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def gemini(handler, **kw):
    clock = Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = GeminiEmbedder("test-key", client=client, sleep=clock.sleep, clock=clock, **kw)
    return emb, clock


def ok(request, dim=768):
    n = len(json.loads(request.content)["requests"])
    return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0] + [0.0] * (dim - 2)} for _ in range(n)]})


async def test_request_format_and_normalization():
    seen = []

    def handler(request):
        seen.append(request)
        return ok(request)

    emb, _ = gemini(handler)
    vectors = await emb.embed(["Rent is paid in advance.", "Quiet hours"], "document", ["Tenant Handbook", None])
    req = seen[0]
    body = json.loads(req.content)
    assert req.url.path == "/v1beta/models/gemini-embedding-2:batchEmbedContents"
    assert req.headers["x-goog-api-key"] == "test-key"
    assert body["requests"][0] == {"model": "models/gemini-embedding-2", "content": {"parts": [{"text": "Rent is paid in advance."}]},
                                   "taskType": "RETRIEVAL_DOCUMENT", "outputDimensionality": 768, "title": "Tenant Handbook"}
    assert "title" not in body["requests"][1]
    assert vectors[0][:2] == [0.6, 0.8] and math.isclose(sum(x * x for x in vectors[0]), 1.0)


async def test_query_task_and_no_title():
    seen = []
    emb, _ = gemini(lambda r: seen.append(json.loads(r.content)) or ok(r))
    await emb.embed(["notice period?"], "query", ["ignored"])
    assert seen[0]["requests"][0]["taskType"] == "RETRIEVAL_QUERY" and "title" not in seen[0]["requests"][0]


async def test_batches_of_100_and_pacing():
    sizes = []
    emb, clock = gemini(lambda r: sizes.append(len(json.loads(r.content)["requests"])) or ok(r), requests_per_minute=30)
    out = await emb.embed([f"text {i}" for i in range(250)], "document")
    assert sizes == [100, 100, 50] and len(out) == 250
    assert clock.slept == [2.0, 2.0]  # 30 requests a minute: one every 2 seconds


async def test_429_waits_for_the_retry_delay():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "message": "Quota exceeded", "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})
        return ok(request)

    emb, clock = gemini(handler, requests_per_minute=0)
    await emb.embed(["a"], "document")
    assert len(calls) == 2 and clock.slept == [7.0]


async def test_long_retry_delay_means_quota_exhausted():
    def handler(request):
        return httpx.Response(429, json={"error": {"code": 429, "message": "You exceeded your current quota", "details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "4269s"}]}})

    emb, clock = gemini(handler, requests_per_minute=0)
    with pytest.raises(QuotaExceeded, match="retry in 4269s"):
        await emb.embed(["a"], "document")
    assert clock.slept == []  # does not sit and wait for an hour


async def test_server_errors_are_retried_then_reported():
    calls = []
    emb, clock = gemini(lambda r: calls.append(1) or httpx.Response(503, json={"error": {"message": "busy"}}),
                        requests_per_minute=0, max_attempts=3)
    with pytest.raises(EmbeddingError, match="HTTP 503: busy"):
        await emb.embed(["a"], "document")
    assert len(calls) == 3 and clock.slept == [2.0, 4.0]


async def test_client_errors_fail_at_once():
    calls = []
    emb, _ = gemini(lambda r: calls.append(1) or httpx.Response(400, json={"error": {"message": "bad dim"}}))
    with pytest.raises(EmbeddingError, match="HTTP 400: bad dim"):
        await emb.embed(["a"], "document")
    assert len(calls) == 1


async def test_wrong_dimensions_are_rejected():
    emb, _ = gemini(lambda r: ok(r, dim=3072))
    with pytest.raises(EmbeddingError, match="768 dimensions"):
        await emb.embed(["a"], "document")


def test_missing_key_and_unknown_provider():
    with pytest.raises(EmbeddingError, match="GEMINI_API_KEY"):
        make_embedder(Settings(_env_file=None))
    assert make_embedder(Settings(_env_file=None, gemini_api_key="any-non-empty-value")).model == "gemini-embedding-2"


def test_normalize_zero_vector():
    assert normalize([0.0, 0.0]) == [0.0, 0.0]


class MemoryCache:
    def __init__(self):
        self.data, self.gets = {}, []

    async def get(self, key, hashes):
        self.gets.append((key, list(hashes)))
        return {h: self.data[(key, h)] for h in hashes if (key, h) in self.data}

    async def put(self, key, items):
        for h, v in items.items():
            self.data[(key, h)] = v


async def test_cache_reuses_vectors_and_dedupes(fake_embedder):
    cached = CachedEmbedder(fake_embedder, MemoryCache())
    first = await cached.embed(["alpha", "beta", "alpha"], "document", ["T", "T", "T"])
    assert fake_embedder.calls == [(2, "document")]  # "alpha" twice in one call is embedded once
    assert (cached.stats.misses, cached.stats.hits) == (2, 0)
    again = await cached.embed(["beta", "alpha"], "document", ["T", "T"])
    assert fake_embedder.calls == [(2, "document")]  # nothing new to embed
    assert (cached.stats.misses, cached.stats.hits) == (2, 2)
    assert again == [first[1], first[0]]
    await cached.embed(["alpha"], "query")  # same text, other task: its own cache entry
    await cached.embed(["alpha"], "document", ["Other title"])  # the title is part of the key
    assert fake_embedder.calls == [(2, "document"), (1, "query"), (1, "document")]
