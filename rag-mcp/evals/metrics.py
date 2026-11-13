"""Scoring helpers for the eval: section matching, per-question results, summary and markdown."""

import re
from pathlib import Path

from rag.db import Hit

CATEGORIES = ["english", "arabic", "cross_ar_to_en", "cross_en_to_ar", "paraphrase", "table", "multi_document", "out_of_scope"]
PRICES = {  # USD per million input / output tokens, list prices
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    for name, (pin, pout) in PRICES.items():
        if model and model.startswith(name):
            return (input_tokens * pin + output_tokens * pout) / 1e6
    return None


def section_number(section: str) -> str | None:
    m = re.match(r"\s*(\d+(?:\.\d+)*)", section)
    return m.group(1) if m else None


def matches(hit: Hit, expected: str) -> bool:
    """expected is "<document name>#<section number>", e.g. "tenant-handbook#3.3". The English and the
    Arabic copy of a document share the name, so either counts; subsections count for their section."""
    doc, _, number = expected.partition("#")
    if Path(hit.document_path).stem != doc:
        return False
    found = section_number(hit.section) or section_number(hit.heading_path)
    return found is not None and (found == number or found.startswith(number + "."))


def retrieval(hits: list[Hit], expected: list[str], k: int = 5) -> dict:
    top = hits[:k]
    found = {e: next((i + 1 for i, h in enumerate(top) if matches(h, e)), None) for e in expected}
    return {"hit": bool(expected) and all(found.values()), "ranks": found,
            "top1": bool(hits) and any(matches(hits[0], e) for e in expected)}


def ratio(n: float, d: int) -> str:
    return f"{n:g}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def summarize(results: list[dict]) -> dict:
    in_scope = [r for r in results if r["category"] != "out_of_scope"]
    oos = [r for r in results if r["category"] == "out_of_scope"]
    judged = [r for r in in_scope if r.get("judge") and r["judge"]["faithfulness"] is not None]
    statements = [s for r in judged for s in r["judge"]["statements"]]
    facts = [f for r in judged for f in r["judge"]["facts"]] + [
        {"covered": False} for r in in_scope if not r["answerable"] for _ in r["facts"]]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    costs = [r["cost_usd"] for r in results if r.get("cost_usd") is not None]
    judge_costs = [r["judge"]["cost_usd"] for r in judged if r["judge"].get("cost_usd") is not None]
    return {
        "questions": len(results), "in_scope": len(in_scope), "out_of_scope": len(oos),
        "retrieval_hit": ratio(sum(r["retrieval"]["hit"] for r in in_scope), len(in_scope)),
        "top1": ratio(sum(r["retrieval"]["top1"] for r in in_scope), len(in_scope)),
        "faithfulness": f"{100 * mean([r['judge']['faithfulness'] for r in judged]):.0f}%" if judged else "n/a",
        "statements_supported": ratio(sum(s["verdict"] == "supported" for s in statements), len(statements)),
        "fully_faithful": ratio(sum(r["judge"]["faithfulness"] == 1.0 for r in judged), len(judged)),
        "facts_covered": ratio(sum(f["covered"] for f in facts), len(facts)),
        "citation_validity": ratio(sum(r["citations_valid"] for r in results), sum(r["citations_total"] for r in results)),
        "refused_out_of_scope": ratio(sum(not r["answerable"] for r in oos), len(oos)),
        "wrongly_refused": ratio(sum(not r["answerable"] for r in in_scope), len(in_scope)),
        "language_match": ratio(sum(r["language_match"] for r in results), len(results)),
        "latency_s": mean([r["latency_s"] for r in results]),
        "latency_max_s": max((r["latency_s"] for r in results), default=None),
        "cost_usd": mean(costs) if len(costs) == len(results) else None,
        "judge_cost_usd": sum(judge_costs) if judge_costs else None,
    }


def by_category(results: list[dict]) -> list[dict]:
    rows = []
    for cat in CATEGORIES:
        rs = [r for r in results if r["category"] == cat]
        if not rs:
            continue
        judged = [r["judge"]["faithfulness"] for r in rs if r.get("judge") and r["judge"]["faithfulness"] is not None]
        rows.append({
            "category": cat, "n": len(rs),
            "retrieval": "n/a" if cat == "out_of_scope" else ratio(sum(r["retrieval"]["hit"] for r in rs), len(rs)),
            "answered": ratio(sum(r["answerable"] for r in rs), len(rs)),
            "faithfulness": f"{100 * sum(judged) / len(judged):.0f}%" if judged else "n/a",
        })
    return rows


def markdown(summary: dict, results: list[dict], label: str) -> str:
    secs = lambda v: f"{v:.1f} s" if v is not None else "n/a"  # noqa: E731
    money = lambda v: f"${v:.4f}" if v is not None else "n/a"  # noqa: E731
    s = summary
    lines = [f"Eval: {s['questions']} questions ({s['in_scope']} in scope, {s['out_of_scope']} out of scope), {label}", "",
             "| Metric | Result |", "| --- | --- |",
             f"| Retrieval hit rate (every expected section in the top 5) | {s['retrieval_hit']} |",
             f"| Top result from an expected section | {s['top1']} |",
             f"| Answer faithfulness (judge, mean per answer) | {s['faithfulness']} |",
             f"| Statements fully supported by their sources | {s['statements_supported']} |",
             f"| Answers with every statement supported | {s['fully_faithful']} |",
             f"| Key facts covered | {s['facts_covered']} |",
             f"| Citation validity (quote found in the cited chunk) | {s['citation_validity']} |",
             f"| Out-of-scope questions refused | {s['refused_out_of_scope']} |",
             f"| In-scope questions wrongly refused | {s['wrongly_refused']} |",
             f"| Answer in the question's language | {s['language_match']} |",
             f"| Avg latency per question (max) | {secs(s['latency_s'])} ({secs(s['latency_max_s'])}) |",
             f"| Avg cost per question (translation + answer) | {money(s['cost_usd'])} |",
             f"| Judge cost for the run | {money(s['judge_cost_usd'])} |",
             "", "| Category | Questions | Retrieval hit | Answered | Faithfulness |", "| --- | --- | --- | --- | --- |"]
    lines += [f"| {r['category']} | {r['n']} | {r['retrieval']} | {r['answered']} | {r['faithfulness']} |" for r in by_category(results)]
    lines += ["", "| Question | Category | Top 5 hit | Answered | Faithfulness | Facts | Latency |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        j = r.get("judge")
        faith = f"{100 * j['faithfulness']:.0f}%" if j and j["faithfulness"] is not None else "-"
        facts = f"{sum(f['covered'] for f in j['facts'])}/{len(j['facts'])}" if j and j["facts"] else "-"
        hit = "-" if r["category"] == "out_of_scope" else ("yes" if r["retrieval"]["hit"] else "no")
        lines.append(f"| {r['id']} | {r['category']} | {hit} | {'yes' if r['answerable'] else 'no'} | {faith} | {facts} | {r['latency_s']:.1f} s |")
    return "\n".join(lines)
