import re

import pytest
from fastapi.testclient import TestClient

from rag.api import EXAMPLES, create_app, highlight
from rag.config import Settings
from rag.db import PgCache
from rag.demo import AnswerCache, DemoLimiter, message
from rag.embeddings import CachedEmbedder
from rag.ingest import ingest_folder
from rag.service import RagService

from .test_answer import FakeLLM, st
from .test_db import DOCS

pytestmark = pytest.mark.anyio

CHEQUE = {"answerable": True, "not_covered": "", "statements": [
    st("A returned cheque costs AED 500.", (1, "A returned cheque costs AED 500")),
    st("It must be replaced within 7 days.", (1, "must be replaced within 7 days"))]}


class CitingLLM(FakeLLM):
    """Like FakeLLM, but cites whichever retrieved source contains each quote."""

    async def complete_json(self, system, user, schema):
        sources = dict(re.findall(r'<source id="(\d+)".*?">\n(.*?)\n</source>', user, re.S))
        result = await super().complete_json(system, user, schema)
        for statement in result.data.get("statements", []):
            for c in statement["citations"]:
                c["source"] = next((int(n) for n, text in sources.items() if c["quote"] in text), 99)
        return result


@pytest.fixture
async def loaded(conn, fake_embedder):
    await ingest_folder(conn, CachedEmbedder(fake_embedder, PgCache(conn)), DOCS)
    return fake_embedder


class Clock:
    def __init__(self, t=1_800_000_000.0):  # 2027-01-15 08:00 UTC
        self.t = t

    def __call__(self):
        return self.t


def make(database_url, embedder, llm=None, *, per_client=5, daily_cap=150, clock=None):
    clock = clock or Clock()
    settings = Settings(_env_file=None, database_url=database_url, demo_questions_per_client=per_client,
                        demo_daily_cap=daily_cap)
    service = RagService(settings, embedder=embedder, llm=llm or CitingLLM(CHEQUE))
    limiter = DemoLimiter(per_client, 600, daily_cap, clock=clock)
    return TestClient(create_app(settings, service=service, limiter=limiter, cache=AnswerCache(clock=clock))), clock


def ask_page(c, q):
    return c.post("/", data={"q": q})  # every TestClient request comes from the same address


async def test_page_and_static_files(database_url, loaded):
    c, _ = make(database_url, loaded)
    with c:
        res = c.get("/")
        css = c.get("/static/app.css")
        js = c.get("/static/app.js")
    assert res.status_code == 200
    html = res.text
    assert '<input id="q" name="q" type="text" dir="auto"' in html
    assert all(f'value="{e}"' in html for e in EXAMPLES)
    assert "<strong>150</strong>/150 left today" in html and "5 per 10 min each" in html  # the limits meter
    assert 'aria-valuenow="150"' in html and 'class="meter-fill w-100"' in html
    assert res.headers["content-security-policy"].startswith("default-src 'self'")
    assert res.headers["x-frame-options"] == "DENY"
    assert css.status_code == 200 and "margin-inline-start" in css.text
    assert "--accent: #0f6e66" in c.get("/static/demo-base.css").text  # the shared palette, loaded first
    assert js.status_code == 200 and "a.cite" in js.text


async def test_english_answer_with_expandable_citations(database_url, loaded):
    c, _ = make(database_url, loaded)
    with c:
        html = ask_page(c, "What does a returned cheque cost?").text
    assert '<div class="answer-text" dir="ltr" lang="en">' in html
    assert re.search(r'A returned cheque costs AED <span class="tail">500\.<a class="cite" href="#src-1" aria-label="source 1">\[1\]</a></span>', html)
    assert '<details class="source" id="src-1">' in html
    assert "<strong>Tenant Handbook</strong>, 3. Rent and payments &gt; 3.3 Late and returned payments, page 2" in html
    assert "<mark>A returned cheque costs AED 500</mark>" in html  # the quote highlighted in its passage
    assert "<mark>must be replaced within 7 days</mark>" in html
    assert "en/tenant-handbook.pdf" in html
    # The question shows as the bubble above its answer; the box is empty for the next one.
    assert '<p class="ask-bubble" dir="auto"><span class="sr-only">Question: </span>What does a returned cheque cost?</p>' in html
    assert 'value="What does a returned cheque cost?"' not in html


async def test_arabic_answer_is_right_to_left(database_url, loaded):
    llm = CitingLLM({"answerable": True, "not_covered": "", "statements": [
        st("رسوم الشيك المرتجع 500 درهم.", (1, "رسوم الشيك المرتجع 500 درهم"))]})
    c, _ = make(database_url, loaded, llm)
    with c:
        html = ask_page(c, "كم رسوم الشيك المرتجع؟").text
    assert '<div class="answer-text" dir="rtl" lang="ar">' in html
    assert '<div class="passage" dir="rtl" lang="ar">' in html
    assert "‏" not in html and "‫" not in html


async def test_not_in_the_documents(database_url, loaded):
    llm = FakeLLM({"answerable": False, "statements": [], "not_covered": "Nothing about car rentals."})
    c, _ = make(database_url, loaded, llm)
    with c:
        html = ask_page(c, "Do you offer car rentals for tenants?").text
    assert '<div class="banner review" dir="ltr" lang="en">The documents do not contain an answer to this question.</div>' in html
    assert "Nothing about car rentals." in html and '<details class="source"' not in html  # no source cards
    assert '<details class="trace" id="trace">' in html and "The model cited no passages." in html  # but how it got there


async def test_question_text_is_escaped(database_url, loaded):
    c, _ = make(database_url, loaded)
    with c:
        html = ask_page(c, '<script>alert(1)</script>').text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


async def test_empty_question(database_url, loaded):
    c, _ = make(database_url, loaded)
    with c:
        res = c.post("/", data={"q": "   "})
    assert res.status_code == 422 and "Type a question first." in res.text


async def test_per_client_limit_and_cache(database_url, loaded):
    llm = CitingLLM(CHEQUE)
    c, clock = make(database_url, loaded, llm, per_client=2)
    with c:
        assert ask_page(c, "returned cheque fee").status_code == 200
        assert ask_page(c, "returned cheque replacement").status_code == 200
        blocked = ask_page(c, "returned cheque again")
        again = ask_page(c, "Returned  CHEQUE fee")  # same question, from the cache: no limit, no model call
        api = c.post("/ask", json={"question": "returned cheque deadline"})
        clock.t += 601  # the window has passed
        later = ask_page(c, "returned cheque penalty")
    assert blocked.status_code == 429 and "Too many questions from your address. Try again in about 10 minutes." in blocked.text
    assert again.status_code == 200 and 'A returned cheque costs AED <span class="tail">500.' in again.text
    assert api.status_code == 429 and api.headers["retry-after"] == "601" and api.json()["limit"] == "client"
    assert later.status_code == 200
    assert len(llm.calls) == 3  # first, second, fifth


async def test_daily_cap_applies_to_everyone(database_url, loaded):
    c, clock = make(database_url, loaded, daily_cap=2)
    with c:
        c.post("/ask", json={"question": "a"})
        c.post("/search", json={"query": "b"})
        res = c.post("/ask", json={"question": "c"})
        page = c.get("/").text
        clock.t += 86400  # next UTC day
        tomorrow = c.post("/ask", json={"question": "d"})
    assert res.status_code == 429 and res.json()["limit"] == "daily"
    assert "<strong>0</strong>/" in page and "left today" in page
    assert tomorrow.status_code == 200


async def test_json_api(database_url, loaded):
    c, _ = make(database_url, loaded)
    with c:
        health = c.get("/health").json()
        res = c.post("/search", json={"query": "returned cheque fee", "top_k": 3}).json()
        ans = c.post("/ask", json={"question": "What does a returned cheque cost?"}).json()
        bad = c.post("/ask", json={"question": ""})
    assert health["status"] == "ok" and health["questions_left_today"] == 150
    assert len(res["results"]) == 3 and res["results"][0]["section"] == "3.3 Late and returned payments"
    assert ans["answerable"] and ans["text"] == "A returned cheque costs AED 500. [1] It must be replaced within 7 days. [1]"
    assert ans["sources"][0]["passage"].startswith("3.3.1 A returned cheque costs AED 500")
    assert bad.status_code == 422


async def test_provider_down_shows_a_plain_message():
    settings = Settings(_env_file=None)  # no database, no keys
    with TestClient(create_app(settings)) as c:
        page = c.post("/", data={"q": "anything"})
        api = c.post("/ask", json={"question": "anything"})
    assert page.status_code == 503 and "not available right now" in page.text
    assert api.status_code == 503


async def test_api_errors_are_generic_and_the_detail_is_logged(caplog):
    from rag.service import UNAVAILABLE, ServiceError

    class Down(RagService):
        async def search(self, *args, **kwargs):
            raise ServiceError("embedding failed: HTTP 403 from https://generativelanguage.googleapis.com/v1beta key=abc123")

    settings = Settings(_env_file=None)
    with caplog.at_level("WARNING", logger="rag.service"), TestClient(create_app(settings, service=Down(settings))) as c:
        search = c.post("/search", json={"query": "returned cheque"})
        ask = c.post("/ask", json={"question": "What does a returned cheque cost?"})
        page = c.post("/", data={"q": "What does a returned cheque cost?"})
    for res in (search, ask):
        assert res.status_code == 503 and res.json() == {"detail": UNAVAILABLE}
    assert page.status_code == 503
    for body in (search.text, ask.text, page.text):
        assert "googleapis" not in body and "abc123" not in body and "embedding failed" not in body
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "POST /search failed: embedding failed: HTTP 403" in logged and "POST /ask failed" in logged


def test_highlight():
    parts = highlight("Rent is paid in advance. A returned cheque costs AED 500.", ["A returned cheque costs AED 500", "missing"])
    assert parts == [("Rent is paid in advance. ", False), ("A returned cheque costs AED 500", True), (".", False)]
    assert highlight("abc", []) == [("abc", False)]


def test_limiter_and_messages():
    clock = Clock()
    lim = DemoLimiter(per_client=2, window=600, daily_cap=3, clock=clock)
    for _ in range(2):
        assert lim.check("a").allowed
        lim.record("a")
    v = lim.check("a")
    assert (v.allowed, v.reason, v.retry_after) == (False, "client", 601)
    assert lim.check("b").allowed
    lim.record("b")
    v = lim.check("c")
    assert (v.allowed, v.reason) == (False, "daily") and v.retry_after == 16 * 3600 + 1  # until 00:00 UTC
    assert message(v) == "The demo has reached its question limit for today. Try again in about 16 hours."
    clock.t += 16 * 3600 + 1
    assert lim.check("c").allowed and lim.remaining_today() == 3


def test_cache_expires_and_evicts():
    clock = Clock()
    cache = AnswerCache(size=2, ttl=100, clock=clock)
    cache.put("Q one", 1)
    cache.put("q two", 2)
    assert cache.get("  q   ONE ") == 1
    cache.put("q three", 3)  # evicts the least recently used: "q two"
    assert cache.get("q two") is None and cache.get("q three") == 3
    clock.t += 101
    assert cache.get("q one") is None


async def test_documents_page_lists_every_document_with_its_sections(database_url, loaded, conn):
    c, _ = make(database_url, loaded)
    with c:
        res = c.get("/documents")
    assert res.status_code == 200 and "<title>Documents | Docs Q&amp;A</title>" in res.text
    cur = await conn.execute("select d.path, count(distinct c.section) as n from documents d join chunks c on c.document_id = d.id "
                             "group by d.path")
    sections = {r["path"]: r["n"] for r in await cur.fetchall()}
    assert res.text.count('class="doc-card"') == len(sections) == 8
    assert f"8 documents &middot; {sum(sections.values())} sections" in res.text
    assert '<p class="doc-path mono">en/tenant-handbook.pdf</p>' in res.text
    assert f'<div><dt>Sections</dt><dd>{sections["en/tenant-handbook.pdf"]}</dd></div>' in res.text
    assert '<span class="lang-tag" lang="ar">Arabic</span>' in res.text and "just now" in res.text
    assert 'aria-current="page">Documents</a>' in res.text


async def test_trace_is_returned_only_when_asked_and_matches_the_answer(database_url, loaded):
    c, _ = make(database_url, loaded)
    q = {"question": "What does a returned cheque cost?"}
    with c:
        plain = c.post("/ask", json=q).json()
        traced = c.post("/ask?trace=1", json=q).json()  # the same question: answered from the cache, trace kept
    assert "trace" not in plain  # normal responses stay small
    t = traced["trace"]
    assert t["from_cache"] is True and {k: v for k, v in traced.items() if k != "trace"} == plain
    assert t["query"]["normalized"] == q["question"] and t["query"]["language"] == "en"
    assert {"return", "chequ", "cost"} <= set(t["query"]["keyword_terms"])  # stemmed, as the keyword arm searches
    vector, keyword = t["candidates"]["vector"], t["candidates"]["keyword"]
    assert [r["rank"] for r in vector] == list(range(1, len(vector) + 1)) and len(vector) <= t["settings"]["candidates"]
    assert keyword and keyword[0]["section"].startswith("3.")  # the keyword winner is in the rent and payments chapter
    chosen = t["chosen"]
    assert len(chosen) == t["settings"]["top_k"] and chosen[0]["section"] == "3.3 Late and returned payments"
    k = t["settings"]["rrf_k"]
    for c_ in chosen:  # fused score = sum of 1 / (k + rank) over the lists the passage is in
        ranks = [c_[r] for r in ("vector_rank", "keyword_rank", "translated_keyword_rank") if c_[r]]
        assert abs(c_["score"] - sum(1 / (k + r) for r in ranks)) < 1e-6
    checks = t["answer"]["checks"]
    assert [x["ok"] for x in checks] == [True, True] and sum(x["ok"] for x in checks) == plain["citations_valid"]
    assert set(t["timings_ms"]) >= {"embed", "translate", "search", "answer", "quote_check", "total", "trace"}


async def test_trace_does_not_change_search_results(conn, loaded):
    from rag.db import PgCache
    from rag.embeddings import CachedEmbedder
    from rag.retrieval import search
    embedder = CachedEmbedder(loaded, PgCache(conn))
    for question in ("What does a returned cheque cost?", "رسوم الشيك المرتجع", "pets allowed gym"):
        trace = {}
        assert await search(conn, embedder, question, top_k=6, trace=trace) == await search(conn, embedder, question, top_k=6)
        assert [c["chunk_id"] for c in trace["chosen"]] == [h.chunk_id for h in await search(conn, embedder, question, top_k=6)]


async def test_answer_page_shows_how_the_answer_was_built(database_url, loaded):
    c, _ = make(database_url, loaded)
    with c:
        html = ask_page(c, "What does a returned cheque cost?").text
        again = ask_page(c, "What does a returned cheque cost?").text
    assert '<details class="trace" id="trace">' in html and "<summary>How this answer was built</summary>" in html
    assert "<strong>2 of 2</strong> quotes were found word for word" in html
    assert '<span class="cited-as">[1]</span>' in html and "Answered from the cache" not in html
    assert "Answered from the cache" in again
    assert ' style="' not in html  # bars use width classes: the page CSP allows no inline styles
