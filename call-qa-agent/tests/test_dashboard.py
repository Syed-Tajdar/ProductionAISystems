import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dashboard import highlight, outcome, quote_span
from app.main import app, get_db
from app.rubrics import load_rubric

RUBRIC = load_rubric("us_insurance")
UTTERANCES = [
    {"speaker": 0, "start": 0.0, "end": 6.0, "text": "Hey, this is Kevin from Harborline Insurance, and this call is being recorded."},
    {"speaker": 1, "start": 6.5, "end": 8.0, "text": "Uh, why do you need that <script>alert(1)</script>?"},
    {"speaker": 0, "start": 65.2, "end": 72.0, "text": "Everyone gets guaranteed approval, no medical questions."},
]


def item(item_id, verdict, quote="", index=None, start=None, **extra):
    return {"item_id": item_id, "verdict": verdict, "evidence_quote": quote, "utterance_index": index,
            "start_time": start, "reasoning": f"why {item_id}", **extra}


SCORE = {
    "overall_score": 61.11, "passed": None, "model": "gemini/gemini-3.8-flash", "input_tokens": 1500,
    "output_tokens": 400, "latency_ms": 2100, "cost_usd": None, "created_at": "2026-11-06T10:00:00+00:00",
    "rubrics": {"slug": "us_insurance", "name": RUBRIC["name"], "version": 1, "items": RUBRIC["items"]},
    "items": [
        item("agent_identification", "pass", "this is Kevin from Harborline Insurance", 0, 0.0),
        item("recording_disclosure", "pass", "THIS CALL IS BEING RECORDED", 0, 0.0),
        item("no_misleading_claims", "fail", "guaranteed approval no medical questions", 2, 65.2),
        item("dnc_handling", "needs_review", "I'll remove you from our list", None, None,
             model_verdict="pass", review_reason="quote not found in transcript"),
        item("next_steps", "na"),
    ],
}


def call(status="done", scores=(SCORE,), **extra):
    return {"id": "c1", "filename": "call_03.wav", "status": status, "error": None, "duration_sec": 81.4, "language": "en",
            "created_at": "2026-11-06T09:58:00+00:00", "transcripts": {"utterances": UTTERANCES}, "scores": list(scores), **extra}


class FakeDB:
    def __init__(self, calls):
        self.calls = calls
        self.list_args = None

    def list_calls(self, limit, offset, demo_only=False):
        self.list_args = (limit, offset, demo_only)
        calls = [c for c in self.calls if c.get("is_demo")] if demo_only else self.calls
        return calls[offset:offset + limit]

    def get_call(self, call_id):
        return next((c for c in self.calls if c["id"] == call_id), None)


@pytest.fixture
def client():
    def make(calls):
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="test", app_passcode="test-passcode")
        app.dependency_overrides[get_db] = lambda: FakeDB(calls)
        return TestClient(app, headers={"X-Passcode": "test-passcode"})
    yield make
    app.dependency_overrides.clear()


def test_quote_span_and_highlight():
    text = UTTERANCES[0]["text"]
    assert text[slice(*quote_span(text, "harborline insurance and this"))] == "Harborline Insurance, and this"
    assert quote_span(text, "not here") is None and quote_span(text, "...") is None
    assert quote_span("Hello Kevinson", "kevin") is None  # whole words only
    segments = highlight(text, [("a", "this is Kevin"), ("b", "Kevin from Harborline"), ("c", "being recorded")])
    marked = [(s["text"], s["items"]) for s in segments if s["items"]]
    assert marked == [("this is Kevin from Harborline", ["a", "b"]), ("being recorded", ["c"])]
    assert "".join(s["text"] for s in segments) == text
    assert highlight("سنوات الخبرة، خمس سنوات", [("x", "الخبرة خمس")])[1]["text"] == "الخبرة، خمس"


def test_outcome_labels():
    assert outcome(None)["label"] == "Not scored"
    assert outcome({"passed": None})["label"] == "Undecided"
    assert outcome({"passed": True})["css"] == "pass" and outcome({"passed": False})["css"] == "fail"


def test_list_page(client):
    calls = [call(), call(id="c2", filename="call_08.wav", scores=[{**SCORE, "passed": True, "items": []}]),
             call(id="c3", filename="call_10.wav", status="transcribing", scores=[])]
    html = client(calls).get("/dashboard").text
    assert 'href="/dashboard/calls/c1"' in html and '<span class="call-title">Call 08</span>' in html
    assert '<span class="badge review">Undecided</span>' in html
    assert '<span class="badge review">1 to review</span>' in html
    assert '<span class="badge pass">Pass</span>' in html and '<span class="badge muted">Not scored</span>' in html
    assert ">61<" in html and "01:21" in html
    assert 'http-equiv="refresh"' in html  # a call is still being processed


def test_list_page_empty_and_paging(client):
    c = client([])
    assert "No calls yet" in c.get("/dashboard").text
    many = [call(id=f"c{i}", filename=f"f{i}.wav") for i in range(30)]
    c = client(many)
    first, second = c.get("/dashboard").text, c.get("/dashboard?page=2").text
    assert first.count('class="badge review">Undecided') == 25 and "page=2" in first and "Newer" not in first
    assert second.count('class="badge review">Undecided') == 5 and '<a href="/dashboard">&larr; Newer</a>' in second
    assert 'http-equiv="refresh"' not in first
    assert c.get("/").status_code == 200 and c.get("/", follow_redirects=False).headers["location"] == "/dashboard"


def test_detail_page(client):
    html = client([call()]).get("/dashboard/calls/c1").text
    # transcript lines with anchors and timestamps
    assert 'id="u-0"' in html and 'id="u-2"' in html and "<time>01:05</time>" in html
    # items jump to their line; evidence is highlighted in the original wording
    assert 'href="#u-2"' in html
    assert re.search(r'<mark class="fail" title="no_misleading_claims">guaranteed approval, no medical questions</mark>', html)
    assert '<mark class="pass" title="agent_identification, recording_disclosure">' not in html  # not overlapping
    assert '<mark class="pass" title="recording_disclosure">this call is being recorded</mark>' in html
    assert 'class="line cited" id="u-0"' in html and 'class="line" id="u-1"' in html
    # needs_review stands out and does not link to a line
    assert 'class="item needs_review"' in html and "Quote not found in transcript (model said pass)" in html
    assert '<span class="badge review">needs review</span>' in html
    # The reason sits once, in the score card; the separate review banner stays.
    assert "Pass/fail undecided" not in html and "1 item needs review" in html
    assert html.count("A critical item needs review before this call can pass.") == 1
    assert '<span class="verdict-reason">A critical item needs review before this call can pass.</span>' in html
    assert "critical" in html and RUBRIC["items"][0]["title"] in html
    # untrusted transcript text is escaped
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_detail_states(client):
    c = client([call(status="failed", scores=[], error="provider returned HTTP 503: busy"),
                call(id="c2", status="scoring", scores=[])])
    failed = c.get("/dashboard/calls/c1").text
    assert "Failed: provider returned HTTP 503: busy" in failed and "Not scored yet" in failed
    assert 'http-equiv="refresh"' not in failed
    assert 'http-equiv="refresh"' in c.get("/dashboard/calls/c2").text
    assert c.get("/dashboard/calls/nope").status_code == 404


def test_stylesheet_is_served(client):
    res = client([]).get("/static/dashboard.css")
    assert res.status_code == 200 and "--review" in res.text
