"""Ingestion and hybrid search against a real pgvector Postgres (Docker), with the fake embedder."""

import shutil
from pathlib import Path

import pytest

from rag.db import PgCache, hybrid_search
from rag.embeddings import CachedEmbedder
from rag.ingest import ingest_folder
from rag.retrieval import search

pytestmark = pytest.mark.anyio
DOCS = Path(__file__).resolve().parents[1] / "documents"


async def count(conn, table):
    cur = await conn.execute(f"select count(*) as n from {table}")
    return (await cur.fetchone())["n"]


async def ingest(conn, embedder, folder=DOCS, **kw):
    cached = CachedEmbedder(embedder, PgCache(conn))
    return await ingest_folder(conn, cached, folder, **kw), cached


async def test_ingest_stores_documents_chunks_and_metadata(conn, fake_embedder):
    report, cached = await ingest(conn, fake_embedder)
    assert len(report.ingested) == 8 and report.failed == {}
    assert await count(conn, "documents") == 8
    assert await count(conn, "chunks") == report.chunks == 111
    assert await count(conn, "embedding_cache") == 111
    assert fake_embedder.calls == [(111, "document")]  # all eight documents in one embed call
    cur = await conn.execute(
        "select d.path, d.format, c.language, c.section, c.heading_path, c.page from chunks c join documents d on d.id = c.document_id "
        "where c.content like '%returned cheque costs AED 500%'")
    row = await cur.fetchone()
    assert row == {"path": "en/tenant-handbook.pdf", "format": "pdf", "language": "en", "section": "3.3 Late and returned payments",
                   "heading_path": "3. Rent and payments > 3.3 Late and returned payments", "page": 2}
    cur = await conn.execute("select count(*) as n from chunks where language = 'ar'")
    assert (await cur.fetchone())["n"] == 56


async def test_reingest_skips_unchanged_and_force_uses_the_cache(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    calls = len(fake_embedder.calls)
    report, cached = await ingest(conn, fake_embedder)
    assert (report.ingested, len(report.skipped)) == ([], 8)
    report, cached = await ingest(conn, fake_embedder, force=True)
    assert len(report.ingested) == 8
    assert len(fake_embedder.calls) == calls  # every embedding came from the cache
    assert (cached.stats.hits, cached.stats.misses) == (111, 0)


async def test_changed_file_replaces_its_chunks_and_prune_removes_deleted(conn, fake_embedder, tmp_path):
    folder = tmp_path / "docs"
    shutil.copytree(DOCS / "en", folder / "en")
    await ingest(conn, fake_embedder, folder)
    faq = folder / "en/service-faq.md"
    faq.write_text(faq.read_text(encoding="utf-8").replace("2.5% processing fee", "3% processing fee"), encoding="utf-8")
    (folder / "en/leave-policy.docx").unlink()
    before = len(fake_embedder.calls)
    report, cached = await ingest(conn, fake_embedder, folder, prune=True)
    assert report.ingested == ["en/service-faq.md"] and report.removed == ["en/leave-policy.docx"]
    assert cached.stats.misses == 1  # only the edited chunk is embedded again
    assert fake_embedder.calls[before:] == [(1, "document")]
    cur = await conn.execute("select count(*) as n from chunks where content like '%3% processing fee%'")
    assert (await cur.fetchone())["n"] == 1
    cur = await conn.execute("select count(*) as n from chunks where content like '%2.5% processing fee%'")
    assert (await cur.fetchone())["n"] == 0
    assert await count(conn, "documents") == 3


async def test_a_bad_file_does_not_stop_the_others(conn, fake_embedder, tmp_path):
    folder = tmp_path / "docs"
    shutil.copytree(DOCS / "en", folder)
    (folder / "broken.pdf").write_bytes(b"not a pdf")
    report, _ = await ingest(conn, fake_embedder, folder)
    assert len(report.ingested) == 4 and list(report.failed) == ["broken.pdf"]


async def test_hybrid_search_fuses_vector_and_keyword_ranks(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    hits = await search(conn, CachedEmbedder(fake_embedder, PgCache(conn)), "What does a returned cheque cost?", top_k=5)
    top = hits[0]
    assert top.document_path == "en/tenant-handbook.pdf" and top.section == "3.3 Late and returned payments"
    assert top.vector_rank is not None and top.keyword_rank is not None
    expected = 1 / (60 + top.vector_rank) + 1 / (60 + top.keyword_rank)
    assert abs(top.score - expected) < 1e-9
    assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)


async def test_keyword_arm_uses_or_and_stemming(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    [vec] = await fake_embedder.embed(["zzz unrelated"], "query")
    hits = await hybrid_search(conn, vec, "pets allowed gym", match_count=20)
    keyword = [h for h in hits if h.keyword_rank]
    assert keyword and keyword[0].section == "5.2 Pets"  # 'pets' and 'gym' match even without 'allowed'


async def test_arabic_keyword_search_and_language_filter(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    [vec] = await fake_embedder.embed(["الشيك المرتجع"], "query")
    hits = await hybrid_search(conn, vec, "رسوم الشيك المرتجع", language="ar", match_count=3)
    assert hits[0].language == "ar" and "500" in hits[0].content
    only_en = await hybrid_search(conn, vec, "returned cheque", only_language="en", match_count=10)
    assert {h.language for h in only_en} == {"en"}


async def test_stopword_only_query_still_returns_vector_results(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    [vec] = await fake_embedder.embed(["the"], "query")
    hits = await hybrid_search(conn, vec, "the and of", match_count=3)
    assert len(hits) == 3 and all(h.keyword_rank is None for h in hits)


async def test_empty_query_returns_nothing(conn, fake_embedder):
    assert await search(conn, fake_embedder, "   ") == []


async def test_translated_keywords_bring_in_the_other_language(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    [vec] = await fake_embedder.embed(["ساعات المسبح"], "query")  # the fake embedder has no cross-language sense
    plain = await hybrid_search(conn, vec, "ما مواعيد عمل المسبح؟", language="ar", match_count=20)
    assert not any(h.section == "1. Swimming pool" for h in plain)
    both = await hybrid_search(conn, vec, "ما مواعيد عمل المسبح؟", language="ar", match_count=20,
                               alt_query="What are the swimming pool opening hours?", alt_language="en")
    pool = next(h for h in both if h.section == "1. Swimming pool")
    assert pool.alt_keyword_rank <= 3 and pool.keyword_rank is None  # found only through the translation


async def test_max_per_document(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    [vec] = await fake_embedder.embed(["rent payments cheques deposit"], "query")
    hits = await hybrid_search(conn, vec, "rent payments cheques deposit", match_count=6, max_per_document=1)
    assert len({h.document_path for h in hits}) == len(hits) >= 3  # one chunk per document
    capped = await hybrid_search(conn, vec, "rent payments cheques deposit", match_count=6, max_per_document=2)
    assert max(sum(h.document_path == d for h in capped) for d in {h.document_path for h in capped}) == 2


class FailingTranslator:
    calls = 0

    async def translate(self, text, target):
        FailingTranslator.calls += 1
        return None


async def test_search_works_when_translation_fails(conn, fake_embedder):
    await ingest(conn, fake_embedder)
    hits = await search(conn, fake_embedder, "returned cheque fee", translator=FailingTranslator())
    assert hits and FailingTranslator.calls == 1 and all(h.alt_keyword_rank is None for h in hits)


class SlowEmbedder:
    def __init__(self, inner, delay=0.3, fail=False):
        self.inner, self.delay, self.fail = inner, delay, fail

    async def embed(self, texts, task, titles=None):
        import asyncio
        await asyncio.sleep(self.delay)
        if self.fail:
            from rag.embeddings import EmbeddingError
            raise EmbeddingError("provider down")
        return await self.inner.embed(texts, task, titles)


class SlowTranslator:
    def __init__(self, delay=0.3):
        self.delay, self.finished = delay, False

    async def translate(self, text, target):
        import asyncio
        await asyncio.sleep(self.delay)
        self.finished = True
        return "رسوم الشيك المرتجع"


async def test_embedding_and_translation_run_at_the_same_time(conn, fake_embedder):
    import time
    await ingest(conn, fake_embedder)
    question = "What does a returned cheque cost?"
    trace, started = {}, time.perf_counter()
    hits = await search(conn, SlowEmbedder(fake_embedder), question, top_k=5, translator=SlowTranslator(), trace=trace)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.55  # one after the other would take at least 0.6 s
    line = {s["step"]: s for s in trace["timeline"]}
    assert abs(line["embed"]["start"] - line["translate"]["start"]) < 50 and line["embed"]["ms"] >= 290
    assert line["search"]["start"] >= max(line["embed"]["start"] + line["embed"]["ms"], line["translate"]["start"] + line["translate"]["ms"])
    assert trace["timings_ms"]["embed_and_translate"] < trace["timings_ms"]["embed"] + trace["timings_ms"]["translate"]
    same = await search(conn, fake_embedder, question, top_k=5, translator=SlowTranslator(0))  # ranking unchanged
    assert [h.chunk_id for h in hits] == [h.chunk_id for h in same] and [h.score for h in hits] == [h.score for h in same]


async def test_a_failed_embedding_waits_for_the_translation_before_raising(conn, fake_embedder):
    from rag.embeddings import EmbeddingError
    translator = SlowTranslator(0.2)
    with pytest.raises(EmbeddingError):
        await search(conn, SlowEmbedder(fake_embedder, delay=0, fail=True), "returned cheque", translator=translator)
    assert translator.finished  # nothing left running in the background
