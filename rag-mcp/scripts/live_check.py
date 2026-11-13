"""Live check against a local pgvector database: real Gemini embeddings and real Claude answers.

    docker run -d --name rag-live -e POSTGRES_PASSWORD=live -p 55433:5432 pgvector/pgvector:pg17
    DATABASE_URL=postgresql://postgres:live@localhost:55433/postgres GEMINI_API_KEY=<key> \
      ANTHROPIC_API_KEY=<key> python scripts/live_check.py --setup

--setup applies the migrations, and is refused for anything but a local database. The run
ingests documents/ twice (the second time forced, to show every embedding coming from the
cache) and asks a few English questions, including one the documents cannot answer.
Synthetic documents only. Prints results, never keys.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.answer import ClaudeLLM, answer_question  # noqa: E402
from rag.config import Settings  # noqa: E402
from rag.db import PgCache, connect  # noqa: E402
from rag.embeddings import CachedEmbedder, make_embedder  # noqa: E402
from rag.ingest import ingest_folder  # noqa: E402
from rag.retrieval import make_translator, search  # noqa: E402

QUESTIONS = [
    "What is the fee for a returned cheque, and how long do I have to replace it?",
    "How quickly will maintenance respond to a water leak?",
    "How many days of annual leave do employees get, and can unused days be carried over?",
    "What is the agency fee for a new tenancy?",
    "Can I keep a dog in my apartment, and is there a pet deposit?",
    "Does Palm Grove Realty offer car rentals for tenants?",
]
PRICE = {"claude-sonnet-5-5": (2.00, 10.00)}  # USD per million input / output tokens


async def main(setup: bool) -> int:
    settings = Settings()
    dsn = settings.database_url.get_secret_value()
    if setup:
        if urlparse(dsn).hostname not in ("localhost", "127.0.0.1"):
            print("--setup only runs against a local database")
            return 2
        conn = await connect(dsn)
        for path in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
        await conn.close()

    conn = await connect(dsn)
    base = make_embedder(settings)
    for label, force in (("first ingest", False), ("forced re-ingest", True)):
        cached = CachedEmbedder(base, PgCache(conn))
        before = base.requests
        started = time.monotonic()
        report = await ingest_folder(conn, cached, ROOT / "documents", force=force)
        print(f"{label}: {len(report.ingested)} documents, {report.chunks} chunks, {cached.stats.misses} embedded, "
              f"{cached.stats.hits} from cache, {base.requests - before} Gemini requests, "
              f"{time.monotonic() - started:.1f} s, failed: {report.failed or 'none'}")

    llm = ClaudeLLM(settings.anthropic_api_key.get_secret_value(), settings.answer_model, settings.answer_effort)
    total_cost = 0.0
    translator = make_translator(settings)
    for q in QUESTIONS:
        started = time.monotonic()
        hits = await search(conn, CachedEmbedder(base, PgCache(conn)), q, top_k=settings.top_k, translator=translator,
                            max_per_document=settings.max_per_document)
        a = await answer_question(q, hits, llm)
        price = PRICE.get(a.model or "", (0, 0))
        cost = (a.input_tokens * price[0] + a.output_tokens * price[1]) / 1e6
        total_cost += cost
        print(f"\nQ: {q}\nA: {a.text}")
        for s in a.sources:
            print(f"   [{s['n']}] {s['label']}")
        if a.not_covered:
            print(f"   not covered: {a.not_covered}")
        if a.dropped:
            print(f"   dropped (no valid citation): {[d['text'] for d in a.dropped]}")
        top = hits[0] if hits else None
        print(f"   top hit: {top.document_path} {top.heading_path} (v{top.vector_rank} k{top.keyword_rank})" if top else "   no hits")
        print(f"   {a.model}, {a.input_tokens} in / {a.output_tokens} out tokens, ${cost:.4f}, "
              f"{time.monotonic() - started:.1f} s total ({a.latency_ms} ms model)")
    print(f"\nanswer cost for {len(QUESTIONS)} questions: ${total_cost:.4f}")
    await conn.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", action="store_true", help="apply the migrations first (local database only)")
    sys.exit(asyncio.run(main(ap.parse_args().setup)))
