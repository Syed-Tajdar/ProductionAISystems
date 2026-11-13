"""Gemini generateContent with JSON output, for the Python automations in this folder
(cv-screener, invoice-to-sheet, price-monitor). Standard library only.

Each script adds this folder to sys.path and imports from here, so the request, the retry rule and the
refusal check exist once. RateLimited is also what the scripts raise when Claude keeps answering 429.
"""

import json
import os
import time
import urllib.error
import urllib.request

BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


class RateLimited(RuntimeError):
    """The provider still answered 429 after a short backoff."""


def gemini_schema(schema):
    """Gemini's responseSchema takes the same JSON schema minus additionalProperties."""
    if isinstance(schema, dict):
        return {k: gemini_schema(v) for k, v in schema.items() if k != "additionalProperties"}
    return [gemini_schema(v) for v in schema] if isinstance(schema, list) else schema


def gemini_key() -> str:
    """Any non-empty value counts as a key."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return key


def gemini_json(model: str, system: str, parts: list, schema: dict) -> dict:
    """One generateContent call with JSON output. 429 and 5xx are retried twice with a short backoff."""
    base = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com")
    body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}],
                       "generationConfig": {"responseMimeType": "application/json", "responseSchema": gemini_schema(schema)}})
    req = urllib.request.Request(f"{base}/v1beta/models/{model}:generateContent", data=body.encode(),
                                 headers={"content-type": "application/json", "x-goog-api-key": gemini_key()})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 2:
                wait = e.headers.get("Retry-After", "")
                time.sleep(min(float(wait) if wait.isdigit() else 2 ** attempt, 5))
                continue
            if e.code == 429:
                raise RateLimited("Gemini rate limit (429) after retries") from e
            raise
    cand = (res.get("candidates") or [{}])[0]
    if res.get("promptFeedback", {}).get("blockReason") or cand.get("finishReason") in BLOCKED:
        raise RuntimeError("model declined the request")
    return json.loads("".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought")))
