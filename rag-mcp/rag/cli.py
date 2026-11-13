"""Command line:

    python -m rag.cli ingest [folder] [--prune] [--force]
    python -m rag.cli search "how long is the notice period?"
    python -m rag.cli ask "how long is the notice period?"
    python -m rag.cli warm [--force]     # answer the page's example questions into the shared cache (rag.warm)
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from rag.answer import ClaudeLLM, answer_question
from rag.config import Settings
from rag.db import PgCache, connect
from rag.embeddings import CachedEmbedder, make_embedder
from rag.ingest import ingest_folder
from rag.retrieval import make_translator, search


async def run_warm(args, settings: Settings) -> int:
    from rag.answer import Answer
    from rag.demo import EXAMPLES, PgAnswerCache
    from rag.service import RagService
    from rag.warm import warm

    service = RagService(settings)
    cache = PgAnswerCache(service.connection, settings.answer_cache_size, settings.answer_cache_seconds,
                          decode=lambda data: Answer(**data))
    try:
        report = await warm(service, cache, EXAMPLES, force=args.force, fresh_hours=args.fresh_hours)
    finally:
        await service.close()
    print(f"answered {len(report.answered)}, still fresh {len(report.fresh)}, failed {len(report.failed)}")
    return 1 if report.failed else 0


async def run(args, settings: Settings) -> int:
    if args.command == "warm":
        return await run_warm(args, settings)
    conn = await connect(settings.database_url.get_secret_value())
    try:
        embedder = CachedEmbedder(make_embedder(settings), PgCache(conn))
        if args.command == "ingest":
            report = await ingest_folder(conn, embedder, Path(args.folder or settings.documents_dir),
                                         max_chars=settings.chunk_max_chars, overlap_chars=settings.chunk_overlap_chars,
                                         prune=args.prune, force=args.force)
            print(json.dumps({**report.to_dict(), "embedded": embedder.stats.misses, "cache_hits": embedder.stats.hits,
                              "api_requests": getattr(embedder.inner, "requests", None)}, indent=2, ensure_ascii=False))
            return 1 if report.failed else 0
        hits = await search(conn, embedder, args.text, top_k=settings.top_k, candidates=settings.search_candidates,
                            rrf_k=settings.rrf_k, translator=make_translator(settings),
                            max_per_document=settings.max_per_document)
        if args.command == "search":
            for h in hits:
                print(f"{h.score:.4f}  v{h.vector_rank or '-':>2} k{h.keyword_rank or '-':>2} t{h.alt_keyword_rank or '-':>2}  "
                      f"{h.document_path}  {h.heading_path}")
            return 0
        llm = ClaudeLLM(settings.anthropic_api_key.get_secret_value(), settings.answer_model, settings.answer_effort)
        answer = await answer_question(args.text, hits, llm)
        print(answer.text)
        for s in answer.sources:
            print(f"  [{s['n']}] {s['label']}")
        if answer.not_covered:
            print(f"  Not covered: {answer.not_covered}")
        return 0
    finally:
        await conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("folder", nargs="?")
    ing.add_argument("--prune", action="store_true", help="delete documents no longer in the folder")
    ing.add_argument("--force", action="store_true", help="re-chunk unchanged files too (embeddings still cached)")
    for name in ("search", "ask"):
        sub.add_parser(name).add_argument("text")
    w = sub.add_parser("warm", help="answer the example questions into the shared answer cache")
    w.add_argument("--force", action="store_true", help="answer all again, even if a fresh answer is cached")
    w.add_argument("--fresh-hours", type=float, default=20, help="keep cached answers younger than this (default 20)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    sys.exit(main())
