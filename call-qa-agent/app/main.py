import base64
import hashlib
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import http_exception_handler
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.auth import call_reader, require_owner
from app.auth import router as auth_router
from app.config import Settings, get_settings
from app.dashboard import router as dashboard_router
from app.db import Supabase, SupabaseError
from app.deepgram import DeepgramClient, merge_keyterms
from app.deps import get_db, get_deepgram
from app.pipeline import create_call, public_view, score_call, transcribe_and_score
from app.rubrics import available as available_rubrics
from app.ui import templates

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs carry project hosts and query strings
# /docs is served below with a policy that pins its inline code; FastAPI's own route would break under the CSP.
# There is no /redoc: ReDoc writes inline styles at runtime, which only 'unsafe-inline' would allow.
app = FastAPI(title="Call QA Agent", version=__version__, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(auth_router)
app.include_router(dashboard_router)

def is_page(request: Request) -> bool:
    """Dashboard pages get an HTML error page; API routes keep their JSON errors."""
    path = request.url.path
    return path == "/dashboard" or path.startswith("/dashboard/") or path == "/upload"


def error_page(request: Request, status: int, retry_after: str | None = None):
    wait = int(retry_after) if retry_after and retry_after.isdigit() else 0
    if status == 404:
        kind, heading, body = "empty", "Call not found", "It may have been removed, or the link is incomplete."
    elif status == 429:
        later = str(templates.env.globals["ui"].wait(wait)) if wait else "later"
        kind, heading, body = "limit", "Too many requests", f"This demo limits how often it can be used. Please try again {later}."
    else:
        kind, heading, body = "error", "Something went wrong", "The calls could not be loaded just now. Please try again in a moment."
    headers = {"Retry-After": retry_after} if retry_after else None
    return templates.TemplateResponse(request, "error.html", {"kind": kind, "heading": heading, "body": body, "viewer": None},
                                      status_code=status, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def page_errors(request: Request, exc: StarletteHTTPException):
    if is_page(request) and exc.status_code >= 400:
        return error_page(request, exc.status_code, (exc.headers or {}).get("Retry-After"))
    return await http_exception_handler(request, exc)


@app.exception_handler(SupabaseError)
async def database_errors(request: Request, exc: SupabaseError):
    logging.getLogger("app").warning("database error on %s: %s", request.url.path, exc)
    if is_page(request):
        return error_page(request, 503)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def storage_origin(supabase_url: str) -> str:
    """https://<project>.supabase.co from SUPABASE_URL, or "" when it is not set or not a URL."""
    parts = urlsplit(supabase_url.strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def page_csp(settings: Settings) -> str:
    """Every HTML page: scripts and styles from this site only. Audio plays from signed Storage URLs, and the
    upload page sends the file straight to Storage, so media and connections also allow the Supabase origin."""
    storage = storage_origin(settings.supabase_url)
    extra = f" {storage}" if storage else ""
    return ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            f"media-src 'self'{extra}; connect-src 'self'{extra}; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if response.headers.get("content-type", "").startswith("text/html") and "content-security-policy" not in response.headers:
        settings = app.dependency_overrides.get(get_settings, get_settings)()
        response.headers["Content-Security-Policy"] = page_csp(settings)
    return response


def pinned(page: HTMLResponse, sources: str) -> HTMLResponse:
    """The API docs load Swagger UI from a CDN and start it with inline code: allow that CDN and exactly
    that inline code (by hash), nothing else."""
    html = page.body.decode()
    digest = lambda code: "'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'"  # noqa: E731
    scripts = " ".join(digest(c) for c in re.findall(r"<script>(.*?)</script>", html, re.S))
    styles = " ".join(digest(c) for c in re.findall(r"<style>(.*?)</style>", html, re.S))
    page.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' {sources} {scripts}; style-src 'self' {sources} {styles}; "
        "img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'")
    return page


@app.get("/docs", include_in_schema=False)
def swagger_docs() -> HTMLResponse:
    return pinned(get_swagger_ui_html(openapi_url=app.openapi_url, title=f"{app.title} - Swagger UI"), "https://cdn.jsdelivr.net")



@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness check. Reports which integrations have keys, never the keys."""
    return {
        "status": "ok",
        "version": __version__,
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "configured": {
            "supabase": bool(settings.supabase_url and settings.supabase_service_key.get_secret_value()),
            "deepgram": bool(settings.deepgram_api_key.get_secret_value()),
            "anthropic": bool(settings.anthropic_api_key.get_secret_value()),
            "openai": bool(settings.openai_api_key.get_secret_value()),
            "gemini": bool(settings.gemini_api_key.get_secret_value()),
        },
        "processing_mode": settings.processing_mode,
        "storage_bucket": settings.storage_bucket,
    }


# Object paths are <uuid>/<cleaned file name>; anything else is refused before it reaches Storage.
STORAGE_PATH = re.compile(r"^[0-9a-f]{32}/[A-Za-z0-9._-]{1,120}$")


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    size: int = Field(gt=0)


def storage_name(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", filename.rsplit("/", 1)[-1]).strip("-.") or "audio"
    return stem[-120:]


@app.post("/uploads", dependencies=[Depends(require_owner)])
def create_upload(body: UploadRequest, settings: Settings = Depends(get_settings), db: Supabase = Depends(get_db)) -> dict:
    """Step 1 of a large upload: a signed URL the browser PUTs the file to, straight to Supabase Storage,
    so the file never passes through this server (Vercel Functions accept at most 4.5 MB per request).
    Step 2: POST /calls with storage_path."""
    if not body.content_type.startswith("audio/"):
        raise HTTPException(415, f"expected an audio file, got {body.content_type}")
    if body.size > settings.storage_max_mb * 1024 * 1024:
        raise HTTPException(413, f"file is larger than {settings.storage_max_mb} MB; send it as a url instead")
    path = f"{uuid.uuid4().hex}/{storage_name(body.filename)}"
    try:
        upload_url = db.signed_upload_url(settings.storage_bucket, path)
    except SupabaseError as e:
        raise HTTPException(502, f"could not create the upload URL: {e}") from e
    return {"storage_path": path, "upload_url": upload_url, "max_mb": settings.storage_max_mb}


@app.post("/calls", status_code=202, dependencies=[Depends(require_owner)])
async def upload_call(
    background: BackgroundTasks,
    response: Response,
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    storage_path: str | None = Form(None, description="Path returned by POST /uploads, after the browser uploaded the file"),
    language: str = Form("en"),
    keyterms: str = Form("", description="Comma-separated brand and product names for this call"),
    rubric: str | None = Form(None, description="Rubric to score with after transcription, e.g. us_insurance"),
    settings: Settings = Depends(get_settings),
    db: Supabase = Depends(get_db),
    dg: DeepgramClient = Depends(get_deepgram),
) -> dict:
    """Upload an audio file, give a public URL, or name a file already uploaded to Storage.

    Background mode answers 202 at once and transcribes (and scores) afterwards. Inline mode does
    the work inside the request and answers 200 with the final status.
    """
    if sum(x is not None and x != "" for x in (file, url, storage_path)) != 1:
        raise HTTPException(422, "send exactly one of file, url or storage_path")
    check_rubric(rubric)
    filename = None
    if storage_path:
        if not STORAGE_PATH.match(storage_path):
            raise HTTPException(422, "storage_path must be the path returned by POST /uploads")
        try:
            # Deepgram fetches the file itself; the signed URL is short-lived and never stored.
            url = db.signed_download_url(settings.storage_bucket, storage_path, settings.storage_signed_url_seconds)
        except SupabaseError as e:
            raise HTTPException(404 if e.status in (400, 404) else 502, f"uploaded file not found in storage: {e}") from e
        filename = storage_path.split("/", 1)[1]
    audio, mimetype = None, "audio/wav"
    if file is not None:
        mimetype = file.content_type or "application/octet-stream"
        if not mimetype.startswith("audio/"):
            raise HTTPException(415, f"expected an audio file, got {mimetype}")
        audio = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(audio) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"file is larger than {settings.max_upload_mb} MB")
    elif not storage_path and not url.startswith(("https://", "http://")):
        raise HTTPException(422, "url must start with http:// or https://")
    if storage_path:
        call = create_call(db, filename=filename, source_url=None, language=language, storage_path=storage_path)
    else:
        call = create_call(db, filename=file.filename if file else url.rsplit("/", 1)[-1] or "remote-audio",
                           source_url=url, language=language)
    terms = merge_keyterms(settings.deepgram_keyterms, keyterms)
    job = (transcribe_and_score, db, dg, settings, call["id"], rubric)
    work = dict(audio=audio, mimetype=mimetype, url=url, language=language, keyterms=terms)
    if settings.processing_mode == "inline":
        await run_in_threadpool(*job, **work)
        response.status_code = 200
        return {**outcome(db, call["id"]), "keyterms": terms, "rubric": rubric}
    background.add_task(*job, **work)
    return {"id": call["id"], "status": call["status"], "keyterms": terms, "rubric": rubric}


def outcome(db: Supabase, call_id: str) -> dict:
    """Final state of a call processed inline: status, error and the latest score."""
    call = db.get_call(call_id) or {}
    score = (call.get("scores") or [None])[0] or {}
    return {"id": call_id, "status": call.get("status"), "error": call.get("error"),
            "overall_score": score.get("overall_score"), "passed": score.get("passed")}


def check_rubric(slug: str | None) -> None:
    if slug is not None and slug not in available_rubrics():
        raise HTTPException(422, f"unknown rubric {slug!r}, use one of {', '.join(available_rubrics())}")


@app.post("/calls/{call_id}/score", status_code=202, dependencies=[Depends(require_owner)])
def rescore_call(call_id: str, background: BackgroundTasks, response: Response, rubric: str = Body(..., embed=True),
                 settings: Settings = Depends(get_settings), db: Supabase = Depends(get_db)) -> dict:
    """Score (or re-score) a transcribed call. Each run adds a scores row; the call shows the latest."""
    check_rubric(rubric)
    call = db.get_call(call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    if not (call.get("transcripts") or {}).get("utterances"):
        raise HTTPException(409, "call has no transcript yet")
    if settings.processing_mode == "inline":
        score_call(db, settings, call_id, rubric)  # sync route: already off the event loop
        response.status_code = 200
        return {**outcome(db, call_id), "rubric": rubric}
    background.add_task(score_call, db, settings, call_id, rubric)
    return {"id": call_id, "status": "scoring", "rubric": rubric}


def readable_call(db: Supabase, call_id: str, reader: str) -> dict:
    call = db.get_call(call_id)
    if call is None or (reader == "demo" and not call.get("is_demo")):
        raise HTTPException(404, "call not found")
    return public_view(call, reader)


@app.get("/calls/{call_id}")
def get_call(call_id: str, db: Supabase = Depends(get_db), reader: str = Depends(call_reader)) -> dict:
    """The call with its transcript and scores. Without the passcode, demo calls only (in demo mode)."""
    return readable_call(db, call_id, reader)


@app.get("/calls/{call_id}/audio", response_class=RedirectResponse, status_code=307)
def call_audio(call_id: str, settings: Settings = Depends(get_settings), db: Supabase = Depends(get_db),
               reader: str = Depends(call_reader)):
    """Redirects to a short-lived signed URL for the call's audio in Storage (audio_url_seconds)."""
    call = readable_call(db, call_id, reader)
    if not call.get("storage_path"):
        raise HTTPException(404, "this call has no stored audio")
    url = db.signed_download_url(settings.storage_bucket, call["storage_path"], settings.audio_url_seconds)
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "no-store"})
