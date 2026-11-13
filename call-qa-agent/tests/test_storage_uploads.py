"""Large uploads through Supabase Storage: signed upload URL, then processing from storage."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import Supabase, SupabaseError
from app.main import app, get_db, get_deepgram
from tests.test_calls_api import FakeDB, FakeDeepgram

PATH = "0123456789abcdef0123456789abcdef/call-01.mp3"


class StorageDB(FakeDB):
    def __init__(self, missing=False):
        super().__init__()
        self.missing, self.signed = missing, []

    def signed_upload_url(self, bucket, path):
        self.signed.append(("upload", bucket, path))
        return f"https://project.supabase.co/storage/v1/object/upload/sign/{bucket}/{path}?token=up"

    def signed_download_url(self, bucket, path, seconds):
        self.signed.append(("download", bucket, path, seconds))
        if self.missing:
            raise SupabaseError(400, "Object not found")
        return f"https://project.supabase.co/storage/v1/object/sign/{bucket}/{path}?token=down"


@pytest.fixture
def api(request):
    db, dg = StorageDB(missing=getattr(request, "param", False)), FakeDeepgram()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, app_env="test", app_passcode="test-passcode", processing_mode="inline", storage_max_mb=50)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_deepgram] = lambda: dg
    yield TestClient(app, headers={"X-Passcode": "test-passcode"}), db, dg
    app.dependency_overrides.clear()


def test_upload_url_for_an_audio_file(api):
    client, db, _ = api
    res = client.post("/uploads", json={"filename": "../Call 01 (final).mp3", "content_type": "audio/mpeg", "size": 30_000_000})
    assert res.status_code == 200
    body = res.json()
    folder, name = body["storage_path"].split("/")
    assert len(folder) == 32 and name == "Call-01-final-.mp3"
    assert body["upload_url"].startswith("https://project.supabase.co/storage/v1/object/upload/sign/call-audio/")
    assert db.signed == [("upload", "call-audio", body["storage_path"])]


@pytest.mark.parametrize("payload, status", [
    ({"filename": "notes.pdf", "content_type": "application/pdf", "size": 10}, 415),
    ({"filename": "long.wav", "content_type": "audio/wav", "size": 51 * 1024 * 1024}, 413),
    ({"filename": "a.wav", "content_type": "audio/wav", "size": 0}, 422),
])
def test_upload_url_is_refused_for_bad_files(api, payload, status):
    client, db, _ = api
    assert client.post("/uploads", json=payload).status_code == status
    assert db.signed == []


def test_upload_url_needs_the_passcode(api):
    _, db, _ = api
    res = TestClient(app).post("/uploads", json={"filename": "a.wav", "content_type": "audio/wav", "size": 10})
    assert res.status_code == 401 and db.signed == []


def test_call_from_storage_is_processed_from_a_signed_url(api):
    client, db, dg = api
    res = client.post("/calls", data={"storage_path": PATH, "language": "en"})
    assert res.status_code == 200 and res.json()["status"] == "scoring"
    call = db.calls["c1"]
    assert call["storage_path"] == PATH and call["filename"] == "call-01.mp3"
    assert call["source_url"] is None  # the signed URL carries a token and is never stored
    assert dg.requests[0]["url"].endswith("?token=down") and dg.requests[0]["audio"] is None
    assert db.signed == [("download", "call-audio", PATH, 3600)]


@pytest.mark.parametrize("path", ["../secrets.txt", "0123/a.wav", PATH + "/x", "0123456789abcdef0123456789abcdef/a b.wav"])
def test_storage_path_must_come_from_the_upload_step(api, path):
    client, db, _ = api
    assert client.post("/calls", data={"storage_path": path}).status_code == 422
    assert db.calls == {} and db.signed == []


@pytest.mark.parametrize("api", [True], indirect=True)
def test_missing_storage_object(api):
    client, db, _ = api
    res = client.post("/calls", data={"storage_path": PATH})
    assert res.status_code == 404 and db.calls == {}


def test_only_one_source_per_call(api):
    client, _, _ = api
    res = client.post("/calls", data={"storage_path": PATH, "url": "https://example.com/a.mp3"})
    assert res.status_code == 422


def test_upload_page_is_for_the_owner(api):
    client, _, _ = api
    page = client.get("/upload")
    assert page.status_code == 200 and "upload.js" in page.text and "us_insurance" in page.text
    visitor = TestClient(app).get("/upload", follow_redirects=False)
    assert visitor.status_code == 303 and visitor.headers["location"].startswith("/login")
    assert "fetch(\"/uploads\"" in TestClient(app).get("/static/upload.js").text


def storage_client(handler):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    return Supabase("https://project.supabase.co", "sb_secret_example", client=httpx.Client(transport=httpx.MockTransport(record))), seen


def test_storage_requests():
    def handler(request):
        if "/upload/sign/" in request.url.path:
            return httpx.Response(200, json={"url": "/object/upload/sign/call-audio/abc/a%20b.wav?token=t1"})
        return httpx.Response(200, json={"signedURL": "/object/sign/call-audio/abc/a.wav?token=t2"})

    db, seen = storage_client(handler)
    assert db.signed_upload_url("call-audio", "abc/a b.wav") == \
        "https://project.supabase.co/storage/v1/object/upload/sign/call-audio/abc/a%20b.wav?token=t1"
    assert db.signed_download_url("call-audio", "abc/a.wav", 600) == \
        "https://project.supabase.co/storage/v1/object/sign/call-audio/abc/a.wav?token=t2"
    upload, download = seen
    assert upload.method == "POST" and upload.url.raw_path == b"/storage/v1/object/upload/sign/call-audio/abc/a%20b.wav"
    assert json.loads(upload.content) == {}  # Storage rejects an empty JSON body
    assert download.url.path == "/storage/v1/object/sign/call-audio/abc/a.wav" and json.loads(download.content) == {"expiresIn": 600}
    assert all(r.headers["apikey"] == "sb_secret_example" for r in seen)


def test_storage_errors_are_raised():
    db, _ = storage_client(lambda r: httpx.Response(400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"}))
    with pytest.raises(SupabaseError, match="Object not found"):
        db.signed_download_url("call-audio", "abc/a.wav", 60)
