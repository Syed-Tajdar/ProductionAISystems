"""Voice the sample call scripts with neural TTS, line by line, into the demo audio.

    pip install -e ".[audio]"                      # numpy, scipy, pyloudnorm; ffmpeg on the PATH
    python scripts/make_sample_audio.py            # all ten calls
    python scripts/make_sample_audio.py call_05    # one call

English-only calls use Deepgram Aura-2 (DEEPGRAM_API_KEY). The two calls that mix Arabic and English (call_05,
call_06) use Gemini TTS (GEMINI_API_KEY) for every line: their lines switch language mid-sentence, and one
engine per call keeps each speaker's voice the same throughout. Every call has two clearly different voices,
matched to the names in the script (five female and five male agents).

Each line is synthesized on its own, checked (real speech at a plausible rate, else requested again), trimmed,
resampled to 24 kHz, low-passed at 9.5 kHz (the 48 kbps MP3 drops the rest) and de-essed, since loud "s" sounds are
harsh on phone speakers and make the encoder overshoot. Then, a few rounds per line: normalize to -16 LUFS and run a look-ahead limiter with a -3 dBFS ceiling,
so every line ends at the same loudness with its peaks under control. Each line gets an 8 ms fade at both ends and
turns are joined with silence. The timings file records exactly where each line sits, so the
transcript and the player stay in step. Writes per call:
    samples/audio/<call>.mp3            mono 48 kbps, 24 kHz (committed; the demo player streams it)
    samples/audio/<call>.timings.json   start and end of every line (committed)
    samples/audio/<call>.wav            16-bit 24 kHz (local, gitignored; for Deepgram eval runs)
TTS responses are cached in samples/audio/.tts-cache/ (gitignored), so a rerun costs nothing. Gemini takes are also
kept in samples/audio/takes/ (committed, Opus): its free tier allows 10 requests a day, so a call can take several
days to voice, and the lines already voiced must survive a fresh checkout. When the daily quota runs out the script
stops, keeps what it has, and says how many lines are left; run it again after the reset (midnight Pacific).
Check the result with scripts/check_sample_audio.py.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import wave
from math import gcd
from pathlib import Path

import httpx
import numpy as np
import pyloudnorm
from scipy.signal import butter, resample_poly, sosfiltfilt

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "samples" / "scripts"
OUT = ROOT / "samples" / "audio"
CACHE = OUT / ".tts-cache"
TAKES = OUT / "takes"
LINE = re.compile(r"^\[\d\d:\d\d\] (AGENT|CUSTOMER): (.+)$")
ARABIC = re.compile(r"[\u0600-\u06FF]")

RATE = 24_000
TARGET_LUFS = -16.0
LIMIT_DB = -3.0             # peak ceiling of the limiter; leaves room for the MP3 encoder's overshoot (up to ~2 dB)
LOWPASS_HZ = 9_500
LEVEL_ROUNDS = 3            # normalize and limit, repeated: limiting lowers loudness a little, the next round restores it
FADE_S = 0.008
LEAD_S, TAIL_S = 0.30, 0.50  # silence before the first and after the last line
GAP_TURN_S = 0.45            # between speakers
GAP_SAME_S = 0.30            # when the same speaker continues
TRIM_DB, TRIM_PAD_S = -50.0, 0.04
MP3_KBPS = 48

DEEPGRAM_MODEL = "aura-2-{voice}-en"
GEMINI_MODEL = "gemini-3.8-flash-tts"

# Agent and customer voice per call. Agents match the script names: Maria, Priya, Hana and Sara are female, as is
# the unnamed call_06 agent; James, Kevin, Omar, Daniel and the call_09 agent are male. The customer is the other
# gender, and no voice is used twice within a call.
VOICES = {
    "call_01": {"engine": "deepgram", "AGENT": ("thalia", "female"), "CUSTOMER": ("arcas", "male")},
    "call_02": {"engine": "deepgram", "AGENT": ("apollo", "male"), "CUSTOMER": ("helena", "female")},
    "call_03": {"engine": "deepgram", "AGENT": ("orion", "male"), "CUSTOMER": ("luna", "female")},
    "call_04": {"engine": "deepgram", "AGENT": ("andromeda", "female"), "CUSTOMER": ("zeus", "male")},
    "call_05": {"engine": "gemini", "AGENT": ("Charon", "male"), "CUSTOMER": ("Kore", "female")},
    "call_06": {"engine": "gemini", "AGENT": ("Aoede", "female"), "CUSTOMER": ("Puck", "male")},
    "call_07": {"engine": "deepgram", "AGENT": ("athena", "female"), "CUSTOMER": ("aries", "male")},
    "call_08": {"engine": "deepgram", "AGENT": ("asteria", "female"), "CUSTOMER": ("hermes", "male")},
    "call_09": {"engine": "deepgram", "AGENT": ("atlas", "male"), "CUSTOMER": ("cora", "female")},
    "call_10": {"engine": "deepgram", "AGENT": ("orpheus", "male"), "CUSTOMER": ("iris", "female")},
}

# A TTS response is accepted only if it holds real speech at a plausible rate for the text; otherwise it is
# requested again. Very short lines ("Okay.") sometimes come back near-silent.
MIN_PEAK = 0.05
CHARS_PER_S = (4.0, 30.0)
TTS_ATTEMPTS = 5
SHORT_LINE_ATTEMPTS = 15  # one-word lines fail most often, and each attempt costs a fraction of a cent

# List prices used for the cost report (pay as you go).
DEEPGRAM_USD_PER_1K_CHARS = 0.030
GEMINI_USD_PER_M_INPUT, GEMINI_USD_PER_M_AUDIO = 0.50, 9.00


def script_lines(path: Path) -> list[tuple[str, str]]:
    return [(m[1], m[2].strip()) for m in map(LINE.match, path.read_text(encoding="utf-8").splitlines()) if m]


def decode(data: bytes, rate: int) -> np.ndarray:
    """WAV or raw 16-bit little-endian PCM to float mono at RATE."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data)) as w:
            rate, channels, frames = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
            pcm = np.frombuffer(frames, "<i2").reshape(-1, channels).mean(axis=1)
    else:
        pcm = np.frombuffer(data, "<i2").astype(np.float64)
    audio = pcm / 32768.0
    if rate != RATE:
        g = gcd(rate, RATE)
        audio = resample_poly(audio, RATE // g, rate // g)
    return audio


class QuotaExhausted(Exception):
    pass


class TTS:
    def __init__(self):
        self.http = httpx.Client(timeout=120)
        self.usage = {"deepgram_chars": 0, "gemini_input_tokens": 0, "gemini_audio_tokens": 0, "cached": 0}
        self.spent = {"deepgram_chars": 0, "gemini_input_tokens": 0, "gemini_audio_tokens": 0}  # this run, not cached
        self.retries = 0

    def say(self, engine: str, voice: str, text: str, offline: bool = False) -> np.ndarray:
        key = tts_key(engine, voice, text)
        cached = CACHE / f"{key}.json"
        if cached.exists():
            entry = json.loads(cached.read_text())
            audio = decode(base64.b64decode(entry["audio"]), entry["rate"])
            if not problem(audio, text):
                self.usage["cached"] += 1
                for k, v in entry["usage"].items():
                    self.usage[k] += v
                return audio
        if (take := TAKES / f"{key}.opus").exists():
            audio = read_take(take)
            if not problem(audio, text):
                self.usage["cached"] += 1
                for k, v in take_index().get(key, {}).get("usage", {}).items():
                    self.usage[k] += v
                return audio
        if offline:
            raise LookupError(f"no cached take for {text[:40]!r}")
        attempts = SHORT_LINE_ATTEMPTS if len(text) < 12 else TTS_ATTEMPTS
        for _ in range(attempts):
            entry = self._deepgram(voice, text) if engine == "deepgram" else self._gemini(voice, text)
            for k, v in entry["usage"].items():  # every attempt is billed
                self.spent[k] += v
                self.usage[k] += v
            audio = decode(base64.b64decode(entry["audio"]), entry["rate"])
            if not (reason := problem(audio, text)):
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps(entry))
                if engine == "gemini":
                    keep_take(key, audio, {"engine": engine, "voice": voice, "text": text, "usage": entry["usage"]})
                return audio
            self.retries += 1
            print(f"  retry {voice} {text[:40]!r}: {reason}")
        raise SystemExit(f"{engine} {voice}: no usable audio for {text!r} after {attempts} attempts")

    def _deepgram(self, voice: str, text: str) -> dict:
        r = self.http.post("https://api.deepgram.com/v1/speak",
                           params={"model": DEEPGRAM_MODEL.format(voice=voice), "encoding": "linear16", "sample_rate": RATE,
                                   "container": "none"},
                           headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}, json={"text": text})
        if r.status_code != 200:
            raise SystemExit(f"Deepgram TTS failed: HTTP {r.status_code} {r.text[:200]}")
        return {"audio": base64.b64encode(r.content).decode(), "rate": RATE, "usage": {"deepgram_chars": len(text)}}

    def _gemini(self, voice: str, text: str) -> dict:
        body = {"contents": [{"parts": [{"text": text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        for attempt in range(8):  # per-minute rate limits answer 429 with a short retry delay
            r = self.http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                               headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, json=body)
            if r.status_code not in (429, 500, 502, 503):
                break
            delay = re.search(r'"retryDelay":\s*"(\d+)', r.text)
            if r.status_code == 429 and ("PerDay" in r.text or (delay and int(delay[1]) > 300)):
                raise QuotaExhausted(f"{GEMINI_MODEL} daily request quota reached")
            time.sleep(int(delay[1]) + 1 if delay else 5 * (attempt + 1))
        if r.status_code != 200:
            raise SystemExit(f"Gemini TTS failed: HTTP {r.status_code} {r.text[:200]}")
        data = r.json()
        inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        rate = int((re.search(r"rate=(\d+)", inline.get("mimeType", "")) or [0, RATE])[1])
        usage = data.get("usageMetadata", {})
        return {"audio": inline["data"], "rate": rate,
                "usage": {"gemini_input_tokens": usage.get("promptTokenCount", 0),
                          "gemini_audio_tokens": usage.get("candidatesTokenCount", 0)}}

    @staticmethod
    def cost(u: dict) -> float:
        return (u["deepgram_chars"] / 1000 * DEEPGRAM_USD_PER_1K_CHARS + u["gemini_input_tokens"] / 1e6 * GEMINI_USD_PER_M_INPUT
                + u["gemini_audio_tokens"] / 1e6 * GEMINI_USD_PER_M_AUDIO)


def take_index() -> dict:
    path = TAKES / "takes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def keep_take(key: str, audio: np.ndarray, meta: dict) -> None:
    """Opus at 64 kbps: about 40 KB a line, and transparent for speech that ends up as a 48 kbps MP3."""
    TAKES.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:",
                    "-c:a", "libopus", "-b:a", "64k", "-map_metadata", "-1", "-fflags", "+bitexact", str(TAKES / f"{key}.opus")],
                   input=audio.astype("<f4").tobytes(), check=True)
    index = take_index()
    index[key] = meta
    (TAKES / "takes.json").write_text(json.dumps(dict(sorted(index.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_take(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(RATE),
                          "pipe:"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<f4").astype(np.float64)


def problem(audio: np.ndarray, text: str) -> str:
    """Why a TTS response is unusable, or "" if it is fine."""
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak < MIN_PEAK:
        return f"near-silent (peak {peak:.3f})"
    seconds = len(trim(audio)) / RATE
    rate = len(text) / max(seconds, 1e-3)
    if not CHARS_PER_S[0] <= rate <= CHARS_PER_S[1]:
        return f"{seconds:.2f} s for {len(text)} characters"
    return ""


def trim(audio: np.ndarray) -> np.ndarray:
    """Cut leading and trailing silence, keeping a short margin so word onsets stay intact."""
    frame = int(0.01 * RATE)
    n = len(audio) // frame
    rms = np.sqrt(np.mean(audio[:n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12)
    loud = np.flatnonzero(20 * np.log10(rms) > TRIM_DB)
    if not len(loud):
        return audio
    pad = int(TRIM_PAD_S * RATE)
    return audio[max(0, loud[0] * frame - pad):min(len(audio), (loud[-1] + 1) * frame + pad)]


def loudness(audio: np.ndarray) -> float:
    """Integrated loudness (BS.1770). A short line ("Okay.") is measured repeated to about 2 s: the meter needs
    several 400 ms blocks, and repeating a line does not change its loudness."""
    reps = max(1, int(np.ceil(2 * RATE / len(audio))))
    value = float(pyloudnorm.Meter(RATE).integrated_loudness(np.tile(audio, reps)))
    if not np.isfinite(value):
        raise ValueError("line has no measurable loudness")
    return value


def level(audio: np.ndarray) -> np.ndarray:
    """Every line to TARGET_LUFS, so speakers and engines sound equally loud."""
    return audio * 10 ** ((TARGET_LUFS - loudness(audio)) / 20)


def lowpass(audio: np.ndarray) -> np.ndarray:
    return sosfiltfilt(butter(8, LOWPASS_HZ, fs=RATE, output="sos"), audio)


def ffmpeg_filter(audio: np.ndarray, chain: str) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:",
                          "-af", chain, "-f", "f32le", "pipe:"], input=audio.astype("<f4").tobytes(), capture_output=True,
                         check=True).stdout
    result = np.frombuffer(out, "<f4").astype(np.float64)[:len(audio)]
    return np.concatenate([result, np.zeros(len(audio) - len(result))])


def deess(audio: np.ndarray) -> np.ndarray:
    return ffmpeg_filter(audio, "deesser=i=0.6:m=0.5:f=0.5:s=o")


def master(line: np.ndarray) -> tuple[np.ndarray, float]:
    """A line at TARGET_LUFS with peaks under LIMIT_DB; returns it and the share of samples the last limiter pass changed."""
    pad = np.zeros(int(0.05 * RATE))  # room for the limiter's look-ahead
    changed = 0.0
    for _ in range(LEVEL_ROUNDS):
        limited, changed = limit(np.concatenate([pad, level(line), pad]))
        line = limited[len(pad):len(pad) + len(line)]
    return line, changed


def limit(audio: np.ndarray) -> tuple[np.ndarray, float]:
    """ffmpeg's look-ahead limiter (5 ms attack, 50 ms release, no make-up gain); returns the share of samples it changed."""
    limited = ffmpeg_filter(audio, f"alimiter=limit={10 ** (LIMIT_DB / 20):.4f}:attack=5:release=50:level=false:latency=true")
    return limited, float(np.mean(np.abs(limited - audio) > 1e-4))


def fade(audio: np.ndarray) -> np.ndarray:
    n = min(int(FADE_S * RATE), len(audio) // 2)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))  # raised cosine, 0 to 1
    audio = audio.copy()
    audio[:n] *= ramp
    audio[-n:] *= ramp[::-1]
    return audio


def tts_key(engine: str, voice: str, text: str) -> str:
    return hashlib.sha256(f"{engine}|{voice}|{GEMINI_MODEL if engine == 'gemini' else ''}|{text}".encode()).hexdigest()[:24]


def has_take(engine: str, voice: str, text: str) -> bool:
    key = tts_key(engine, voice, text)
    return (CACHE / f"{key}.json").exists() or (TAKES / f"{key}.opus").exists()


def make(path: Path, tts: TTS) -> dict:
    call_id = path.stem[:7]
    plan = VOICES[call_id]
    engine = plan["engine"]
    lead = np.zeros(int(LEAD_S * RATE))
    parts, timings, pos, prev, changed = [lead], [], len(lead), None, []
    for i, (role, text) in enumerate(script_lines(path)):
        voice, gender = plan[role]
        if engine == "deepgram" and ARABIC.search(text):
            raise SystemExit(f"{call_id} line {i} has Arabic text; give this call to Gemini in VOICES")
        line, share = master(deess(lowpass(trim(tts.say(engine, voice, text)))))
        changed.append(share)
        line = fade(line)
        if prev is not None:
            gap = np.zeros(int((GAP_TURN_S if role != prev else GAP_SAME_S) * RATE))
            parts.append(gap)
            pos += len(gap)
        timings.append({"index": i, "role": role, "voice": voice, "gender": gender, "start": round(pos / RATE, 3),
                        "end": round((pos + len(line)) / RATE, 3), "text": text})
        parts.append(line)
        pos += len(line)
        prev = role
    parts.append(np.zeros(int(TAIL_S * RATE)))
    pcm = np.round(np.clip(np.concatenate(parts), -1.0, 1.0) * 32767).astype("<i2")
    limited = float(np.mean(changed))

    wav = OUT / f"{call_id}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), "-ac", "1", "-ar", str(RATE),
                    "-codec:a", "libmp3lame", "-b:a", f"{MP3_KBPS}k", "-map_metadata", "-1", "-id3v2_version", "0",
                    "-write_id3v1", "0", str(OUT / f"{call_id}.mp3")], check=True)
    voices = {role: {"engine": engine, "voice": plan[role][0], "gender": plan[role][1]} for role in ("AGENT", "CUSTOMER")}
    (OUT / f"{call_id}.timings.json").write_text(
        json.dumps({"call_id": call_id, "rate": RATE, "voices": voices, "lines": timings}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return {"call_id": call_id, "seconds": round(len(pcm) / RATE, 1), "lines": len(timings), "limited": limited,
            "voices": f"{voices['AGENT']['voice']} / {voices['CUSTOMER']['voice']} ({engine})"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("calls", nargs="*", help="call ids such as call_05 (default: all)")
    args = ap.parse_args(argv)
    paths = sorted(p for p in SCRIPTS.glob("call_*.md") if not args.calls or p.stem[:7] in args.calls)
    if not paths:
        print("no matching scripts", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    tts = TTS()
    left = {}
    for path in paths:
        plan, lines = VOICES[path.stem[:7]], script_lines(path)
        try:  # voice every line first, so a call is only built once all its lines exist
            for role, text in lines:
                tts.say(plan["engine"], plan[role][0], text)
        except QuotaExhausted as e:
            done = sum(1 for role, text in lines if has_take(plan["engine"], plan[role][0], text))
            left[path.stem[:7]] = len(lines) - done
            print(f"{path.stem[:7]}: {e}; {done} of {len(lines)} lines voiced and kept, run again after the reset")
            continue
        info = make(path, tts)
        print(f"{info['call_id']}: {info['seconds']:6.1f} s, {info['lines']} lines, {info['voices']}"
              + f", limiter touched {100 * info['limited']:.2f}% of samples")
    u, s = tts.usage, tts.spent
    print(f"TTS for these calls: Deepgram {u['deepgram_chars']} characters, Gemini {u['gemini_input_tokens']} input and "
          f"{u['gemini_audio_tokens']} audio tokens, ${tts.cost(u):.4f} at list price; "
          f"this run (not cached): ${tts.cost(s):.4f}, {tts.retries} rejected response(s) requested again")
    if left:
        print(f"not finished: {', '.join(f'{c} ({n} lines left)' for c, n in left.items())}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
