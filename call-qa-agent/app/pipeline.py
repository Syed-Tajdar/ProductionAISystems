"""Call pipeline: create the call, transcribe it, store the utterances, score them against a rubric."""

import logging

import httpx

from app.config import Settings
from app.db import Supabase, SupabaseError
from app.deepgram import DeepgramClient, merge_keyterms
from app.llm import complete_json
from app.rubrics import ensure_rubric_row, load_rubric
from app.scoring import SYSTEM, build_prompt, overall, schema_for, validate_items

log = logging.getLogger(__name__)

# After transcription the call waits for scoring in status "scoring"; a stored score sets "done".
READY_FOR_SCORING = "scoring"


def create_call(db: Supabase, *, filename: str, source_url: str | None, language: str,
                storage_path: str | None = None) -> dict:
    fields = dict(filename=filename, source_url=source_url, language=language, status="queued")
    if storage_path:
        fields["storage_path"] = storage_path
    return db.insert_call(**fields)


FAILED_TEXT = "Processing failed. The details are kept for the owner."


def public_view(call: dict, reader: str) -> dict:
    """What a reader may see of a call. The stored error can quote an upstream service's answer, so only the
    owner gets it; everyone else gets a fixed sentence."""
    if reader != "owner" and call.get("error"):
        return {**call, "error": FAILED_TEXT}
    return call


def _fail(db: Supabase, call_id: str, error: Exception) -> None:
    log.exception("call %s failed", call_id)
    try:
        db.update_call(call_id, status="failed", error=str(error)[:500])
    except (SupabaseError, httpx.HTTPError):
        log.exception("could not mark call %s as failed", call_id)


def transcribe_call(db: Supabase, dg: DeepgramClient, call_id: str, *, audio: bytes | None = None,
                    mimetype: str = "audio/wav", url: str | None = None, language: str = "en",
                    keyterms: list[str] | None = None) -> bool:
    """Runs in the background. Any failure is stored on the call instead of raised. Returns success."""
    try:
        db.update_call(call_id, status="transcribing")
        transcript = dg.transcribe(audio=audio, mimetype=mimetype, url=url, language=language,
                                   keyterms=merge_keyterms(keyterms))
        db.save_transcript(call_id, transcript.utterances)
        db.update_call(call_id, status=READY_FOR_SCORING, duration_sec=transcript.duration_sec,
                       language=transcript.detected_language or language, error=None)
        log.info("call %s transcribed: %d utterances", call_id, len(transcript.utterances))
        return True
    except Exception as e:  # noqa: BLE001 - the end of a background job: whatever went wrong is recorded on the
        _fail(db, call_id, e)  # call, or it would stay "transcribing" forever with nobody to see the error
        return False


def score_call(db: Supabase, settings: Settings, call_id: str, rubric_slug: str, *, llm=None) -> dict | None:
    """Scores the stored transcript and stores the result. Failures are stored on the call."""
    llm = llm or complete_json
    try:
        call = db.get_call(call_id)
        if call is None:
            raise ValueError(f"call {call_id} not found")
        utterances = (call.get("transcripts") or {}).get("utterances") or []
        if not utterances:
            raise ValueError("no transcript to score")
        rubric = load_rubric(rubric_slug)
        rubric_id = ensure_rubric_row(db, rubric)
        db.update_call(call_id, status="scoring")

        result = llm(settings, SYSTEM, build_prompt(rubric, utterances), schema_for(rubric))
        score = overall(rubric, validate_items(rubric, result.data.get("items", []), utterances))
        row = db.insert_score(
            call_id=call_id, rubric_id=rubric_id, overall_score=score.overall_score, passed=score.passed,
            items=score.items, model=f"{result.provider}/{result.model}", input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, latency_ms=result.latency_ms, cost_usd=result.cost_usd)
        db.update_call(call_id, status="done", error=None)
        review = sum(i["verdict"] == "needs_review" for i in score.items)
        log.info("call %s scored %.1f (passed=%s, %d needs_review)", call_id, score.overall_score, score.passed, review)
        return row
    except Exception as e:  # noqa: BLE001 - the end of a background job, as in transcribe_call
        _fail(db, call_id, e)
        return None


def transcribe_and_score(db: Supabase, dg: DeepgramClient, settings: Settings, call_id: str, rubric_slug: str | None,
                         **transcribe_kwargs) -> None:
    if transcribe_call(db, dg, call_id, **transcribe_kwargs) and rubric_slug:
        score_call(db, settings, call_id, rubric_slug)
