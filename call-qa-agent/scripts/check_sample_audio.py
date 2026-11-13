"""Check the demo audio without listening to it: decodes each samples/audio/<call>.mp3 the way a player would and
measures it against its timings file.

    python scripts/check_sample_audio.py            # all calls with an MP3
    python scripts/check_sample_audio.py --stt      # also transcribe English calls with Deepgram and compare words

Checks per file (FAIL makes the exit code 1):
    format     mp3, mono, 24 kHz, about 48 kbps
    timing     lines in order, no overlaps, gaps of 0.25-0.6 s, file ends just after the last line
    clipping   no decoded sample at full scale; true peak (4x oversampled) below -0.5 dBTP
    loudness   every line within 2 LU of -16 LUFS; whole file reported
    dropouts   no sudden hole inside a line: 30 ms or more below -60 dBFS with speech right before and after. A hole
               that is also in the line's TTS take (local cache or committed take) is the voice's own short pause,
               such as after "Great.", and is listed, not failed; without a take to compare, every hole fails.
    joins      silence between lines stays below -50 dBFS, so no click or noise at a join
    clicks     no spike where processing could add one: within 20 ms of a line's start or end, or in the gaps.
               Spikes in quiet moments inside a line are listed for information: they are in the TTS output itself
               (consonant bursts after a short pause), not added here.
    words      with --stt: share of script words Deepgram hears back (English calls)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pyloudnorm
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # make_sample_audio, for the TTS takes
AUDIO = ROOT / "samples" / "audio"
RATE = 24_000


def decode(mp3: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(mp3), "-f", "f32le", "-ac", "1", "-ar", str(RATE),
                          "pipe:"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<f4").astype(np.float64)


def probe(mp3: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json",
                          str(mp3)], capture_output=True, check=True, text=True).stdout
    return json.loads(out)["streams"][0]


def db(x: float) -> float:
    return float(20 * np.log10(max(x, 1e-12)))


def frames_db(audio: np.ndarray, ms: int = 5) -> np.ndarray:
    n = int(RATE * ms / 1000)
    k = len(audio) // n
    return 20 * np.log10(np.sqrt(np.mean(audio[:k * n].reshape(k, n) ** 2, axis=1)) + 1e-12)


def lufs(audio: np.ndarray) -> float:
    reps = max(1, int(np.ceil(2 * RATE / max(len(audio), 1))))
    return float(pyloudnorm.Meter(RATE).integrated_loudness(np.tile(audio, reps)))


def holes(line: np.ndarray) -> list[float]:
    """Seconds into the line of sudden gaps: >= 30 ms below -60 dBFS with speech (> -35 dBFS) within 20 ms on both sides."""
    f = frames_db(line)[6:-6]  # leave out the 30 ms at each edge (fades)
    quiet, loud = f < -60, f > -35
    found, i = [], 0
    while i < len(f):
        if quiet[i]:
            j = i
            while j < len(f) and quiet[j]:
                j += 1
            if j - i >= 6 and loud[max(0, i - 4):i].any() and loud[j:j + 4].any():
                found.append((i + 6) * 0.005)
            i = j
        else:
            i += 1
    return found


def source_holes(call_id: str, line: dict) -> list[float] | None:
    """Holes in the line's TTS take, leveled the same way; None when no take is available here."""
    import make_sample_audio as maker
    plan = maker.VOICES[call_id]
    try:
        raw = maker.TTS().say(plan["engine"], plan[line["role"]][0], line["text"], offline=True)
    except LookupError:
        return None
    return holes(maker.level(maker.trim(raw)))


def click_times(audio: np.ndarray) -> np.ndarray:
    """Seconds of spikes that would be heard: a second-difference spike above -30 dBFS where the 10 ms on both sides
    is otherwise below -45 dBFS. Inside running speech consonants make such jumps and mask them, so only quiet counts."""
    d2 = np.abs(np.diff(audio, 2))
    w = int(0.005 * RATE)
    k = len(d2) // w
    frames = d2[:k * w].reshape(k, w)
    peak = frames.max(axis=1)
    rms = 20 * np.log10(np.sqrt(np.mean(audio[:k * w].reshape(k, w) ** 2, axis=1)) + 1e-12)
    quiet_around = np.minimum(np.r_[rms[1:], -200], np.r_[-200, rms[:-1]]) < -45
    neighbours = np.maximum(np.r_[rms[2:], -200, -200], np.r_[-200, -200, rms[:-2]]) < -45
    return np.flatnonzero((peak > 10 ** (-30 / 20)) & quiet_around & neighbours) * w / RATE


def clicks(audio: np.ndarray) -> int:
    return len(click_times(audio))


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def stt_recall(mp3: Path, lines: list[dict]) -> float:
    import httpx
    r = httpx.post("https://api.deepgram.com/v1/listen", params={"model": "nova-3", "smart_format": "false", "language": "en"},
                   headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "audio/mpeg"},
                   content=mp3.read_bytes(), timeout=180)
    r.raise_for_status()
    heard = set(words(r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]))
    script = [w for line in lines for w in words(line["text"])]
    return sum(w in heard for w in script) / max(len(script), 1)


def check(mp3: Path, stt: bool) -> tuple[list[str], dict]:
    timing = json.loads((mp3.parent / f"{mp3.stem}.timings.json").read_text(encoding="utf-8"))
    lines = timing["lines"]
    fails, info = [], {}
    p = probe(mp3)
    kbps = int(p.get("bit_rate", 0)) // 1000
    info["format"] = f"{p['codec_name']} {p['sample_rate']} Hz {p['channels']} ch {kbps} kbps"
    if (p["codec_name"], int(p["sample_rate"]), int(p["channels"])) != ("mp3", RATE, 1) or not 44 <= kbps <= 52:
        fails.append(f"format {info['format']}")

    audio = decode(mp3)
    seconds = len(audio) / RATE
    info["seconds"] = round(seconds, 1)
    for a, b in zip(lines, lines[1:], strict=False):  # each line with the next
        gap = b["start"] - a["end"]
        if not 0.25 <= gap <= 0.6:
            fails.append(f"gap {gap:.3f} s before line {b['index']}")
    if lines[0]["start"] < 0.1 or not 0.2 <= seconds - lines[-1]["end"] <= 0.9:
        fails.append(f"edges: first line at {lines[0]['start']} s, {seconds - lines[-1]['end']:.2f} s after the last")

    full_scale = int(np.sum(np.abs(audio) >= 0.999))
    tp = db(float(np.max(np.abs(resample_poly(audio, 4, 1)))))
    info["peak"] = f"{db(float(np.max(np.abs(audio)))):.1f} dBFS, true peak {tp:.1f} dBTP, {full_scale} full-scale samples"
    if full_scale or tp > -0.5:
        fails.append(f"clipping: {info['peak']}")

    levels, hole_count, pauses, join_max = [], 0, 0, -200.0
    for i, line in enumerate(lines):
        s, e = int(line["start"] * RATE), int(line["end"] * RATE)
        levels.append(lufs(audio[s:e]))
        found = holes(audio[s:e])
        if found:
            source = source_holes(mp3.stem, line)
            inherent = [t for t in found if source and any(abs(t - u) <= 0.02 for u in source)]
            pauses += len(inherent)
            hole_count += len(found) - len(inherent)
        nxt = int(lines[i + 1]["start"] * RATE) if i + 1 < len(lines) else len(audio)
        gap = audio[e + int(0.02 * RATE):nxt - int(0.02 * RATE)]  # 20 ms margin for the codec's smearing
        if len(gap):
            join_max = max(join_max, float(np.max(frames_db(gap, 2))) if len(gap) > 48 else db(float(np.max(np.abs(gap)))))
    spread = max(abs(x + 16) for x in levels)
    info["loudness"] = f"file {lufs(audio):.1f} LUFS, lines {min(levels):.1f} to {max(levels):.1f} LUFS"
    if spread > 2:
        fails.append(f"loudness: a line is {spread:.1f} LU from -16")
    info["dropouts"] = hole_count
    info["pauses"] = pauses
    if hole_count:
        fails.append(f"{hole_count} dropout(s) inside lines")
    info["joins"] = f"loudest 2 ms in the gaps {join_max:.1f} dBFS"
    if join_max > -50:
        fails.append(f"join noise {join_max:.1f} dBFS")
    edges = [(line["start"] + 0.02, line["end"] - 0.02) for line in lines]
    inside = [t for t in click_times(audio) if any(s <= t <= e for s, e in edges)]
    info["clicks"] = len(click_times(audio)) - len(inside)
    info["bursts"] = len(inside)
    if info["clicks"]:
        fails.append(f"{info['clicks']} click(s) at line edges or in gaps")
    if stt and timing["voices"]["AGENT"]["engine"] == "deepgram":
        recall = stt_recall(mp3, lines)
        info["words"] = f"{100 * recall:.1f}% of script words heard"
        if recall < 0.9:
            fails.append(f"words: {info['words']}")
    return fails, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("calls", nargs="*", help="call ids (default: every MP3 in samples/audio)")
    ap.add_argument("--stt", action="store_true", help="transcribe English calls with Deepgram (about $0.005 a call)")
    args = ap.parse_args(argv)
    files = sorted(p for p in AUDIO.glob("call_*.mp3") if not args.calls or p.stem in args.calls)
    failed = 0
    for mp3 in files:
        fails, info = check(mp3, args.stt)
        failed += bool(fails)
        print(f"{mp3.stem}: {'FAIL' if fails else 'ok'}  {info['seconds']} s, {info['format']}")
        print(f"    {info['peak']}; {info['loudness']}")
        print(f"    dropouts {info['dropouts']} (short pauses in the TTS take {info['pauses']}), "
              f"clicks at edges/gaps {info['clicks']} (bursts inside lines {info['bursts']}), {info['joins']}"
              + (f"; {info['words']}" if "words" in info else ""))
        for f in fails:
            print(f"    FAIL {f}")
    print(f"{len(files) - failed} of {len(files)} files pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
