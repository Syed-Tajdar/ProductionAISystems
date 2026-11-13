"""The redesigned question page: document chips, example row, loading state, source cards with snippets,
the passage sheet, and the shared look (mark, color-scheme, back link). No database needed."""

from datetime import UTC

from fastapi.testclient import TestClient

from rag.answer import Answer
from rag.api import DOCUMENTS, create_app, snippet
from rag.config import Settings
from rag.demo import AnswerCache, DemoLimiter

PASSAGE = "3.3.1 A returned cheque costs AED 500, plus any bank charges. Replace it within 7 days."


class Stub:
    pool = embedder = llm = object()

    def __init__(self, answer):
        self.answer = answer

    async def close(self):
        pass

    async def ask(self, question, language=None, trace=None):
        return self.answer


def page_client(answer=None, **settings):
    s = Settings(_env_file=None, **settings)
    return TestClient(create_app(s, service=Stub(answer), limiter=DemoLimiter(), cache=AnswerCache()))


CITED = Answer(question="q", answerable=True, text="", language="en",
               statements=[{"text": "A returned cheque costs AED 500.", "citations": [{"n": 1}]}],
               sources=[{"n": 1, "title": "Tenant Handbook", "section": "3.3", "heading_path": "3. Rent > 3.3 Returned",
                         "page": 2, "language": "en", "document": "en/tenant-handbook.pdf", "passage": PASSAGE,
                         "quotes": ["A returned cheque costs AED 500"]}])


def test_snippet():
    assert snippet({"quotes": ["A returned  cheque\ncosts AED 500"], "passage": PASSAGE}) == "A returned cheque costs AED 500"
    assert snippet({"quotes": [], "passage": PASSAGE}, limit=30) == "3.3.1 A returned cheque costs..."
    assert snippet({"quotes": [""], "passage": "Short."}) == "Short."


def test_home_page_shell():
    html = page_client(portfolio_url="https://portfolio.example").get("/").text
    assert '<meta name="color-scheme" content="light dark">' in html
    assert 'type="image/svg+xml"' in html and 'class="mark"' in html
    assert '<a href="https://portfolio.example" class="back">' in html
    assert "<span>Docs Q&amp;A</span>" in html and "<title>Docs Q&amp;A</title>" in html and "Ask the documents" not in html
    assert "<h1>Ask the company documents</h1>" in html
    assert all(f"<span>{title}</span><span class=\"langs\">{langs}</span>" in html for title, langs in DOCUMENTS)
    assert '<div class="example-row">' in html and html.count("data-loading") == 2
    # The waiting state is a template app.js puts into each new question and answer pair.
    assert '<template id="turn-loading">' in html and "Searching the documents and writing a cited answer" in html
    assert '<div class="thread" id="thread">' in html and 'class="turn"' not in html
    assert '<div class="composer">' in html and html.index('class="thread"') < html.index('class="composer"')
    assert "<dialog" not in html  # the passage sheet only comes with sources
    # Theme: set before the stylesheets load; the toggle shows only once the script runs.
    assert html.index("/static/theme.js") < html.index("/static/app.css")
    assert '<button type="button" class="du-theme" data-du-theme hidden aria-label="Dark theme" aria-pressed="false">' in html
    # The examples show on an empty thread; the disclaimer sits in the composer, full sentence and phone line.
    assert '<form method="post" action="/" class="examples" data-loading>' in html
    composer = html[html.index('<div class="composer">'):]
    assert ('<span class="privacy-long">Questions are processed by third-party AI services (Anthropic and Google). '
            "Don't enter personal information.</span>") in composer
    assert '<span class="privacy-short">Answered by Anthropic and Google. No personal information.</span>' in composer
    # The card is open on the first visit, as a collapsible <details>.
    assert '<details class="du-card-fold" open>' in html


def test_answer_page_has_source_cards_and_the_passage_sheet():
    with page_client(CITED) as c:
        html = c.post("/", data={"q": "What does a returned cheque cost?"}).text
    assert '<span class="snippet" dir="auto">A returned cheque costs AED 500</span>' in html
    assert '<a class="cite" href="#src-1" aria-label="source 1">[1]</a>' in html
    assert '<dialog class="sheet" id="passage-sheet" aria-labelledby="sheet-title">' in html
    assert '<form method="dialog">' in html


def test_assets():
    c = page_client()
    js, css = c.get("/static/app.js").text, c.get("/static/app.css").text
    assert "showModal" in js and "form[data-loading]" in js and "DOMParser" in js and "prefixIds" in js
    assert "data-copy-answer" in js and "duFoldCard" in c.get("/static/demo-ui.js").text
    assert "--composer-h" in js and "--composer-h" in css and ".du-js .composer .privacy-short" in css
    assert 'classList.add("du-js")' in c.get("/static/theme.js").text
    assert c.get("/static/theme.js").status_code == 200
    assert ':root[data-theme="dark"]' in css and ':root:not([data-theme="light"])' in css
    base = c.get("/static/demo-base.css").text  # the shared palette and header, loaded before app.css
    assert "color-scheme: only light" in base and "color-scheme: only dark" in base and "header.top" in base
    assert "dialog.sheet" in css and "overflow-x: auto" in css
    assert c.get("/static/icon.svg").status_code == 200


def test_without_script_a_question_posts_and_shows_one_pair():
    with page_client(CITED) as c:
        html = c.post("/", data={"q": "What does a returned cheque cost?"}).text
    turn = html[html.index('<article class="turn"'):html.index("</article>")]
    assert turn.index('class="ask-bubble"') < turn.index('<section class="answer"')
    assert '<button type="button" class="copy-answer" data-copy-answer hidden>Copy</button>' in turn
    assert 'id="passage-sheet"' in turn and 'href="#src-1"' in turn  # served ids keep no prefix
    assert '<details class="du-card-fold">' in html  # folded to its title line after a question
    assert '<form method="post" action="/" class="examples" data-loading hidden>' in html  # examples are for an empty thread
    assert html.index("</article>") < html.index('<div class="composer">')


def test_a_rejected_question_stays_in_the_box():
    s = Settings(_env_file=None)
    limited = TestClient(create_app(s, service=Stub(CITED), limiter=DemoLimiter(daily_cap=0), cache=AnswerCache()))
    html = limited.post("/", data={"q": "Hello?"}).text
    turn = html[html.index('<article class="turn"'):html.index("</article>")]
    assert 'class="ask-bubble"' in turn and 'class="du-state du-limit"' in turn
    assert 'value="Hello?"' in html
    empty = page_client(CITED).post("/", data={"q": "  "})
    assert empty.status_code == 422 and '<div class="banner fail" role="alert">Type a question first.</div>' in empty.text
    assert 'class="turn"' not in empty.text


def refusal(language, text):
    return Answer(question="q", answerable=False, language=language, direction="rtl" if language == "ar" else "ltr",
                  text="The documents do not contain an answer to this question.", not_covered=text)


def test_not_covered_line_has_a_label_in_the_answer_language():
    with page_client(refusal("en", "Whether the company offers car rentals for tenants.")) as c:
        html = c.post("/", data={"q": "Do you offer car rentals?"}).text
    assert ('<p class="not-covered" dir="ltr" lang="en"><span class="nc-label">Not covered:</span> '
            "Whether the company offers car rentals for tenants.</p>") in html
    with page_client(refusal("ar", "تأجير السيارات للمستأجرين.")) as c:
        html = c.post("/", data={"q": "هل تؤجرون السيارات؟"}).text
    assert ('<p class="not-covered" dir="rtl" lang="ar"><span class="nc-label">خارج نطاق المستندات:</span> '
            "تأجير السيارات للمستأجرين.</p>") in html
    with page_client(refusal("en", "")) as c:
        assert "nc-label" not in c.post("/", data={"q": "anything"}).text  # nothing to label


def test_citation_markers_stay_with_the_last_word():
    two = Answer(question="q", answerable=True, text="", language="ar", direction="rtl",
                 statements=[{"text": "رسوم الشيك المرتجع 500 درهم. ", "citations": [{"n": 1}, {"n": 2}]},
                             {"text": "Short", "citations": [{"n": 1}]}],
                 sources=CITED.sources)
    with page_client(two) as c:
        html = c.post("/", data={"q": "cheque?"}).text
    assert ('<span class="statement">رسوم الشيك المرتجع 500 <span class="tail">درهم.'
            '<a class="cite" href="#src-1" aria-label="source 1">[1]</a>'
            '<a class="cite" href="#src-2" aria-label="source 2">[2]</a></span></span>') in html
    assert '<span class="statement"><span class="tail">Short<a class="cite"' in html
    assert ".statement .tail { white-space: nowrap; }" in c.get("/static/app.css").text


def test_shared_demo_ui_and_width_classes():
    from rag.api import width_class
    assert [width_class(v, 150) for v in (0, 1, 75, 149, 150)] == ["w-0", "w-0", "w-50", "w-100", "w-100"]
    assert width_class(3, 0) == "w-0"
    c = page_client(portfolio_url="https://portfolio.example")
    html = c.get("/").text
    assert 'data-du-card="rag"' in html and html.count("<li>", html.index("du-points")) >= 3
    assert "<span>Built by Syed Tajdar</span>" in html and '<a href="https://portfolio.example/#contact">Contact</a>' in html
    assert ' style="' not in html  # the CSP allows no inline styles
    css = c.get("/static/app.css").text
    assert ".w-55 { width: 55%; }" in css and ".meter-fill" in css
    assert c.get("/static/demo-ui.js").status_code == 200 and c.get("/static/demo-ui.css").status_code == 200


def test_errors_use_the_shared_states():
    from rag.service import ServiceError

    class Down(Stub):
        async def ask(self, question, language=None, trace=None):
            raise ServiceError("database unavailable")

    s = Settings(_env_file=None)
    c = TestClient(create_app(s, service=Down(None), limiter=DemoLimiter(), cache=AnswerCache()))
    page = c.post("/", data={"q": "Hello?"})
    assert page.status_code == 503 and '<div class="du-state du-error" role="alert">' in page.text
    assert "database unavailable" not in page.text
    limited = TestClient(create_app(s, service=Stub(CITED), limiter=DemoLimiter(daily_cap=0), cache=AnswerCache()))
    page = limited.post("/", data={"q": "Hello?"})
    assert page.status_code == 429 and '<div class="du-state du-limit" role="status">' in page.text


def test_documents_page_states_and_ago():
    from datetime import datetime, timedelta

    from rag.api import ago
    from rag.service import ServiceError
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    assert [ago(now - timedelta(seconds=s), now) for s in (5, 60, 7200, 3 * 86400)] == \
        ["just now", "1 minute ago", "2 hours ago", "3 days ago"]

    class Empty(Stub):
        async def document_overview(self):
            return []

    class Down(Stub):
        async def document_overview(self):
            raise ServiceError("DATABASE_URL is not set")

    s = Settings(_env_file=None)
    empty = TestClient(create_app(s, service=Empty(None), limiter=DemoLimiter(), cache=AnswerCache())).get("/documents")
    assert empty.status_code == 200 and "No documents indexed yet" in empty.text
    down = TestClient(create_app(s, service=Down(None), limiter=DemoLimiter(), cache=AnswerCache())).get("/documents")
    assert down.status_code == 503 and '<div class="du-state du-error" role="alert">' in down.text
    assert "DATABASE_URL" not in down.text and down.headers["content-security-policy"].startswith("default-src 'self'")


def test_use_with_mcp_panel_reads_the_tools_from_the_server():
    import html as html_lib
    import json
    import re

    public = page_client(mcp_public=True).get("/", headers={"host": "docs.example", "x-forwarded-proto": "https"}).text
    assert '<details class="mcp" id="mcp">' in public and "<code>https://docs.example/mcp</code>" in public
    config = json.loads(html_lib.unescape(re.search(r'<pre id="mcp-config"><code>(.*?)</code></pre>', public, re.S)[1]))
    assert config == {"mcpServers": {"palm-grove-docs": {"type": "http", "url": "https://docs.example/mcp"}}}
    names = re.findall(r'<li><code>(\w+)</code><span class="tool-title">', public)
    assert sorted(names) == ["ask", "list_documents", "search_documents"]  # from the server, not a fixed list
    assert '<code title="What to look for, in English or Arabic">query</code>' in public and ">top_k?</code>" in public
    assert 'data-copy="mcp-config" hidden' in public  # the copy button needs the script
    private = page_client(mcp_bearer_token="t" * 32).get("/").text
    off = page_client().get("/").text
    assert 'class="mcp"' not in private and 'class="mcp"' not in off


def test_timeline_marks_parallel_steps_and_keeps_old_traces_readable():
    from rag.api import timeline_view
    trace = {"timeline": [{"step": "embed", "start": 0, "ms": 300}, {"step": "translate", "start": 2, "ms": 900},
                          {"step": "search", "start": 905, "ms": 95}, {"step": "answer", "start": 1000, "ms": 1000},
                          {"step": "quote_check", "start": 2000, "ms": 0}]}
    steps = timeline_view(trace)
    assert [(s["label"], s["parallel"]) for s in steps] == [
        ("Embed the question", True), ("Translate for keyword search", True), ("Hybrid search", False),
        ("Write the cited answer", False), ("Check every quote", False)]
    assert [(s["offset"], s["width"]) for s in steps] == [("o-0", "w-15"), ("o-0", "w-45"), ("o-45", "w-5"),
                                                          ("o-50", "w-50"), ("o-100", "w-0")]
    old = timeline_view({"timings_ms": {"embed": 100, "translate": 50, "search": 20, "answer": 200}})
    assert [(s["width"], s["parallel"]) for s in old] == [("w-50", False), ("w-25", False), ("w-10", False), ("w-100", False)]


def test_a_quote_used_by_several_statements_is_shown_once():
    from rag.api import group_checks, trace_view
    q = "A returned cheque costs AED 500"
    citations = [{"statement": 0, "source": 1, "quote": q, "ok": True, "reason": "found in the source"},
                 {"statement": 1, "source": 1, "quote": q, "ok": True, "reason": "found in the source"},
                 {"statement": 1, "source": 2, "quote": q, "ok": True, "reason": "found in the source"},
                 {"statement": 2, "source": 1, "quote": "made up", "ok": False, "reason": "quote not in the source"}]
    grouped = group_checks(citations, {1: {"title": "Tenant Handbook", "section": "3.3"}})
    assert [(g["source"], g["used_by"], g["statements"]) for g in grouped] == [(1, 2, [0, 1]), (2, 1, [1]), (1, 1, [2])]
    view = trace_view({"answer": {"checks": citations}, "chosen": []}, [])
    assert (view["checks_ok"], len(view["checks"]), view["citations"]) == (2, 3, 4)


def test_answer_page_groups_repeated_quotes():
    import asyncio

    from rag.answer import answer_question
    from tests.test_answer import HITS, FakeLLM, st
    q = "A returned cheque costs AED 500"
    trace = {}
    answer = asyncio.run(answer_question("What does a returned cheque cost?", HITS, FakeLLM(
        {"answerable": True, "not_covered": "", "statements": [st("It costs AED 500.", (1, q)), st("That is the fee.", (1, q))]}),
        trace=trace))
    trace.update(query={"normalized": "What does a returned cheque cost?", "language": "en", "keyword_terms": ["chequ"],
                        "translated": None, "translated_terms": []},
                 settings={"candidates": 20, "rrf_k": 60, "top_k": 6, "max_per_document": 3},
                 candidates={"vector": [], "keyword": [], "translated_keyword": [], "pool": 0},
                 chosen=[{"source": 1, "chunk_id": HITS[0].chunk_id, "title": "Tenant Handbook", "section": "3.3",
                          "vector_rank": 1, "keyword_rank": 1, "translated_keyword_rank": None, "score": 0.0328}])
    html = page_client(answer).post("/", data={"q": "What does a returned cheque cost?"}).text
    assert html.count('<li class="ok"><span class="mark"') == 1 and "used by 2 statements" in html
    assert "<strong>1 of 1</strong> quote was found word for word in the passage it cites (2 citations)" in html


def test_pages_load_vercel_web_analytics_within_the_csp():
    page = page_client().get("/")
    assert '<script defer src="/_vercel/insights/script.js"></script>' in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]  # same origin: allowed
