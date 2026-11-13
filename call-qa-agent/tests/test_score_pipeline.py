"""score_call and the scoring endpoint, with a fake database and a fake LLM."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.llm import LLMError, LLMResult
from app.main import app, get_db, get_deepgram
from app.pipeline import score_call

UTTERANCES = [
    {"speaker": 0, "start": 0.0, "end": 5.0, "text": "Hi, this is Maria Lopez, a licensed agent with Harborline Insurance."},
    {"speaker": 0, "start": 5.5, "end": 9.0, "text": "This call is recorded for quality and compliance purposes."},
    {"speaker": 0, "start": 9.5, "end": 14.0, "text": "Do I have your permission to ask a few questions?"},
]


class FakeDB:
    def __init__(self, utterances=UTTERANCES):
        self.call = {"id": "c1", "status": "scoring", "transcripts": {"utterances": utterances}}
        self.rubrics, self.scores = {}, []

    def get_call(self, call_id):
        return self.call if call_id == "c1" else None

    def update_call(self, call_id, **fields):
        self.call.update(fields)

    def get_rubric(self, slug, version):
        return self.rubrics.get((slug, version))

    def insert_rubric(self, **fields):
        row = {"id": f"r{len(self.rubrics) + 1}", **fields}
        self.rubrics[(fields["slug"], fields["version"])] = row
        return row

    def insert_score(self, **fields):
        self.scores.append(fields)
        return {"id": "s1", **fields}


def fake_llm(items=None, error=None):
    calls = []

    def llm(settings, system, user, schema):
        calls.append({"system": system, "user": user, "schema": schema})
        if error:
            raise error
        return LLMResult({"items": items or []}, "gemini", "gemini-3.8-flash", 1500, 400, 2100, None)

    llm.calls = calls
    return llm


GOOD = [
    {"item_id": "agent_identification", "verdict": "pass", "utterance_index": 0,
     "evidence_quote": "a licensed agent with Harborline Insurance", "reasoning": ""},
    {"item_id": "recording_disclosure", "verdict": "pass", "utterance_index": 1, "evidence_quote": "This call is recorded", "reasoning": ""},
    {"item_id": "consent_to_continue", "verdict": "pass", "utterance_index": 2, "evidence_quote": "Do I have your permission", "reasoning": ""},
    {"item_id": "needs_discovery", "verdict": "fail", "utterance_index": 2, "evidence_quote": "ask a few questions", "reasoning": ""},
    {"item_id": "no_misleading_claims", "verdict": "pass", "utterance_index": 1, "evidence_quote": "for quality and compliance purposes", "reasoning": ""},
    {"item_id": "dnc_handling", "verdict": "na", "utterance_index": -1, "evidence_quote": "", "reasoning": ""},
    {"item_id": "next_steps", "verdict": "fail", "utterance_index": 2, "evidence_quote": "We will email you tomorrow", "reasoning": ""},
]


def test_score_is_stored_with_validated_items():
    db, llm = FakeDB(), fake_llm(GOOD)
    row = score_call(db, Settings(_env_file=None), "c1", "us_insurance", llm=llm)

    assert row is not None and db.call["status"] == "done"
    stored = db.scores[0]
    items = {i["item_id"]: i for i in stored["items"]}
    assert items["recording_disclosure"]["start_time"] == 5.5
    assert items["next_steps"]["verdict"] == "needs_review"  # quote is not in the transcript
    # 10 + 20 + 20 + 20 passed of 95 applicable (dnc_handling is na)
    assert stored["overall_score"] == round(100 * 70 / 90, 2)
    assert stored["passed"] is False  # below the 80 threshold
    assert stored["model"] == "gemini/gemini-3.8-flash" and stored["input_tokens"] == 1500 and stored["latency_ms"] == 2100
    assert stored["rubric_id"] == "r1" and db.rubrics[("us_insurance", 1)]["items"]
    assert "[1] 00:05.5 Speaker 0: This call is recorded" in llm.calls[0]["user"]


def test_rubric_row_is_reused():
    db = FakeDB()
    score_call(db, Settings(_env_file=None), "c1", "us_insurance", llm=fake_llm(GOOD))
    score_call(db, Settings(_env_file=None), "c1", "us_insurance", llm=fake_llm(GOOD))
    assert len(db.rubrics) == 1 and len(db.scores) == 2


@pytest.mark.parametrize("db, llm, message", [
    (FakeDB(utterances=[]), fake_llm(GOOD), "no transcript"),
    (FakeDB(), fake_llm(error=LLMError("provider returned HTTP 503: busy")), "HTTP 503"),
])
def test_failures_are_recorded_on_the_call(db, llm, message):
    assert score_call(db, Settings(_env_file=None), "c1", "us_insurance", llm=llm) is None
    assert db.call["status"] == "failed" and message in db.call["error"] and db.scores == []


@pytest.fixture
def api(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr("app.pipeline.complete_json", fake_llm(GOOD))
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="test", app_passcode="test-passcode")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_deepgram] = lambda: None
    yield TestClient(app, headers={"X-Passcode": "test-passcode"}), db
    app.dependency_overrides.clear()


def test_score_endpoint(api):
    client, db = api
    res = client.post("/calls/c1/score", json={"rubric": "us_insurance"})
    assert res.status_code == 202 and res.json()["rubric"] == "us_insurance"
    assert db.call["status"] == "done" and len(db.scores) == 1


def test_score_endpoint_rejects_bad_requests(api):
    client, db = api
    assert client.post("/calls/c1/score", json={"rubric": "nope"}).status_code == 422
    assert client.post("/calls/missing/score", json={"rubric": "us_insurance"}).status_code == 404
    db.call["transcripts"] = {"utterances": []}
    assert client.post("/calls/c1/score", json={"rubric": "us_insurance"}).status_code == 409
    assert client.post("/calls", data={"url": "https://example.com/a.wav", "rubric": "nope"}).status_code == 422
