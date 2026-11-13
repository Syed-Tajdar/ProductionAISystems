import json
from urllib.parse import parse_qsl

import httpx
import pytest

from app.deepgram import DeepgramClient, DeepgramError, merge_keyterms

RESPONSE = {
    "metadata": {"request_id": "req-1", "duration": 19.77,
                 "model_info": {"m1": {"name": "general-nova-3", "version": "2025-07-31.0"}}},
    "results": {
        "channels": [{"alternatives": [{"transcript": "..."}]}],
        "utterances": [
            {"speaker": 0, "start": 0.08, "end": 9.523, "confidence": 0.99,
             "transcript": "Good afternoon, this is James Carter with Harborline Insurance."},
            {"speaker": 1, "start": 10.3, "end": 12.46, "confidence": 0.98, "transcript": "Sure, I've got a few minutes."},
        ],
    },
}


def client_with(handler, **kwargs):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request, sum(isinstance(s, httpx.Request) for s in seen))

    dg = DeepgramClient("any-non-empty-key", client=httpx.Client(transport=httpx.MockTransport(record)),
                        sleep=lambda s: seen.append(("sleep", s)), **kwargs)
    return dg, seen


def test_request_parameters_and_parsing():
    dg, seen = client_with(lambda req, n: httpx.Response(200, json=RESPONSE))
    t = dg.transcribe(audio=b"RIFF....", mimetype="audio/wav", language="en",
                      keyterms=["Harborline Insurance", "Medicare Part B"])

    req = seen[0]
    assert req.method == "POST" and req.url.path == "/v1/listen"
    params = parse_qsl(req.url.query.decode())
    assert ("model", "nova-3") in params and ("diarize", "true") in params and ("utterances", "true") in params
    assert [v for k, v in params if k == "keyterm"] == ["Harborline Insurance", "Medicare Part B"]
    assert req.headers["authorization"] == "Token any-non-empty-key"
    assert req.headers["content-type"] == "audio/wav" and req.content == b"RIFF...."

    assert t.duration_sec == 19.77 and t.model == "general-nova-3 2025-07-31.0" and t.request_id == "req-1"
    assert t.utterances[0] == {"speaker": 0, "start": 0.08, "end": 9.52, "confidence": 0.99,
                               "text": "Good afternoon, this is James Carter with Harborline Insurance."}
    assert [u["speaker"] for u in t.utterances] == [0, 1]


def test_url_source_is_sent_as_json():
    dg, seen = client_with(lambda req, n: httpx.Response(200, json=RESPONSE))
    dg.transcribe(url="https://example.com/call.mp3")
    assert json.loads(seen[0].content) == {"url": "https://example.com/call.mp3"}


def test_older_models_use_keywords():
    dg, seen = client_with(lambda req, n: httpx.Response(200, json=RESPONSE), model="nova-2")
    dg.transcribe(audio=b"x", keyterms=["Harborline"])
    params = parse_qsl(seen[0].url.query.decode())
    assert ("keywords", "Harborline") in params and not any(k == "keyterm" for k, _ in params)


def test_retries_429_then_succeeds():
    def handler(req, n):
        if n == 1:
            return httpx.Response(429, json={"err_msg": "slow down"}, headers={"retry-after": "3"})
        if n == 2:
            return httpx.Response(503, json={"err_msg": "busy"})
        return httpx.Response(200, json=RESPONSE)

    dg, seen = client_with(handler)
    assert len(dg.transcribe(audio=b"x").utterances) == 2
    assert [s for s in seen if isinstance(s, tuple)] == [("sleep", 3), ("sleep", 2)]


def test_gives_up_after_three_attempts():
    dg, seen = client_with(lambda req, n: httpx.Response(503, json={"err_msg": "busy"}))
    with pytest.raises(DeepgramError, match="HTTP 503: busy"):
        dg.transcribe(audio=b"x")
    assert len([s for s in seen if isinstance(s, httpx.Request)]) == 3


def test_client_errors_are_not_retried():
    dg, seen = client_with(lambda req, n: httpx.Response(400, json={"err_msg": "Bad Request: unsupported audio"}))
    with pytest.raises(DeepgramError, match="unsupported audio"):
        dg.transcribe(audio=b"x")
    assert len(seen) == 1


def test_needs_audio_or_url_and_a_key():
    dg, _ = client_with(lambda req, n: httpx.Response(200, json=RESPONSE))
    with pytest.raises(ValueError):
        dg.transcribe()
    with pytest.raises(DeepgramError, match="DEEPGRAM_API_KEY"):
        DeepgramClient("")


def test_merge_keyterms():
    assert merge_keyterms("Harborline Insurance, Brightwave Internet", ["harborline insurance", " ", "Palm Crest"]) == [
        "Harborline Insurance", "Brightwave Internet", "Palm Crest"]
    assert len(merge_keyterms([f"term {i}" for i in range(150)])) == 100
