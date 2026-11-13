"""Run the question set through retrieval and answering, grade the answers, print the tables.

    DATABASE_URL=... GEMINI_API_KEY=... ANTHROPIC_API_KEY=... python -m evals.run
    python -m evals.run en-01 x-ar-01          # only these questions

Ingests documents/ first (unchanged files are skipped) and embeds all questions in one cached
request, so a repeated run makes no embedding calls. Answers come from ANSWER_MODEL; the judge
is a different, stronger model (JUDGE_MODEL, default claude-opus-5-5). Writes
evals/results/latest.json and evals/results/manual_check.md (a sample to check by hand); both
are committed with the results in the README. Stops if the LLM provider is overloaded.
"""

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.judge import judge_answer  # noqa: E402
from evals.metrics import cost, markdown, retrieval, summarize  # noqa: E402
from rag.answer import AnswerError, ClaudeLLM, answer_question  # noqa: E402
from rag.config import Settings  # noqa: E402
from rag.db import PgCache, connect  # noqa: E402
from rag.documents import detect_language  # noqa: E402
from rag.embeddings import CachedEmbedder, make_embedder  # noqa: E402
from rag.ingest import ingest_folder  # noqa: E402
from rag.retrieval import make_translator, search  # noqa: E402

QUESTIONS = ROOT / "evals" / "questions.jsonl"
RESULTS = ROOT / "evals" / "results"
UNAVAILABLE = re.compile(r"HTTP (502|503|529)|overloaded", re.I)


def load_questions(ids: list[str] | None = None) -> list[dict]:
    qs = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [q for q in qs if not ids or q["id"] in ids]


async def run_eval(conn, embedder, llm, judge, questions: list[dict], *, top_k: int = 6, translator=None,
                   max_per_document: int | None = 3, log=print) -> dict:
    """embedder must be cached (CachedEmbedder); llm answers, judge grades."""
    await embedder.embed([q["question"] for q in questions], "query")  # one batch; search reads the cache
    results, stopped = [], None
    for q in questions:
        started = time.monotonic()
        used = (getattr(translator, "input_tokens", 0), getattr(translator, "output_tokens", 0))
        hits = await search(conn, embedder, q["question"], top_k=top_k, translator=translator, max_per_document=max_per_document)
        try:
            a = await answer_question(q["question"], hits, llm)
        except AnswerError as e:
            if UNAVAILABLE.search(str(e)):
                stopped = f"{q['id']}: {e}"
                log(f"stopped: {stopped}")
                break
            raise
        latency = time.monotonic() - started
        t_in = getattr(translator, "input_tokens", 0) - used[0]
        t_out = getattr(translator, "output_tokens", 0) - used[1]
        t_cost = (cost(getattr(translator, "model", None), t_in, t_out) or 0.0) if (t_in or t_out) else 0.0
        r = {"id": q["id"], "category": q["category"], "question": q["question"], "facts": q["facts"],
             "language": detect_language(q["question"]), "answerable": a.answerable, "answer": a.text,
             "language_match": detect_language(a.text) == detect_language(q["question"]),
             "retrieval": retrieval(hits, q["expected"]), "citations_total": a.citations_total,
             "citations_valid": a.citations_valid, "dropped": a.dropped, "not_covered": a.not_covered,
             "statements": a.statements, "sources": a.sources, "model": a.model, "latency_s": round(latency, 2),
             "cost_usd": ((cost(a.model, a.input_tokens, a.output_tokens) or 0.0) if a.model else 0.0) + t_cost,
             "translation_cost_usd": t_cost,
             "retrieved": [{"document": h.document_path, "section": h.heading_path or h.section, "score": h.score} for h in hits[:5]]}
        if a.answerable and a.statements:
            by_chunk = {h.chunk_id: h for h in hits}
            texts = {s["n"]: f"{s['label']}\n{by_chunk[s['chunk_id']].content}" for s in a.sources}
            j = await judge_answer(judge, q["question"], a.statements, texts, q["facts"])
            j["cost_usd"] = cost(j["model"], j["input_tokens"], j["output_tokens"])
            r["judge"] = j
            r["cited_text"] = texts
        results.append(r)
        log(f"{q['id']:8} hit={r['retrieval']['hit']!s:5} answered={a.answerable!s:5} "
            f"faith={r.get('judge', {}).get('faithfulness')} {latency:.1f}s")
    return {"results": results, "summary": summarize(results), "stopped": stopped}


def manual_sample(results: list[dict], n: int = 6, seed: int = 7) -> str:
    """A fixed random sample of answered questions, with everything needed to check them by hand."""
    answered = [r for r in results if r.get("judge")]
    sample = sorted(random.Random(seed).sample(answered, min(n, len(answered))), key=lambda r: r["id"])
    out = ["# Manual faithfulness check", "",
           "For each statement, check the cited source text and mark it yourself. The judge's verdict is shown for comparison.", ""]
    for r in sample:
        out += [f"## {r['id']} ({r['category']})", "", f"**Question:** {r['question']}", "", f"**Answer:** {r['answer']}", ""]
        for st, g in zip(r["statements"], r["judge"]["statements"], strict=False):
            cites = ", ".join(f"[{c['n']}] \"{c['quote']}\"" for c in st["citations"])
            out += [f"- {st['text']}", f"  - cites: {cites}", f"  - judge: {g['verdict']} ({g['reason']})",
                    "  - your verdict: [ ] supported  [ ] partial  [ ] unsupported"]
        out += ["", "Cited sources:", ""]
        for n, text in sorted(r["cited_text"].items()):
            out += [f"> [{n}] " + text.replace("\n", "\n> "), ""]
    return "\n".join(out)


async def main(ids: list[str] | None) -> int:
    settings = Settings()
    conn = await connect(settings.database_url.get_secret_value())
    base = make_embedder(settings)
    embedder = CachedEmbedder(base, PgCache(conn))
    report = await ingest_folder(conn, embedder, ROOT / settings.documents_dir)
    print(f"ingest: {len(report.ingested)} ingested, {len(report.skipped)} unchanged, failed: {report.failed or 'none'}")
    key = settings.anthropic_api_key.get_secret_value()
    llm = ClaudeLLM(key, settings.answer_model, settings.answer_effort)
    judge = ClaudeLLM(key, os.environ.get("JUDGE_MODEL", "claude-opus-5-5"), "medium")
    translator = make_translator(settings)
    out = await run_eval(conn, embedder, llm, judge, load_questions(ids), top_k=settings.top_k, translator=translator,
                         max_per_document=settings.max_per_document)
    await conn.close()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    (RESULTS / "manual_check.md").write_text(manual_sample(out["results"]), encoding="utf-8")
    label = (f"answers by {settings.answer_model}, judged by {judge.model}, embeddings {base.model}, "
             f"query translation {settings.translation_model if translator else 'off'}, max {settings.max_per_document} chunks per document")
    print(f"\nembedding requests this run: {base.requests} (cache hits {embedder.stats.hits}, new {embedder.stats.misses})\n")
    print(markdown(out["summary"], out["results"], label))
    if out["stopped"]:
        print(f"\nRun stopped early: {out['stopped']}")
        return 2
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="question ids (default: all)")
    sys.exit(asyncio.run(main(ap.parse_args().ids or None)))
