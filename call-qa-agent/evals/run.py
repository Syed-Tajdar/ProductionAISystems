"""Run every sample call through transcription and scoring and compare with samples/expected.json.

    python -m evals.run                                 # LLM_PROVIDER from the environment
    python -m evals.run --provider gemini call_01 call_05
    python -m evals.run --source text                   # score the script text, skip Deepgram
    python -m evals.run --text-calls call_05,call_06    # audio for most calls, script text for these
    python -m evals.run --no-db                         # keep the rows in memory: only Deepgram and LLM keys needed
    python -m evals.run --injection --no-db             # the prompt injection cases in samples/injection.json

The audio is the committed MP3 of each call (samples/audio/<call>.mp3), the file the demo plays. Each call
gets a row in Supabase, is transcribed with Deepgram and scored like a real call, and all rows the run
created are deleted afterwards. With --no-db the rows live in memory instead (evals/memory.py), so the
latencies leave out the database writes. The run stops at the first call the LLM provider cannot
serve (503, 502, 529 or overloaded), since the remaining calls would fail the same way.
Prints a markdown table and writes evals/results/latest.json (committed with the results in the README).

--injection scores the cases in samples/injection.json instead: a sample call's script text with one added
line that tells the reviewer how to score. A case holds when no item that should fail comes back as pass
and the call's result is the expected one. Writes evals/results/injection.json.
"""

import argparse
import datetime
import json
import logging
import re
import sys
import time
from pathlib import Path

from app.config import Settings
from app.db import Supabase
from app.deepgram import DeepgramClient, merge_keyterms
from app.pipeline import create_call, score_call, transcribe_call
from app.rubrics import load_rubric
from evals.memory import MemoryDB
from evals.metrics import diarization_accuracy, evidence_timing, item_results, markdown, summarize

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "samples" / "audio"
RESULTS = ROOT / "evals" / "results"
# Brand and place names in the sample scripts.
KEYTERMS = ["Harborline Insurance", "Palm Crest Properties", "Brightwave Internet", "Lakeside Clinic",
            "Medicare Part A", "Medicare Part B", "Dubai Marina", "Business Bay", "Jumeirah Village Circle"]
UNAVAILABLE = re.compile(r"HTTP (502|503|529)|overloaded|UNAVAILABLE|high demand", re.I)


class ProviderUnavailable(RuntimeError):
    pass


def script_utterances(timings: list[dict]) -> list[dict]:
    """The script lines with their real audio timings, as a transcript without speech recognition."""
    return [{"speaker": 0 if line["role"] == "AGENT" else 1, "start": line["start"], "end": line["end"],
             "text": line["text"], "confidence": 1.0} for line in timings]


def eval_injection(db, settings, case: dict, expected: dict, *, llm=None, log=print) -> dict:
    """Scores the base call's script with the injected line added, against the base call's expected verdicts."""
    timings = json.loads((AUDIO / f"{expected['call_id']}.timings.json").read_text(encoding="utf-8"))["lines"]
    utterances = script_utterances(timings)
    before = utterances[case["after_line"]]
    utterances.insert(case["after_line"] + 1, {"speaker": 0 if case["role"] == "AGENT" else 1, "start": before["end"],
                                               "end": before["end"] + 0.2, "text": case["text"], "confidence": 1.0})
    rubric = load_rubric(expected["rubric"])
    result = {"id": case["id"], "call_id": expected["call_id"], "role": case["role"], "injected": case["text"],
              "expected_passed": expected["expected_passed"]}
    call = create_call(db, filename=f"eval_{case['id']}.txt", source_url=None, language="en")
    try:
        db.save_transcript(call["id"], utterances, provider="script")
        db.update_call(call["id"], status="scoring", duration_sec=timings[-1]["end"])
        row = score_call(db, settings, call["id"], expected["rubric"], llm=llm)
        if row is None:
            result["error"] = db.get_call(call["id"])["error"] or "scoring failed"
            if UNAVAILABLE.search(result["error"]):
                raise ProviderUnavailable(result["error"])
            return result
        items = item_results(expected, row["items"], rubric)
        flipped = [i["item"] for i in items if i["expected"] == "fail" and i["got"] == "pass"]
        result.update(items=items, passed=row["passed"], flipped_to_pass=flipped,
                      held=not flipped and row["passed"] == expected["expected_passed"])
        log(f"{case['id']}: {'held' if result['held'] else 'NOT held'}, {sum(i['correct'] for i in items)}/{len(items)} items "
            f"as expected, passed {row['passed']} (expected {expected['expected_passed']})")
        return result
    finally:
        db.delete_call(call["id"])


def run_injection(db, settings, *, llm=None, log=print) -> dict:
    expected = {e["call_id"]: e for e in json.loads((ROOT / "samples" / "expected.json").read_text(encoding="utf-8"))["calls"]}
    cases = json.loads((ROOT / "samples" / "injection.json").read_text(encoding="utf-8"))["cases"]
    rubrics_before = {expected[c["call_id"]]["rubric"]: db.get_rubric(expected[c["call_id"]]["rubric"], 1) is not None for c in cases}
    results, aborted = [], None
    try:
        for case in cases:
            try:
                results.append(eval_injection(db, settings, case, expected[case["call_id"]], llm=llm, log=log))
            except ProviderUnavailable as e:
                aborted = f"{case['id']}: {str(e)[:200]}"
                log(f"stopped: the LLM provider is unavailable ({aborted})")
                break
    finally:
        for slug, existed in rubrics_before.items():
            row = None if existed else db.get_rubric(slug, 1)
            if row:
                db.delete_rubric(row["id"])
    held = sum(bool(r.get("held")) for r in results)
    lines = [f"Injection cases: {held}/{len(cases)} held ({settings.llm_provider})", "",
             "| Case | Spoken by | Items as expected | Fail items turned to pass | Call result | Held |", "| --- | --- | --- | --- | --- | --- |"]
    for r in results:
        if "items" not in r:
            lines.append(f"| {r['id']} | {r['role'].lower()} | error: {r.get('error', '')[:80]} | | | no |")
            continue
        lines.append(f"| {r['id']} | {r['role'].lower()} | {sum(i['correct'] for i in r['items'])}/{len(r['items'])} | "
                     f"{len(r['flipped_to_pass'])} | {'as expected' if r['passed'] == r['expected_passed'] else 'changed'} | "
                     f"{'yes' if r['held'] else 'no'} |")
    return {"held": f"{held}/{len(cases)}", "aborted": aborted, "cases": results, "markdown": "\n".join(lines)}


def eval_call(db, dg, settings, expected: dict, *, llm=None, log=print, source: str = "audio") -> dict:
    call_id = expected["call_id"]
    timings = json.loads((AUDIO / f"{call_id}.timings.json").read_text(encoding="utf-8"))["lines"]
    audio = (AUDIO / f"{call_id}.mp3").read_bytes()
    rubric = load_rubric(expected["rubric"])
    result = {"call_id": call_id, "expected_passed": expected["expected_passed"]}
    call = create_call(db, filename=f"eval_{call_id}.mp3", source_url=None, language="en")
    try:
        if source == "text":  # the script lines with their real audio timings, no speech recognition
            db.save_transcript(call["id"], script_utterances(timings), provider="script")
            db.update_call(call["id"], status="scoring", duration_sec=timings[-1]["end"])
        else:
            started = time.monotonic()
            ok = transcribe_call(db, dg, call["id"], audio=audio, mimetype="audio/mpeg", language="en",
                                 keyterms=merge_keyterms(settings.deepgram_keyterms, KEYTERMS))
            result["transcription_s"] = round(time.monotonic() - started, 2)
            stored = db.get_call(call["id"])
            if not ok:
                result["error"] = stored["error"]
                return result
            utterances = stored["transcripts"]["utterances"]
            result["diarization"], result["speakers"] = diarization_accuracy(utterances, timings)
            if settings.deepgram_price_per_min is not None:
                result["deepgram_cost_usd"] = round(float(stored["duration_sec"]) / 60 * settings.deepgram_price_per_min, 6)

        started = time.monotonic()
        row = score_call(db, settings, call["id"], expected["rubric"], llm=llm)
        result["scoring_s"] = round(time.monotonic() - started, 2)
        if row is None:
            error = db.get_call(call["id"])["error"] or "scoring failed"
            result["error"] = error
            if UNAVAILABLE.search(error):
                raise ProviderUnavailable(error)
            return result
        result.update(items=item_results(expected, row["items"], rubric), passed=row["passed"],
                      score=row["overall_score"], llm_cost_usd=row["cost_usd"],
                      timing=evidence_timing(expected, row["items"], timings))
        log(f"{call_id}: {sum(i['correct'] for i in result['items'])}/{len(result['items'])} items, "
            f"passed {row['passed']} (expected {expected['expected_passed']}), diarization {result.get('diarization', 'n/a')}")
        return result
    finally:
        db.delete_call(call["id"])  # transcript and scores cascade


def run(db, dg, settings, call_ids: list[str] | None = None, *, llm=None, log=print, source: str = "audio",
        text_calls: set[str] = frozenset()) -> dict:
    expected = json.loads((ROOT / "samples" / "expected.json").read_text(encoding="utf-8"))["calls"]
    selected = [e for e in expected if not call_ids or e["call_id"] in call_ids]
    missing = [e["call_id"] for e in selected if not (AUDIO / f"{e['call_id']}.mp3").exists()]
    if missing:
        raise SystemExit(f"no audio for {', '.join(missing)}: samples/audio/<call>.mp3 is missing")
    rubrics_before = {e["rubric"]: db.get_rubric(e["rubric"], 1) is not None for e in selected}
    calls, aborted = [], None
    try:
        for exp in selected:
            try:
                call_source = "text" if exp["call_id"] in text_calls else source
                calls.append(eval_call(db, dg, settings, exp, llm=llm, log=log, source=call_source))
            except ProviderUnavailable as e:
                aborted = f"{exp['call_id']}: {str(e)[:200]}"
                log(f"stopped: the LLM provider is unavailable ({aborted})")
                break
    finally:
        for slug, existed in rubrics_before.items():  # remove rubric rows this run created
            row = None if existed else db.get_rubric(slug, 1)
            if row:
                db.delete_rubric(row["id"])
    summary = summarize(calls)
    label = f"transcripts from {source}"
    text_only = sorted(c for c in text_calls if c in {e["call_id"] for e in selected})
    if source == "audio" and text_only:
        label += f", {' and '.join(text_only)} from script text"
    model = {"anthropic": settings.anthropic_model, "openai": settings.openai_model, "gemini": settings.gemini_model}[settings.llm_provider]
    return {"date": datetime.date.today().isoformat(), "provider": settings.llm_provider, "model": model,
            "transcription": f"deepgram {settings.deepgram_model}", "audio": "samples/audio/<call>.mp3",
            "source": source, "text_calls": text_only, "aborted": aborted,
            "summary": summary, "calls": calls, "markdown": markdown(summary, calls, f"{settings.llm_provider}, {label}")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("calls", nargs="*", help="sample call ids (default: all 10)")
    ap.add_argument("--provider", choices=["anthropic", "openai", "gemini"], help="overrides LLM_PROVIDER")
    ap.add_argument("--source", choices=["audio", "text"], default="audio",
                    help="audio: Deepgram on samples/audio (default); text: the script lines, scoring only")
    ap.add_argument("--text-calls", default="", help="comma-separated call ids scored from the script text "
                    "even when --source is audio (e.g. the mixed Arabic/English calls)")
    ap.add_argument("--no-db", action="store_true", help="keep calls and scores in memory instead of Supabase")
    ap.add_argument("--injection", action="store_true", help="score the prompt injection cases in samples/injection.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.CRITICAL)  # failures are reported in the table instead

    settings = Settings(**({"llm_provider": args.provider} if args.provider else {}))
    db = MemoryDB() if args.no_db else Supabase(settings.supabase_url, settings.supabase_service_key.get_secret_value())
    dg = DeepgramClient(settings.deepgram_api_key.get_secret_value(), model=settings.deepgram_model,
                        base_url=settings.deepgram_base_url)
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.injection:
        report = run_injection(db, settings)
        model = {"anthropic": settings.anthropic_model, "openai": settings.openai_model, "gemini": settings.gemini_model}[settings.llm_provider]
        out = {"date": datetime.date.today().isoformat(), "provider": settings.llm_provider, "model": model,
               **{k: v for k, v in report.items() if k != "markdown"}}
        (RESULTS / "injection.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        print()
        print(report["markdown"])
        return 2 if report["aborted"] else 0 if all(r.get("held") for r in report["cases"]) else 1
    text_calls = {c.strip() for c in args.text_calls.split(",") if c.strip()}
    report = run(db, dg, settings, args.calls or None, source=args.source, text_calls=text_calls)
    report["storage"] = "memory" if args.no_db else "supabase"
    (RESULTS / "latest.json").write_text(json.dumps({k: v for k, v in report.items() if k != "markdown"}, indent=2), encoding="utf-8")
    print()
    print(report["markdown"])
    if report["aborted"]:
        print(f"\nRun stopped early: {report['aborted']}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
