"""Demo audio for the player: uploaded by the seed script, transcript moved to the audio's timings.
Uses only committed files (samples/audio/*.mp3 and *.timings.json); the WAVs and the TTS cache are local only."""

import json
from pathlib import Path

from app.config import Settings
from app.llm import LLMResult
from tests.test_auth import SeedDB, seed_demo

VOICED = sorted(p.stem for p in seed_demo.AUDIO.glob("call_*.mp3"))


class AudioDB(SeedDB):
    def __init__(self):
        super().__init__()
        self.uploads, self.removed = {}, []

    def save_transcript(self, cid, utterances, provider="deepgram"):
        self.calls[cid]["transcripts"] = {"utterances": utterances, "provider": provider}

    def upload_object(self, bucket, path, data, content_type):
        self.uploads[(bucket, path)] = (len(data), content_type)

    def remove_objects(self, bucket, paths):
        self.removed += [(bucket, p) for p in paths]


scored = []


def llm(settings, system, user, schema):
    scored.append(1)
    return LLMResult({"items": []}, "fake", "m", 1, 1, 1, None)


def starts(call_id):
    return [line["start"] for line in seed_demo.audio_timings(call_id)]


def test_retime_needs_matching_lines():
    timings = seed_demo.audio_timings("call_03")
    utterances, _ = seed_demo.sample_transcript("call_03")
    moved = seed_demo.retime(utterances, timings)
    assert [u["start"] for u in moved] == starts("call_03") != [u["start"] for u in utterances]
    assert all(m["text"] == u["text"] and m["speaker"] == u["speaker"] for m, u in zip(moved, utterances, strict=True))
    assert seed_demo.retime(utterances[:-1], timings) is None
    assert seed_demo.retime([{**utterances[0], "text": "something else"}, *utterances[1:]], timings) is None


def test_seed_uploads_audio_and_moves_the_transcript_to_it():
    db, uploaded = AudioDB(), []
    seed_demo.seed(db, Settings(_env_file=None), ["call_03"], "text", llm=llm, log=lambda *_: None, audio=True, uploaded=uploaded)
    call = next(iter(db.calls.values()))
    assert uploaded == ["call_03"] and call["storage_path"] == "demo/v2/call_03.mp3"
    assert db.uploads[("call-audio", "demo/v2/call_03.mp3")] == ((seed_demo.AUDIO / "call_03.mp3").stat().st_size, "audio/mpeg")
    assert [u["start"] for u in call["transcripts"]["utterances"]] == starts("call_03")
    assert call["duration_sec"] == call["transcripts"]["utterances"][-1]["end"] and db.removed == []


def test_skip_existing_replaces_older_audio_without_scoring_again():
    db = AudioDB()
    seed_demo.seed(db, Settings(_env_file=None), ["call_01"], "text", llm=llm, log=lambda *_: None)
    call = next(iter(db.calls.values()))
    call["storage_path"] = "demo/call_01.mp3"  # seeded with the first audio
    scored.clear()
    first, again = [], []
    seed_demo.seed(db, Settings(_env_file=None), ["call_01"], "text", llm=llm, log=lambda *_: None,
                   skip_existing=True, audio=True, uploaded=first)
    assert first == ["call_01"] and call["storage_path"] == "demo/v2/call_01.mp3" and scored == []
    assert db.removed == [("call-audio", "demo/call_01.mp3")]
    assert [u["start"] for u in call["transcripts"]["utterances"]] == starts("call_01")
    seed_demo.seed(db, Settings(_env_file=None), ["call_01"], "text", llm=llm, log=lambda *_: None,
                   skip_existing=True, audio=True, uploaded=again)
    assert again == [] and len(db.uploads) == 1 and len(db.calls) == 1  # current audio: nothing to do


def test_a_call_without_current_audio_loses_the_old_file_and_other_audio_is_left_alone():
    db = AudioDB()
    call = db.insert_call(filename="x.wav", is_demo=True, storage_path="demo/call_05.mp3")
    assert seed_demo.attach_audio(db, Settings(_env_file=None), call["id"], "call_99", log=lambda *_: None) is False
    assert call["storage_path"] is None and db.removed == [("call-audio", "demo/call_05.mp3")]
    own = db.insert_call(filename="y.wav", is_demo=True, storage_path="0123abcd/upload.wav")
    assert seed_demo.attach_audio(db, Settings(_env_file=None), own["id"], "call_01", log=lambda *_: None) is False
    assert own["storage_path"] == "0123abcd/upload.wav" and not db.uploads


def test_seed_summary_line(monkeypatch, capsys):
    db = AudioDB()
    monkeypatch.setattr(seed_demo, "Supabase", lambda *a: db)
    monkeypatch.setattr(seed_demo, "seed", lambda *a, **k: (k["uploaded"].append("call_01"),
                                                            {"seeded": [], "failed": [], "skipped": ["call_01"]})[1])
    assert seed_demo.main(["--skip-existing"]) == 0
    assert capsys.readouterr().out.strip().endswith("seeded 0, failed 0, already there 1, audio uploaded 1")


def mp3_header(data: bytes) -> tuple[int, int, int]:
    """(bitrate in kbps, sample rate in Hz, channel mode) of the first audio frame, after LAME's Info/Xing header
    frame (which has its own bitrate); channel mode 3 is mono."""
    i = 0
    if data[:3] == b"ID3":
        i = 10 + ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
    while True:
        while not (data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0):
            i += 1
        b1, b2, b3 = data[i + 1], data[i + 2], data[i + 3]
        mpeg2 = not (b1 >> 3) & 1
        rates = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160] if mpeg2 else \
            [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
        sample_rates = [22050, 24000, 16000] if mpeg2 else [44100, 48000, 32000]
        kbps, rate = rates[b2 >> 4], sample_rates[(b2 >> 2) & 3]
        length = (72 if mpeg2 else 144) * kbps * 1000 // rate + ((b2 >> 1) & 1)
        if b"Info" not in data[i:i + 64] and b"Xing" not in data[i:i + 64]:
            return kbps, rate, b3 >> 6
        i += length


def test_committed_audio_is_neural_phone_sized_and_matches_its_timings():
    expected = {e["call_id"] for e in json.loads((Path(seed_demo.ROOT) / "samples/expected.json").read_text())["calls"]}
    assert len(VOICED) >= 8 and set(VOICED) <= expected
    total = 0
    for call_id in VOICED:
        data = (seed_demo.AUDIO / f"{call_id}.mp3").read_bytes()
        total += len(data)
        assert mp3_header(data[:200_000]) == (48, 24000, 3), call_id  # 48 kbps, 24 kHz, mono
        timing = json.loads((seed_demo.AUDIO / f"{call_id}.timings.json").read_text())
        voices = timing["voices"]
        assert {voices["AGENT"]["engine"], voices["CUSTOMER"]["engine"]} <= {"deepgram", "gemini"}, call_id
        assert voices["AGENT"]["voice"] != voices["CUSTOMER"]["voice"] and voices["AGENT"]["gender"] != voices["CUSTOMER"]["gender"]
        utterances, _ = seed_demo.sample_transcript(call_id)
        assert seed_demo.retime(utterances, timing["lines"]), call_id  # one timed line per script line
        ends = [(l["start"], l["end"]) for l in timing["lines"]]
        assert all(s < e <= n for (s, e), (n, _) in zip(ends, ends[1:], strict=False)), call_id  # in order, no overlap
    assert total < 7 * 1024 * 1024
