"""One structured (JSON schema) LLM call, behind the LLM_PROVIDER switch."""

import json
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
# USD per million input / output tokens, Claude API list prices. Prompts here stay under
# Haiku's 100K-token threshold, above which a higher rate applies.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
GEMINI_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    data: dict
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float | None


def cost(settings: Settings, model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None and settings.llm_price_input_per_mtok is not None and settings.llm_price_output_per_mtok is not None:
        price = (settings.llm_price_input_per_mtok, settings.llm_price_output_per_mtok)
    if price is None:
        return None  # unknown price: leave empty rather than guess
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 6)


def without_additional_properties(schema):
    """Gemini's responseSchema takes the same JSON schema minus additionalProperties."""
    if isinstance(schema, dict):
        return {k: without_additional_properties(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(schema, list):
        return [without_additional_properties(v) for v in schema]
    return schema


def post_with_retries(client: httpx.Client, url: str, *, max_attempts: int, sleep, **kwargs) -> httpx.Response:
    """POST with exponential backoff (1 s, 2 s, ...) on 429 and 5xx, honouring Retry-After up to 10 s."""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(url, **kwargs)
        except httpx.TransportError as e:
            if attempt == max_attempts:
                raise LLMError(f"provider unreachable: {e.__class__.__name__}") from e
            sleep(2 ** (attempt - 1))
            continue
        if response.status_code < 400:
            return response
        if response.status_code not in RETRY_STATUS or attempt == max_attempts:
            raise LLMError(f"provider returned HTTP {response.status_code}: {response.text[:200]}")
        retry_after = response.headers.get("retry-after", "")
        sleep(min(int(retry_after), 10) if retry_after.isdigit() else 2 ** (attempt - 1))
    raise LLMError("request failed")  # not reached


def complete_json(settings: Settings, system: str, user: str, schema: dict, *, http: httpx.Client | None = None,
                  anthropic_client=None, sleep=time.sleep) -> LLMResult:
    provider = settings.llm_provider
    http = http or httpx.Client(timeout=httpx.Timeout(300, connect=10))
    started = time.monotonic()
    if provider == "gemini":
        data, model, tokens_in, tokens_out = _gemini(settings, system, user, schema, http, sleep)
    elif provider == "openai":
        data, model, tokens_in, tokens_out = _openai(settings, system, user, schema, http, sleep)
    else:
        data, model, tokens_in, tokens_out = _anthropic(settings, system, user, schema, anthropic_client)
    latency = int((time.monotonic() - started) * 1000)
    result = LLMResult(data, provider, model, tokens_in, tokens_out, latency, cost(settings, model, tokens_in, tokens_out))
    log.info("llm %s %s: %d in / %d out tokens, %d ms, cost %s", provider, model, tokens_in, tokens_out, latency,
             "unknown" if result.cost_usd is None else f"${result.cost_usd}")
    return result


def _key(secret, name: str) -> str:
    key = secret.get_secret_value()
    if not key:
        raise LLMError(f"{name} is not set")
    return key


def _gemini(settings, system, user, schema, http, sleep):
    model = settings.gemini_model
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": without_additional_properties(schema)}}
    response = post_with_retries(
        http, f"{settings.gemini_base_url.rstrip('/')}/v1beta/models/{model}:generateContent", json=body,
        headers={"x-goog-api-key": _key(settings.gemini_api_key, "GEMINI_API_KEY")},
        max_attempts=settings.llm_max_attempts, sleep=sleep)
    res = response.json()
    candidate = (res.get("candidates") or [{}])[0]
    if res.get("promptFeedback", {}).get("blockReason") or candidate.get("finishReason") in GEMINI_BLOCKED:
        raise LLMError("model declined the request")
    text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
    usage = res.get("usageMetadata", {})
    out = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)  # thinking is billed as output
    return _parse(text), model, usage.get("promptTokenCount", 0), out


def _openai(settings, system, user, schema, http, sleep):
    model = settings.openai_model
    if not model:
        raise LLMError("OPENAI_MODEL is not set")
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "rubric_scores", "schema": schema, "strict": True}}}
    response = post_with_retries(
        http, f"{settings.openai_base_url.rstrip('/')}/v1/chat/completions", json=body,
        headers={"Authorization": f"Bearer {_key(settings.openai_api_key, 'OPENAI_API_KEY')}"},
        max_attempts=settings.llm_max_attempts, sleep=sleep)
    res = response.json()
    message = res["choices"][0]["message"]
    if message.get("refusal"):
        raise LLMError("model declined the request")
    usage = res.get("usage", {})
    return _parse(message.get("content") or ""), model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _anthropic(settings, system, user, schema, client):
    import anthropic

    model = settings.anthropic_model
    client = client or anthropic.Anthropic(api_key=_key(settings.anthropic_api_key, "ANTHROPIC_API_KEY"),
                                           max_retries=settings.llm_max_attempts - 1)  # the SDK backs off on 429 and 5xx
    # Haiku 5.5 has no server-side refusal fallback; send it only to models that support it.
    fallback = {} if model.startswith("claude-haiku") else {
        "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        response = client.beta.messages.create(
            model=model, max_tokens=16000, **fallback, system=system,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user}])
    except anthropic.APIStatusError as e:
        raise LLMError(f"provider returned HTTP {e.status_code}") from e
    if response.stop_reason == "refusal":
        raise LLMError("model declined the request")
    text = next((b.text for b in reversed(response.content) if b.type == "text"), "")
    return _parse(text), response.model or model, response.usage.input_tokens, response.usage.output_tokens


def _parse(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON: {text[:120]!r}") from e
