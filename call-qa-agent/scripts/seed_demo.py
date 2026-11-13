"""Create (or remove) the pre-scored demo calls that DEMO_MODE shows to visitors.

    python scripts/seed_demo.py                        # all samples, transcript from the script text
    python scripts/seed_demo.py --source audio         # Deepgram on samples/audio (make_sample_audio.py first)
    python scripts/seed_demo.py --provider gemini call_01 call_08
    python scripts/seed_demo.py --remove               # delete every demo call
    python scripts/seed_demo.py --skip-existing        # only the samples not yet seeded (safe to rerun)

Each demo call also gets its sample audio in Storage (STORAGE_BUCKET, at demo/v2/<call>.mp3) for the player on the
call page. With --skip-existing, demo calls that already exist get the current audio if theirs is missing or older
(a new path, so phones do not play a cached old file); the old file is deleted and the call is not scored again. A
transcript made from the script text is moved to the audio's line timings (samples/audio/<call>.timings.json) so the
player can follow it. A sample without an MP3 yet leaves its call without a player.

Demo calls are ordinary calls with is_demo = true, so they go through the normal scoring.
A call that fails is deleted again, so visitors never see half-made calls. Synthetic data only.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_score import sample_transcript  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Supabase  # noqa: E402
from app.deepgram import DeepgramClient, merge_keyterms  # noqa: E402
from app.pipeline import score_call, transcribe_call  # noqa: E402
from evals.run import AUDIO, KEYTERMS, UNAVAILABLE  # noqa: E402


def demo_filename(exp: dict) -> str:
    return f"demo_{exp['script'].split('/')[-1].replace('.md', '.wav')}"


def demo_labels(exp: dict) -> dict:
    """Readable title and summary for the dashboard, from the sample script's front matter."""
    front = yaml.safe_load((ROOT / "samples" / exp["script"]).read_text(encoding="utf-8").split("---", 2)[1])
    return {"title": front.get("title"), "summary": front.get("scenario")}


def audio_timings(call_id: str) -> list[dict]:
    path = AUDIO / f"{call_id}.timings.json"
    return json.loads(path.read_text(encoding="utf-8"))["lines"] if path.exists() else []


def retime(utterances: list[dict], timings: list[dict]) -> list[dict] | None:
    """Script-text utterances moved to the real start and end of each line in the audio; None if they do not line up."""
    if len(utterances) != len(timings) or any(u["text"][:30] != t["text"][:30] for u, t in zip(utterances, timings, strict=True)):
        return None
    return [{**u, "start": t["start"], "end": t["end"]} for u, t in zip(utterances, timings, strict=True)]


AUDIO_PREFIX = "demo/v2"  # bump when the sample audio changes, so phones fetch the new file instead of a cached one


def attach_audio(db, settings, call_id: str, sample: str, log=print) -> bool:
    """Uploads the sample's current audio for the player and re-times the transcript to it. Replaces older demo audio
    (another path under demo/), deleting the old file; returns False when the call already has the current audio."""
    call = db.get_call(call_id) or {}
    mp3 = AUDIO / f"{sample}.mp3"  # committed, small; the WAVs are local only (Deepgram runs)
    path, old = f"{AUDIO_PREFIX}/{sample}.mp3", call.get("storage_path")
    if old == path:
        return False
    if old and not old.startswith("demo/"):
        return False  # not demo audio: leave it alone
    if not mp3.exists():
        if old:  # older audio, nothing current to replace it with: no player rather than the old voice
            db.update_call(call_id, storage_path=None)
            db.remove_objects(settings.storage_bucket, [old])
            log(f"{sample}: old audio {old} removed, no current audio yet")
        return False
    db.upload_object(settings.storage_bucket, path, mp3.read_bytes(), "audio/mpeg")
    fields = {"storage_path": path}
    transcript = call.get("transcripts") or {}
    if transcript.get("provider") == "script" and (moved := retime(transcript.get("utterances") or [], audio_timings(sample))):
        db.save_transcript(call_id, moved, provider="script")
        fields["duration_sec"] = moved[-1]["end"]
    db.update_call(call_id, **fields)
    if old:
        db.remove_objects(settings.storage_bucket, [old])
    log(f"{sample}: audio stored at {path}" + (f", replacing {old}" if old else ""))
    return True


def seed(db, settings, call_ids: list[str] | None, source: str, dg=None, *, llm=None, log=print,
         skip_existing: bool = False, audio: bool = False, uploaded: list | None = None) -> dict:
    expected = json.loads((ROOT / "samples/expected.json").read_text(encoding="utf-8"))["calls"]
    done, failed, skipped = [], [], []
    existing = {c["filename"]: c for c in db.list_calls(limit=1000, offset=0, demo_only=True)} if skip_existing else {}
    for exp in (e for e in expected if not call_ids or e["call_id"] in call_ids):
        call_id = exp["call_id"]
        labels = demo_labels(exp)
        if demo_filename(exp) in existing:
            old = existing[demo_filename(exp)]
            if not old.get("title"):  # seeded before calls had titles
                db.update_call(old["id"], **labels)
            if audio and attach_audio(db, settings, old["id"], call_id, log) and uploaded is not None:
                uploaded.append(call_id)
            skipped.append(call_id)
            continue
        call = db.insert_call(filename=demo_filename(exp), source_url=None,
                              language=exp["language"], status="queued", is_demo=True, **labels)
        if source == "audio":
            ok = transcribe_call(db, dg, call["id"], audio=(AUDIO / f"{call_id}.wav").read_bytes(), mimetype="audio/wav",
                                 language="en", keyterms=merge_keyterms(settings.deepgram_keyterms, KEYTERMS))
        else:
            utterances, _ = sample_transcript(call_id)
            db.save_transcript(call["id"], utterances, provider="script")
            db.update_call(call["id"], status="scoring", duration_sec=utterances[-1]["end"])
            ok = True
        row = score_call(db, settings, call["id"], exp["rubric"], llm=llm) if ok else None
        if row is None:
            error = (db.get_call(call["id"]) or {}).get("error") or "failed"
            db.delete_call(call["id"])
            failed.append(call_id)
            log(f"{call_id}: failed and removed ({error[:120]})")
            if UNAVAILABLE.search(error):
                log("stopped: the LLM provider is unavailable; rerun later to add the remaining calls")
                break
            continue
        done.append(call_id)
        log(f"{call_id}: demo call ready, score {row['overall_score']}, passed {row['passed']}")
        if audio and attach_audio(db, settings, call["id"], call_id, log) and uploaded is not None:
            uploaded.append(call_id)
    return {"seeded": done, "failed": failed, "skipped": skipped}


def remove(db, log=print) -> int:
    calls = db.list_calls(limit=1000, offset=0, demo_only=True)
    for call in calls:
        db.delete_call(call["id"])
    log(f"removed {len(calls)} demo calls")
    return len(calls)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("calls", nargs="*", help="sample call ids (default: all 10)")
    ap.add_argument("--source", choices=["text", "audio"], default="text")
    ap.add_argument("--provider", choices=["anthropic", "openai", "gemini"], help="overrides LLM_PROVIDER")
    ap.add_argument("--remove", action="store_true", help="delete every demo call instead")
    ap.add_argument("--skip-existing", action="store_true", help="leave out samples that already have a demo call")
    ap.add_argument("--no-audio", action="store_true", help="do not upload the sample audio to Storage")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.CRITICAL)

    settings = Settings(**({"llm_provider": args.provider} if args.provider else {}))
    db = Supabase(settings.supabase_url, settings.supabase_service_key.get_secret_value())
    if args.remove:
        remove(db)
        return 0
    dg = None
    if args.source == "audio":
        dg = DeepgramClient(settings.deepgram_api_key.get_secret_value(), model=settings.deepgram_model,
                            base_url=settings.deepgram_base_url)
    uploaded: list[str] = []
    result = seed(db, settings, args.calls or None, args.source, dg, skip_existing=args.skip_existing,
                  audio=not args.no_audio, uploaded=uploaded)
    print(f"seeded {len(result['seeded'])}, failed {len(result['failed'])}, already there {len(result['skipped'])}, "
          f"audio uploaded {len(uploaded)}")
    return 0 if not result["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
