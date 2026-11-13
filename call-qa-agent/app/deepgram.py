"""Deepgram pre-recorded transcription with speaker diarization."""

import logging
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_KEYTERMS = 100  # Deepgram's limit per request


class DeepgramError(RuntimeError):
    pass


@dataclass
class Transcript:
    utterances: list[dict]
    duration_sec: float
    model: str
    request_id: str
    detected_language: str | None = None
    keyterms: list[str] = field(default_factory=list)


def merge_keyterms(*groups: str | list[str] | None) -> list[str]:
    """Combine comma-separated or list keyterms, keep order, drop blanks and duplicates."""
    seen: dict[str, str] = {}
    for group in groups:
        items = group.split(",") if isinstance(group, str) else (group or [])
        for term in (t.strip() for t in items):
            if term and term.lower() not in seen:
                seen[term.lower()] = term
    return list(seen.values())[:MAX_KEYTERMS]


class DeepgramClient:
    def __init__(self, api_key: str, model: str = "nova-3", base_url: str = "https://api.deepgram.com",
                 client: httpx.Client | None = None, max_attempts: int = 3, sleep=time.sleep):
        if not api_key:
            raise DeepgramError("DEEPGRAM_API_KEY is not set")
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=httpx.Timeout(300, connect=10))
        self.max_attempts, self.sleep = max_attempts, sleep

    def params(self, language: str, keyterms: list[str]) -> list[tuple[str, str]]:
        params = [("model", self.model), ("language", language), ("diarize", "true"), ("utterances", "true"),
                  ("smart_format", "true"), ("punctuate", "true")]
        # Nova-3 takes keyterm prompting; older models take keywords.
        name = "keyterm" if self.model.startswith("nova-3") else "keywords"
        return params + [(name, term) for term in keyterms]

    def transcribe(self, *, audio: bytes | None = None, mimetype: str = "audio/wav", url: str | None = None,
                   language: str = "en", keyterms: list[str] | None = None) -> Transcript:
        if (audio is None) == (url is None):
            raise ValueError("pass either audio bytes or a url")
        terms = merge_keyterms(keyterms)
        headers = {"Authorization": f"Token {self.api_key}"}
        if url:
            kwargs = {"json": {"url": url}}
        else:
            kwargs = {"content": audio}
            headers["Content-Type"] = mimetype
        started = time.monotonic()
        response = self._post(f"{self.base_url}/v1/listen", params=self.params(language, terms), headers=headers, **kwargs)
        data = response.json()
        transcript = parse(data, terms)
        log.info("deepgram transcribed %.1fs of audio in %d ms (request %s)", transcript.duration_sec,
                 (time.monotonic() - started) * 1000, transcript.request_id)
        return transcript

    def _post(self, url: str, **kwargs) -> httpx.Response:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.client.post(url, **kwargs)
            except httpx.TransportError as e:
                if attempt == self.max_attempts:
                    raise DeepgramError(f"Deepgram unreachable: {e.__class__.__name__}") from e
            else:
                if response.status_code < 400:
                    return response
                if response.status_code not in RETRY_STATUS or attempt == self.max_attempts:
                    raise DeepgramError(f"Deepgram returned HTTP {response.status_code}: {error_message(response)}")
                retry_after = response.headers.get("retry-after", "")
                if retry_after.isdigit():
                    self.sleep(min(int(retry_after), 10))
                    continue
            self.sleep(2 ** (attempt - 1))  # 1 s, 2 s, ...
        raise DeepgramError("Deepgram request failed")  # not reached


def error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    return str(body.get("err_msg") or body.get("message") or body)[:200]


def parse(data: dict, keyterms: list[str]) -> Transcript:
    meta = data.get("metadata", {})
    model = next(iter(meta.get("model_info", {}).values()), {})
    channel = (data.get("results", {}).get("channels") or [{}])[0]
    utterances = [
        {"speaker": u.get("speaker", 0), "start": round(u["start"], 2), "end": round(u["end"], 2),
         "text": u["transcript"], "confidence": round(u.get("confidence", 0), 3)}
        for u in data.get("results", {}).get("utterances", [])
    ]
    return Transcript(
        utterances=utterances,
        duration_sec=round(float(meta.get("duration", 0)), 2),
        model=" ".join(filter(None, [model.get("name"), model.get("version")])) or "unknown",
        request_id=meta.get("request_id", ""),
        detected_language=channel.get("detected_language"),
        keyterms=keyterms,
    )
