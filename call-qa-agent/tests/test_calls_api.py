"""Upload endpoint and background pipeline, with Supabase and Deepgram replaced by fakes."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.deepgram import DeepgramError, Transcript
from app.main import app, get_db, get_deepgram
from app.pipeline import transcribe_call


class FakeDB:
    def __init__(self):
        self.calls, self.transcripts = {}, {}

    def insert_call(self, **fields):
        call = {"id": f"c{len(self.calls) + 1}", **fields}
        self.calls[call["id"]] = call
        return call

    def update_call(self, call_id, **fields):
        self.calls[call_id].update(fields)

    def save_transcript(self, call_id, utterances, provider="deepgram"):
        self.transcripts[call_id] = utterances

    def get_call(self, call_id):
        call = self.calls.get(call_id)
        return call and {**call, "transcripts": {"utterances": self.transcripts.get(call_id, [])}}


class FakeDeepgram:
    def __init__(self, fail=False):
        self.fail, self.requests = fail, []

    def transcribe(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail:
            raise DeepgramError("Deepgram returned HTTP 400: unsupported audio")
        return Transcript(utterances=[{"speaker": 0, "start": 0.1, "end": 2.0, "text": "Hello from Harborline Insurance."}],
                          duration_sec=2.4, model="general-nova-3", request_id="r1", keyterms=kwargs["keyterms"])


@pytest.fixture
def api():
    db, dg = FakeDB(), FakeDeepgram()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, app_env="test", deepgram_keyterms="Harborline Insurance, Brightwave Internet", max_upload_mb=1,
        app_passcode="test-passcode")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_deepgram] = lambda: dg
    yield TestClient(app, headers={"X-Passcode": "test-passcode"}), db, dg
    app.dependency_overrides.clear()


def test_upload_transcribes_and_stores(api):
    client, db, dg = api
    res = client.post("/calls", files={"file": ("call_02.wav", b"RIFF-audio", "audio/wav")},
                      data={"keyterms": "Medicare Part B, harborline insurance"})
    assert res.status_code == 202
    body = res.json()
    assert body["keyterms"] == ["Harborline Insurance", "Brightwave Internet", "Medicare Part B"]

    call = client.get(f"/calls/{body['id']}").json()  # background task has run
    assert (call["status"], call["duration_sec"], call["filename"]) == ("scoring", 2.4, "call_02.wav")
    assert call["transcripts"]["utterances"][0]["text"] == "Hello from Harborline Insurance."
    assert dg.requests[0]["audio"] == b"RIFF-audio" and dg.requests[0]["mimetype"] == "audio/wav"


def test_url_source(api):
    client, db, dg = api
    res = client.post("/calls", data={"url": "https://example.com/audio/call_05.mp3", "language": "ar"})
    assert res.status_code == 202
    assert db.calls["c1"]["filename"] == "call_05.mp3" and db.calls["c1"]["source_url"].endswith("call_05.mp3")
    assert dg.requests[0]["url"].startswith("https://") and dg.requests[0]["language"] == "ar"


@pytest.mark.parametrize("kwargs, status", [
    ({}, 422),                                                                       # neither file nor url
    ({"files": {"file": ("a.wav", b"x", "audio/wav")}, "data": {"url": "https://x/a.wav"}}, 422),  # both
    ({"files": {"file": ("notes.txt", b"x", "text/plain")}}, 415),
    ({"files": {"file": ("big.wav", b"0" * (1024 * 1024 + 1), "audio/wav")}}, 413),
    ({"data": {"url": "ftp://example.com/a.wav"}}, 422),
])
def test_rejects_bad_uploads(api, kwargs, status):
    client, db, _ = api
    assert client.post("/calls", **kwargs).status_code == status
    assert db.calls == {}


def test_missing_call_is_404(api):
    assert api[0].get("/calls/nope").status_code == 404


def test_failure_is_recorded_on_the_call():
    db = FakeDB()
    call = db.insert_call(filename="bad.wav", status="queued")
    transcribe_call(db, FakeDeepgram(fail=True), call["id"], audio=b"x")
    assert db.calls["c1"]["status"] == "failed"
    assert "unsupported audio" in db.calls["c1"]["error"]
    assert "c1" not in db.transcripts
