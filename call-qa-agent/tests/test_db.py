import json

import httpx
import pytest

from app.db import Supabase, SupabaseError

CALL_ID = "0b0e4f5e-9a57-4f0c-8f0e-2f5d1c7a9b11"


def db_with(handler, key="sb_secret_example"):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    return Supabase("https://project.supabase.co", key, client=httpx.Client(transport=httpx.MockTransport(record))), seen


def test_secret_key_goes_in_apikey_header_only():
    db, seen = db_with(lambda r: httpx.Response(200, json=[]))
    db.get_call(CALL_ID)
    assert seen[0].headers["apikey"] == "sb_secret_example"
    assert "authorization" not in seen[0].headers


def test_legacy_jwt_key_also_sends_bearer():
    db, seen = db_with(lambda r: httpx.Response(200, json=[]), key="eyJhbGciOiJIUzI1NiJ9.legacy")
    db.get_call(CALL_ID)
    assert seen[0].headers["authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.legacy"


def test_insert_update_save_delete():
    def handler(r):
        if r.method == "POST" and r.url.path.endswith("/calls"):
            return httpx.Response(201, json=[{"id": "c1", **json.loads(r.content)}])
        return httpx.Response(204)

    db, seen = db_with(handler)
    call = db.insert_call(filename="a.wav", source_url=None, language="en", status="queued")
    db.update_call("c1", status="scoring", duration_sec=19.77)
    db.save_transcript("c1", [{"speaker": 0, "start": 0, "end": 1, "text": "hi"}])
    db.delete_call("c1")

    assert call["id"] == "c1"
    insert, update, upsert, delete = seen
    assert insert.headers["prefer"] == "return=representation"
    assert (update.method, update.url.path, update.url.params["id"]) == ("PATCH", "/rest/v1/calls", "eq.c1")
    assert json.loads(update.content) == {"status": "scoring", "duration_sec": 19.77}
    assert upsert.url.path == "/rest/v1/transcripts" and upsert.url.params["on_conflict"] == "call_id"
    assert "resolution=merge-duplicates" in upsert.headers["prefer"]
    assert json.loads(upsert.content)["provider"] == "deepgram"
    assert (delete.method, delete.url.params["id"]) == ("DELETE", "eq.c1")


def test_get_call_embeds_transcript_and_handles_missing():
    db, seen = db_with(lambda r: httpx.Response(200, json=[]))
    assert db.get_call("11111111-2222-4333-8444-555555555555") is None
    assert seen[0].url.params["select"] == "*,transcripts(utterances,provider),scores(*,rubrics(slug,name,version,items))"
    assert seen[0].url.params["scores.order"] == "created_at.desc"


def test_list_calls_embeds_latest_score_only():
    db, seen = db_with(lambda r: httpx.Response(200, json=[]))
    assert db.list_calls(limit=20, offset=40) == []
    p = seen[0].url.params
    assert (p["order"], p["limit"], p["offset"], p["scores.limit"], p["scores.order"]) == (
        "created_at.desc", "20", "40", "1", "created_at.desc")
    assert "rubrics(slug,name,version,items)" in p["select"]  # the list shows why a call failed


def test_rubric_and_score_rows():
    def handler(r):
        if r.method == "POST":
            return httpx.Response(201, json=[{"id": "x1", **json.loads(r.content)}])
        return httpx.Response(200, json=[{"id": "r1"}])

    db, seen = db_with(handler)
    assert db.get_rubric("us_insurance", 1) == {"id": "r1"}
    assert (seen[0].url.params["slug"], seen[0].url.params["version"]) == ("eq.us_insurance", "eq.1")
    assert db.insert_rubric(slug="us_insurance", name="US", version=1, items=[])["id"] == "x1"
    score = db.insert_score(call_id="c1", rubric_id="r1", overall_score=80.0, items=[], model="gemini/gemini-3.8-flash")
    assert score["overall_score"] == 80.0 and seen[-1].url.path == "/rest/v1/scores"


def test_auth_failure_is_reported_not_hidden():
    db, _ = db_with(lambda r: httpx.Response(401, json={"code": "PGRST301", "message": "Invalid API key"}))
    with pytest.raises(SupabaseError, match="HTTP 401: PGRST301 Invalid API key") as err:
        db.get_call(CALL_ID)
    assert err.value.status == 401


def test_requires_url_and_key():
    with pytest.raises(SupabaseError):
        Supabase("", "key")


def test_storage_upload_and_signed_download():
    def handler(request):
        if "/object/sign/" in request.url.path:
            return httpx.Response(200, json={"signedURL": "/object/sign/call-audio/demo/call_01.wav?token=t"})
        return httpx.Response(200, json={"Key": "call-audio/demo/call_01.wav"})

    db, seen = db_with(handler)
    db.upload_object("call-audio", "demo/call_01.wav", b"RIFF", "audio/wav")
    up = seen[0]
    assert (up.method, up.url.path) == ("POST", "/storage/v1/object/call-audio/demo/call_01.wav")
    assert up.headers["content-type"] == "audio/wav" and up.headers["x-upsert"] == "true" and up.content == b"RIFF"
    url = db.signed_download_url("call-audio", "demo/call_01.wav", 300)
    assert json.loads(seen[1].content) == {"expiresIn": 300}
    assert url == "https://project.supabase.co/storage/v1/object/sign/call-audio/demo/call_01.wav?token=t"


def test_storage_remove():
    db, seen = db_with(lambda r: httpx.Response(200, json=[]))
    db.remove_objects("call-audio", ["demo/call_01.mp3"])
    assert (seen[0].method, seen[0].url.path) == ("DELETE", "/storage/v1/object/call-audio")
    assert json.loads(seen[0].content) == {"prefixes": ["demo/call_01.mp3"]}


@pytest.mark.parametrize("call_id", ["abc", "1 or 1=1", "../calls", "0b0e4f5e-9a57-4f0c-8f0e", ""])
def test_a_call_id_that_is_not_a_uuid_is_not_sent_to_the_database(call_id):
    db, seen = db_with(lambda r: httpx.Response(400, json={"message": "invalid input syntax for type uuid"}))
    assert db.get_call(call_id) is None and seen == []


def test_a_uuid_call_id_is_looked_up():
    db, seen = db_with(lambda r: httpx.Response(200, json=[{"id": CALL_ID}]))
    assert db.get_call(CALL_ID.upper()) == {"id": CALL_ID}
    assert seen[0].url.params["id"] == f"eq.{CALL_ID}"
