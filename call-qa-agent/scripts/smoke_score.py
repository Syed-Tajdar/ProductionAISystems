"""Live smoke check for scoring: synthetic transcript in, score out, rows removed.

    python scripts/smoke_score.py                       # call_03 with LLM_PROVIDER from the environment
    python scripts/smoke_score.py call_08 --provider gemini

Builds a transcript from a sample script (no audio, no Deepgram), stores it in Supabase,
scores it with the configured provider, compares each verdict with samples/expected.json,
then deletes every row it created. Prints results only, never keys, headers or URLs.
Exit code 0 when scoring succeeded, 1 otherwise.
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.db import Supabase  # noqa: E402
from app.pipeline import score_call  # noqa: E402

LINE = re.compile(r"^\[(\d\d):(\d\d)\] (AGENT|CUSTOMER): (.+)$", re.M)


def sample_transcript(call_id: str) -> tuple[list[dict], dict]:
    expected = {c["call_id"]: c for c in json.loads((ROOT / "samples/expected.json").read_text(encoding="utf-8"))["calls"]}
    if call_id not in expected:
        raise SystemExit(f"unknown sample {call_id}, use one of {', '.join(expected)}")
    script = (ROOT / "samples" / expected[call_id]["script"]).read_text(encoding="utf-8")
    lines = LINE.findall(script)
    starts = [int(m) * 60 + int(s) for m, s, _, _ in lines]
    utterances = [{"speaker": 0 if who == "AGENT" else 1, "start": float(start),
                   "end": float(starts[i + 1] - 0.5) if i + 1 < len(starts) else float(start + 4),
                   "text": text, "confidence": 1.0}
                  for i, ((_, _, who, text), start) in enumerate(zip(lines, starts, strict=True))]
    return utterances, expected[call_id]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sample", nargs="?", default="call_03", help="sample call id from samples/expected.json")
    ap.add_argument("--provider", choices=["anthropic", "openai", "gemini"], help="overrides LLM_PROVIDER")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings(**({"llm_provider": args.provider} if args.provider else {}))
    db = Supabase(settings.supabase_url, settings.supabase_service_key.get_secret_value())
    utterances, expected = sample_transcript(args.sample)
    rubric_slug = expected["rubric"]
    rubric_existed = db.get_rubric(rubric_slug, 1) is not None

    call = db.insert_call(filename=f"smoke-check_{args.sample}.json", source_url=None, language=expected["language"],
                          status="scoring")
    ok = False
    try:
        db.save_transcript(call["id"], utterances, provider="synthetic")
        print(f"{args.sample}: {len(utterances)} utterances, rubric {rubric_slug}, provider {settings.llm_provider}")
        started = time.monotonic()
        row = score_call(db, settings, call["id"], rubric_slug)
        stored = db.get_call(call["id"])
        print(f"scoring took {time.monotonic() - started:.1f} s, call status {stored['status']}")
        if row is None:
            print(f"scoring failed: {stored['error']}")
        else:
            ok = True
            score = stored["scores"][0]
            print(f"score {score['overall_score']} (expected {expected['expected_overall_score']}), "
                  f"passed {score['passed']} (expected {expected['expected_passed']}), model {score['model']}, "
                  f"tokens {score['input_tokens']} in / {score['output_tokens']} out, "
                  f"{score['latency_ms']} ms, cost {score['cost_usd']}")
            want = {i["item_id"]: i["verdict"] for i in expected["items"]}
            agree = 0
            for item in score["items"]:
                same = item["verdict"] == want[item["item_id"]]
                agree += same
                note = f" [model said {item['model_verdict']}: {item['review_reason']}]" if item.get("model_verdict") else ""
                print(f"  {'ok  ' if same else 'DIFF'} {item['item_id']:24} {item['verdict']:12} expected {want[item['item_id']]:5}"
                      f" @{item['start_time']} \"{item['evidence_quote'][:60]}\"{note}")
            print(f"agreement with expected.json: {agree}/{len(want)}")
    finally:
        db.delete_call(call["id"])  # transcript and scores cascade
        rubric = db.get_rubric(rubric_slug, 1)
        if rubric and not rubric_existed:
            db.delete_rubric(rubric["id"])
        print("cleanup: test call, transcript, score" + ("" if rubric_existed else " and rubric row") + " deleted")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
