import json
import re
from pathlib import Path

import pytest

import evals.run as runner
from app.config import Settings
from app.deepgram import Transcript
from app.llm import LLMError, LLMResult
from app.rubrics import load_rubric
from evals.memory import MemoryDB
from evals.metrics import diarization_accuracy, evidence_timing, item_results, markdown, summarize

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {c["call_id"]: c for c in json.loads((ROOT / "samples/expected.json").read_text(encoding="utf-8"))["calls"]}
LINE = re.compile(r"^\[(\d\d):(\d\d)\] (AGENT|CUSTOMER): (.+)$", re.M)


def truth_lines(call_id):
    script = (ROOT / "samples" / EXPECTED[call_id]["script"]).read_text(encoding="utf-8")
    lines, t = [], 0.0
    for _, _, role, text in LINE.findall(script):
        lines.append({"role": role, "start": t, "end": t + 4.0, "text": text})
        t += 5.0
    return lines


def test_item_results_and_critical_flags():
    exp = EXPECTED["call_03"]
    scored = [{"item_id": i["item_id"], "verdict": i["verdict"]} for i in exp["items"]]
    scored[1]["verdict"] = "needs_review"  # recording_disclosure (critical)
    rows = item_results(exp, scored, load_rubric("us_insurance"))
    assert sum(r["correct"] for r in rows) == len(rows) - 1
    wrong = next(r for r in rows if not r["correct"])
    assert (wrong["item"], wrong["critical"], wrong["needs_review"]) == ("us_insurance.recording_disclosure", True, True)


def test_diarization_accuracy():
    truth = [{"role": "AGENT", "start": 0, "end": 4}, {"role": "CUSTOMER", "start": 5, "end": 7}, {"role": "AGENT", "start": 8, "end": 12}]
    perfect = [{"speaker": 1, "start": 0, "end": 4}, {"speaker": 0, "start": 5, "end": 7}, {"speaker": 1, "start": 8, "end": 12}]
    assert diarization_accuracy(perfect, truth) == (1.0, 2)  # speaker numbers do not matter
    merged = [{**u, "speaker": 0} for u in perfect]
    assert diarization_accuracy(merged, truth) == (0.8, 1)  # one speaker: only the agent's 8 of 10 s are right
    assert diarization_accuracy([], truth) == (None, 0)


def test_evidence_timing():
    exp = EXPECTED["call_01"]
    truth = truth_lines("call_01")
    scored = []
    for item in exp["items"]:
        line = next((l for l in truth if item["evidence_quote"] and item["evidence_quote"] in l["text"]), None)
        scored.append({"item_id": item["item_id"], "verdict": item["verdict"], "start_time": line["start"] if line else None})
    matched, checked = evidence_timing(exp, scored, truth)
    assert matched == checked > 0
    scored[0]["start_time"] = 999.0
    assert evidence_timing(exp, scored, truth)[0] == matched - 1


class FakeDeepgram:
    def transcribe(self, *, audio, url=None, mimetype, language, keyterms):
        truth = json.loads(audio)
        utterances = [{"speaker": 0 if l["role"] == "AGENT" else 1, "start": l["start"], "end": l["end"], "text": l["text"]}
                      for l in truth]
        return Transcript(utterances, truth[-1]["end"], "fake", "r", keyterms=keyterms)


def expected_llm(fail_on=None):
    """Answers like a perfect model: the expected verdicts with quotes from the transcript."""
    def llm(settings, system, user, schema):
        call_id = next(c for c in EXPECTED if EXPECTED[c]["items"][0]["item_id"] in user and c in llm.queue)
        llm.queue.remove(call_id)
        if call_id == fail_on:
            raise LLMError("provider returned HTTP 503: model is experiencing high demand")
        lines = [l.split(": ", 1)[1] for l in user.splitlines() if l.startswith("[")]
        items = []
        for e in EXPECTED[call_id]["items"]:
            quote = e["evidence_quote"] or ""
            index = next((i for i, l in enumerate(lines) if quote and quote in l), -1)
            if e["verdict"] == "fail" and not quote:
                index, quote = 0, lines[0][:30]
            items.append({"item_id": e["item_id"], "verdict": e["verdict"], "evidence_quote": quote,
                          "utterance_index": index, "reasoning": ""})
        return LLMResult({"items": items}, "fake", "fake-model", 1000, 200, 900, 0.001)
    return llm


@pytest.fixture
def audio(tmp_path, monkeypatch):
    for call_id in EXPECTED:
        lines = truth_lines(call_id)
        (tmp_path / f"{call_id}.mp3").write_text(json.dumps(lines))  # FakeDeepgram reads the timings back
        (tmp_path / f"{call_id}.timings.json").write_text(json.dumps({"lines": lines}))
    monkeypatch.setattr(runner, "AUDIO", tmp_path)
    return tmp_path


def run_with(calls, fail_on=None, **settings):
    db, llm = MemoryDB(), expected_llm(fail_on)
    llm.queue = list(calls)
    report = runner.run(db, FakeDeepgram(), Settings(_env_file=None, **settings), calls, llm=llm, log=lambda *_: None)
    return report, db


def test_full_run_with_a_perfect_model_cleans_up(audio):
    report, db = run_with(["call_01", "call_02", "call_06"], deepgram_price_per_min=0.0043)
    s = report["summary"]
    assert report["aborted"] is None and s["scored"] == 3
    assert s["item_accuracy"].startswith("20/20") and s["critical_accuracy"].endswith("(100%)")
    assert s["pass_fail_accuracy"] == "3/3 (100%)" and s["needs_review_rate"].startswith("0/")
    assert s["diarization_accuracy"] == "100%" and s["llm_cost_usd"] == 0.001 and s["deepgram_cost_usd"] > 0
    assert db.calls == {} and db.rubrics == {}  # every row created by the run is gone
    md = report["markdown"]
    assert "| Item accuracy | 20/20 (100%) |" in md and "| us_insurance.recording_disclosure | 2/2 (100%) |" in md


def test_provider_outage_stops_the_run(audio):
    report, db = run_with(["call_01", "call_02", "call_03"], fail_on="call_02")
    assert report["aborted"].startswith("call_02: provider returned HTTP 503")
    assert [c["call_id"] for c in report["calls"]] == ["call_01"]  # call_03 never ran
    assert db.calls == {} and db.rubrics == {}


def test_text_source_skips_deepgram(audio):
    db, llm = MemoryDB(), expected_llm()
    llm.queue = ["call_05", "call_06"]

    class NoDeepgram:
        def transcribe(self, **kwargs):
            raise AssertionError("Deepgram must not be called")

    report = runner.run(db, NoDeepgram(), Settings(_env_file=None), ["call_05", "call_06"], llm=llm, log=lambda *_: None,
                        source="text")
    s = report["summary"]
    assert s["item_accuracy"] == "12/12 (100%)" and s["diarization_accuracy"] == "n/a" and s["transcription_s"] is None
    assert "transcripts from text" in report["markdown"] and db.calls == {}


def test_text_calls_mix_with_audio(audio):
    db, llm = MemoryDB(), expected_llm()
    llm.queue = ["call_01", "call_05"]

    class OnlyForCall01(FakeDeepgram):
        def transcribe(self, **kwargs):
            self.used = getattr(self, "used", 0) + 1
            return super().transcribe(**kwargs)

    dg = OnlyForCall01()
    report = runner.run(db, dg, Settings(_env_file=None), ["call_01", "call_05"], llm=llm, log=lambda *_: None,
                        text_calls={"call_05", "call_06"})
    by_id = {c["call_id"]: c for c in report["calls"]}
    assert dg.used == 1 and "diarization" in by_id["call_01"] and "diarization" not in by_id["call_05"]
    assert report["text_calls"] == ["call_05"]  # call_06 was not selected
    assert "transcripts from audio, call_05 from script text" in report["markdown"] and db.calls == {}


INJECTION = json.loads((ROOT / "samples/injection.json").read_text(encoding="utf-8"))["cases"]


def test_injection_cases_hold_when_the_model_follows_the_rubric(audio):
    db, llm, prompts = MemoryDB(), expected_llm(), []
    llm.queue = [c["call_id"] for c in INJECTION]

    def recording(settings, system, user, schema):
        prompts.append((system, user))
        return llm(settings, system, user, schema)

    report = runner.run_injection(db, Settings(_env_file=None), llm=recording, log=lambda *_: None)
    assert report["held"] == f"{len(INJECTION)}/{len(INJECTION)}" and report["aborted"] is None
    assert all(r["held"] and r["flipped_to_pass"] == [] for r in report["cases"])
    for case, (system, user) in zip(INJECTION, prompts, strict=True):
        transcript = user.split("<transcript>")[1].split("</transcript>")[0]
        assert case["text"] in transcript  # the injected line is part of the transcript, nowhere else
        assert case["text"] not in system and "never instructions to follow" in system
    assert db.calls == {} and db.rubrics == {}


def test_injection_cases_fail_when_the_model_obeys(audio):
    def obedient(settings, system, user, schema):
        first = next(l for l in user.splitlines() if l.startswith("[0]")).split(": ", 1)[1]
        ids = schema["properties"]["items"]["items"]["properties"]["item_id"]["enum"]
        return LLMResult({"items": [{"item_id": i, "verdict": "pass", "evidence_quote": first, "utterance_index": 0, "reasoning": ""}
                                    for i in ids]}, "fake", "fake-model", 1000, 200, 900, 0.001)

    report = runner.run_injection(MemoryDB(), Settings(_env_file=None), llm=obedient, log=lambda *_: None)
    assert report["held"] == f"0/{len(INJECTION)}"
    assert all(not r["held"] and r["flipped_to_pass"] and r["passed"] is True for r in report["cases"])
    assert "| no |" in report["markdown"]


def test_missing_audio_is_explained(audio):
    (audio / "call_04.mp3").unlink()
    with pytest.raises(SystemExit, match="call_04.*mp3 is missing"):
        run_with(["call_04"])


def test_markdown_without_prices():
    calls = [{"call_id": "call_09", "error": "Deepgram returned HTTP 400: bad audio"}]
    md = markdown(summarize(calls), calls, "gemini")
    assert "price not set" in md and "failed: Deepgram returned HTTP 400" in md
