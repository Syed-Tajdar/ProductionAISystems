"""PROCESSING_MODE=inline: transcription and scoring finish inside the request (Cloud Run with CPU throttling)."""

import pytest
from fastapi.testclient import TestClient

from app import pipeline
from app.config import Settings, get_settings
from app.deepgram import DeepgramError, Transcript
from app.llm import LLMError, LLMResult
from app.main import app, get_db, get_deepgram
from app.rubrics import load_rubric

RUBRIC = load_rubric("us_insurance")
LINE = "Hi, this is Kevin from Harborline Insurance."


class FakeDB:
    def __init__(self):
        self.calls, self.transcripts, self.scores, self.rubrics = {}, {}, {}, {}

    def insert_call(self, **fields):
        call = {"id": f"c{len(self.calls) + 1}", "error": None, **fields}
        self.calls[call["id"]] = call
        return call

    def update_call(self, call_id, **fields):
        self.calls[call_id].update(fields)

    def save_transcript(self, call_id, utterances, provider="deepgram"):
        self.transcripts[call_id] = utterances

    def get_call(self, call_id):
        call = self.calls.get(call_id)
        return call and {**call, "transcripts": {"utterances": self.transcripts.get(call_id, [])},
                         "scores": list(reversed(self.scores.get(call_id, [])))}

    def get_rubric(self, slug, version):
        return self.rubrics.get((slug, version))

    def insert_rubric(self, **fields):
        self.rubrics[(fields["slug"], fields["version"])] = {"id": "r1", **fields}
        return self.rubrics[(fields["slug"], fields["version"])]

    def insert_score(self, **fields):
        self.scores.setdefault(fields["call_id"], []).append(fields)
        return fields


class FakeDeepgram:
    def __init__(self):
        self.fail = False

    def transcribe(self, **kwargs):
        if self.fail:
            raise DeepgramError("Deepgram returned HTTP 400: unsupported audio")
        return Transcript(utterances=[{"speaker": 0, "start": 0.0, "end": 3.0, "text": LINE}], duration_sec=3.2,
                          model="general-nova-3", request_id="r1", keyterms=kwargs["keyterms"])


class FakeLLM:
    """Passes agent_identification with a real quote and marks every other item n/a."""

    def __init__(self):
        self.calls, self.fail = 0, False

    def __call__(self, settings, system, user, schema):
        self.calls += 1
        if self.fail:
            raise LLMError("provider returned HTTP 503: high demand")
        items = [{"item_id": i["id"], "verdict": "na", "utterance_index": None, "evidence_quote": "", "reasoning": "n/a"}
                 for i in RUBRIC["items"]]
        items[0] = {"item_id": "agent_identification", "verdict": "pass", "utterance_index": 0,
                    "evidence_quote": "this is Kevin from Harborline Insurance", "reasoning": "named agent and company"}
        return LLMResult({"items": items}, "fake", "fake-model", 900, 200, 1500, None)


@pytest.fixture
def api(monkeypatch):
    db, dg, llm = FakeDB(), FakeDeepgram(), FakeLLM()
    monkeypatch.setattr(pipeline, "complete_json", llm)

    def make(**settings):
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None, **{"app_env": "test", "app_passcode": "test-passcode", "processing_mode": "inline", **settings})
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_deepgram] = lambda: dg
        return TestClient(app, headers={"X-Passcode": "test-passcode"})

    yield make, db, dg, llm
    app.dependency_overrides.clear()


def upload(client, **data):
    return client.post("/calls", files={"file": ("call_01.wav", b"RIFF-audio", "audio/wav")}, data=data)


def test_background_is_the_default(monkeypatch):
    assert Settings(_env_file=None).processing_mode == "background"
    monkeypatch.setenv("PROCESSING_MODE", "inline")
    assert Settings(_env_file=None).processing_mode == "inline"


def test_inline_upload_returns_the_finished_score(api):
    make, db, dg, llm = api
    res = upload(make(), rubric="us_insurance")
    assert res.status_code == 200
    body = res.json()
    assert (body["status"], body["error"], body["overall_score"], body["passed"]) == ("done", None, 100.0, True)
    assert body["rubric"] == "us_insurance"
    assert llm.calls == 1 and db.calls[body["id"]]["status"] == "done"


def test_inline_upload_without_rubric_stops_after_transcription(api):
    make, db, dg, llm = api
    body = upload(make()).json()
    assert (body["status"], body["overall_score"], body["passed"]) == ("scoring", None, None)
    assert db.transcripts[body["id"]][0]["text"] == LINE and llm.calls == 0


def test_inline_failures_come_back_in_the_response(api):
    make, db, dg, llm = api
    dg.fail = True
    res = upload(make(), rubric="us_insurance")
    assert res.status_code == 200
    assert res.json()["status"] == "failed" and "HTTP 400" in res.json()["error"] and llm.calls == 0

    dg.fail, llm.fail = False, True
    body = upload(make(), rubric="us_insurance").json()
    assert body["status"] == "failed" and "HTTP 503" in body["error"] and body["overall_score"] is None


def test_inline_rescore(api):
    make, db, dg, llm = api
    client = make()
    call_id = upload(client).json()["id"]
    res = client.post(f"/calls/{call_id}/score", json={"rubric": "us_insurance"})
    assert res.status_code == 200
    assert res.json()["status"] == "done" and res.json()["overall_score"] == 100.0 and llm.calls == 1

    llm.fail = True
    body = client.post(f"/calls/{call_id}/score", json={"rubric": "us_insurance"}).json()
    assert body["status"] == "failed" and "HTTP 503" in body["error"]
    assert len(db.scores[call_id]) == 1  # the failed run stored no score


def test_background_mode_still_answers_202_before_the_work(api):
    make, db, dg, llm = api
    res = upload(make(processing_mode="background"), rubric="us_insurance")
    assert res.status_code == 202 and res.json()["status"] == "queued"


def test_demo_visitors_stay_read_only_in_inline_mode(api):
    make, db, dg, llm = api
    visitor = make(demo_mode=True)
    visitor.headers.pop("X-Passcode")
    assert upload(visitor, rubric="us_insurance").status_code == 401
    assert visitor.post("/calls/c1/score", json={"rubric": "us_insurance"}).status_code == 401
    assert db.calls == {} and llm.calls == 0
