import re
from collections import Counter
from pathlib import Path

import pytest

from evals.judge import judge_answer, judge_prompt
from evals.metrics import cost, markdown, matches, retrieval, summarize
from evals.run import load_questions, manual_sample, run_eval
from rag.answer import LLMResult
from rag.chunking import chunk_document
from rag.db import Hit, PgCache
from rag.documents import parse_file, scan
from rag.embeddings import CachedEmbedder
from rag.ingest import ingest_folder

from .test_answer import FakeLLM

DOCS = Path(__file__).resolve().parents[1] / "documents"


def hit(path, section, heading_path=""):
    return Hit(1, path, "T", section, heading_path or section, "en", None, None, "x", 1, 1, None, 0.5, 0.03)


def test_section_matching():
    assert matches(hit("en/tenant-handbook.pdf", "3.3 Late and returned payments"), "tenant-handbook#3.3")
    assert matches(hit("ar/tenant-handbook.md", "3.3 الدفعات المتأخرة"), "tenant-handbook#3.3")  # either language
    assert matches(hit("en/leave-policy.docx", "4. Leave entitlements"), "leave-policy#4")
    assert matches(hit("en/tenant-handbook.pdf", "3.3.2 x"), "tenant-handbook#3.3")  # subsection counts
    assert not matches(hit("en/tenant-handbook.pdf", "3.30 x"), "tenant-handbook#3.3")
    assert not matches(hit("en/service-faq.md", "3.3 x"), "tenant-handbook#3.3")
    assert not matches(hit("en/tenant-handbook.pdf", "Tenant Handbook"), "tenant-handbook#1")


def test_retrieval_needs_every_expected_section():
    hits = [hit("en/service-faq.md", "2.3 Fee"), hit("x.md", "1 a"), hit("en/tenant-handbook.pdf", "2.3 Registration")]
    r = retrieval(hits, ["service-faq#2.3", "tenant-handbook#2.3"])
    assert r == {"hit": True, "ranks": {"service-faq#2.3": 1, "tenant-handbook#2.3": 3}, "top1": True}
    assert not retrieval(hits, ["service-faq#2.3", "leave-policy#5"])["hit"]
    assert not retrieval(hits[1:] + [hit("x.md", "9")] * 3 + hits[:1], ["service-faq#2.3"])["hit"]  # rank 6 is outside the top 5


def test_question_set():
    qs = load_questions()
    assert len(qs) == 32 and len({q["id"] for q in qs}) == 32
    assert Counter(q["category"] for q in qs) == {"english": 5, "arabic": 5, "cross_ar_to_en": 3, "cross_en_to_ar": 3,
                                                   "paraphrase": 4, "table": 4, "multi_document": 3, "out_of_scope": 5}
    chunks = [(p, c) for p in scan(DOCS) for c in chunk_document(parse_file(p, DOCS))]
    for q in qs:
        assert (q["category"] == "out_of_scope") == (not q["expected"]), q["id"]
        for e in q["expected"]:
            found = [p for p, c in chunks if matches(hit(str(p.relative_to(DOCS)), c.section, c.heading_path), e)]
            assert found, f"{q['id']}: no chunk for {e}"
    # cross-language questions point at documents that exist in only the other language
    langs = {q["id"]: re.search(r"[؀-ۿ]", q["question"]) is not None for q in qs}
    assert all(langs[q["id"]] for q in qs if q["category"] == "cross_ar_to_en")
    assert not any(langs[q["id"]] for q in qs if q["category"] == "cross_en_to_ar")
    assert not list(DOCS.glob("ar/amenities-guide.*")) and not list(DOCS.glob("en/travel-expenses-policy.*"))


def test_cost():
    assert cost("claude-sonnet-5-5", 1_000_000, 100_000) == pytest.approx(3.0)
    assert cost("claude-opus-5-5", 1000, 100) == pytest.approx(0.006)
    assert cost("unknown", 1, 1) is None


pytestmark = pytest.mark.anyio


STATEMENTS = [{"text": "Fee is AED 500.", "citations": [{"n": 1, "quote": "costs AED 500"}]},
              {"text": "Replace in 7 days.", "citations": [{"n": 1, "quote": "within 7 days"}]}]


async def test_judge_maps_verdicts_and_facts():
    llm = FakeLLM({"statements": [{"index": 1, "verdict": "supported", "reason": "stated"},
                                  {"index": 2, "verdict": "partial", "reason": "method missing"}],
                   "facts": [{"fact": "AED 500 (paraphrased by the judge)", "covered": True}, {"fact": "?", "covered": False}]})
    j = await judge_answer(llm, "q", STATEMENTS, {1: "A returned cheque costs AED 500 ..."}, ["AED 500", "7 days"])
    assert j["faithfulness"] == 0.75
    assert j["facts"] == [{"fact": "AED 500", "covered": True}, {"fact": "7 days", "covered": False}]  # by position
    prompt = llm.calls[0][1]
    assert "[1] Fee is AED 500." in prompt and '<source n="1">' in prompt and "- 7 days" in prompt


async def test_judge_missing_grades_count_as_unsupported():
    j = await judge_answer(FakeLLM({"statements": [{"index": 1, "verdict": "supported", "reason": ""}], "facts": []}),
                           "q", STATEMENTS, {1: "x"}, [])
    assert j["faithfulness"] == 0.5 and j["statements"][1]["reason"] == "not graded by the judge"


def test_judge_prompt_without_facts():
    assert "Key facts: none given" in judge_prompt("q", STATEMENTS, {1: "x"}, [])


class QuotingLLM:
    """Answers every question by quoting the first six words of source 1; refuses out-of-scope ids."""

    def __init__(self):
        self.calls = 0

    async def complete_json(self, system, user, schema):
        self.calls += 1
        if "chief executive" in user or "car rentals" in user:
            return LLMResult({"answerable": False, "statements": [], "not_covered": "not covered"}, "claude-sonnet-5-5", 500, 20, 900)
        first = re.search(r'<source id="1".*?">\n(.*?)\n</source>', user, re.S).group(1)
        quote = " ".join(first.split()[1:7])
        lang_ar = "in Arabic" in system
        text = "الإجابة من المصدر." if lang_ar else "Answer from the source."
        return LLMResult({"answerable": True, "not_covered": "", "statements": [
            {"text": text, "citations": [{"source": 1, "quote": quote}]}]}, "claude-sonnet-5-5", 1000, 100, 1200)


async def test_run_eval_end_to_end_with_fakes(conn, fake_embedder):
    cached = CachedEmbedder(fake_embedder, PgCache(conn))
    await ingest_folder(conn, cached, DOCS)
    judge = FakeLLM({"statements": [{"index": 1, "verdict": "supported", "reason": "ok"}], "facts": []})
    questions = load_questions(["en-01", "x-ar-01", "m-01", "o-01", "o-05"])
    from rag.retrieval import LLMTranslator

    translator = LLMTranslator(FakeLLM({"translation": "translated query"}))  # 900 in, 120 out tokens per call
    out = await run_eval(conn, cached, QuotingLLM(), judge, questions, translator=translator, log=lambda *_: None)
    assert fake_embedder.calls[-1] == (5, "query")  # all questions embedded in one batch, then read from the cache
    assert out["stopped"] is None and len(out["results"]) == 5
    s = out["summary"]
    assert s["refused_out_of_scope"] == "2/2 (100%)" and s["wrongly_refused"] == "0/3 (0%)"
    assert s["citation_validity"] == "3/3 (100%)" and s["faithfulness"] == "100%"
    assert s["language_match"] == "5/5 (100%)"  # the Arabic question got the Arabic answer text
    answers = 3 * (1000 * 2 + 100 * 10) + 2 * (500 * 2 + 20 * 10)
    translations = 5 * (900 * 2 + 120 * 10)
    assert s["cost_usd"] == pytest.approx((answers + translations) / 1e6 / 5)
    assert all(r["translation_cost_usd"] == pytest.approx(0.003) for r in out["results"])
    md = markdown(s, out["results"], "fake run")
    assert "| Out-of-scope questions refused | 2/2 (100%) |" in md and "| o-05 | out_of_scope | - | no |" in md
    sample = manual_sample(out["results"], n=2)
    assert sample.count("your verdict") == 2 and "Cited sources:" in sample


async def test_run_eval_stops_when_the_provider_is_overloaded(conn, fake_embedder):
    class Overloaded:
        async def complete_json(self, *a):
            from rag.answer import AnswerError
            raise AnswerError("provider returned HTTP 529")

    await ingest_folder(conn, CachedEmbedder(fake_embedder, PgCache(conn)), DOCS)
    out = await run_eval(conn, CachedEmbedder(fake_embedder, PgCache(conn)), Overloaded(), None,
                         load_questions(["en-01", "en-02"]), log=lambda *_: None)
    assert out["stopped"].startswith("en-01") and out["results"] == []


def test_summary_counts_facts_of_wrong_refusals_as_missing():
    r = {"category": "english", "answerable": False, "facts": ["a", "b"], "retrieval": {"hit": True, "top1": True},
         "citations_valid": 0, "citations_total": 0, "language_match": True, "latency_s": 1.0, "cost_usd": 0.0}
    assert summarize([r])["facts_covered"] == "0/2 (0%)"
