import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.config import Settings
from app.llm import LLMError, complete_json, cost

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"],
          "properties": {"items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                               "properties": {"verdict": {"type": "string", "enum": ["pass", "fail"]}}}}}}
ANSWER = {"items": [{"verdict": "pass"}]}


def settings(**kw):
    return Settings(_env_file=None, gemini_api_key="any-non-empty-key", openai_api_key="any-non-empty-key",
                    anthropic_api_key="any-non-empty-key", **kw)


def mock(handler):
    seen, sleeps = [], []

    def record(request):
        seen.append(request)
        return handler(request, len(seen))

    return httpx.Client(transport=httpx.MockTransport(record)), seen, sleeps


def gemini_ok(req, n):
    return httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": json.dumps(ANSWER)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 150, "thoughtsTokenCount": 50}})


def test_gemini_request_and_usage():
    http, seen, sleeps = mock(gemini_ok)
    r = complete_json(settings(llm_provider="gemini"), "sys", "user text", SCHEMA, http=http, sleep=sleeps.append)
    req = seen[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req.headers["x-goog-api-key"] == "any-non-empty-key"
    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"][0]["parts"][0]["text"] == "user text"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "additionalProperties" not in json.dumps(body["generationConfig"]["responseSchema"])
    assert (r.data, r.provider, r.model, r.input_tokens, r.output_tokens) == (ANSWER, "gemini", "gemini-3.8-flash", 1200, 200)
    assert r.cost_usd is None  # no built-in Gemini price, none configured


def test_gemini_backoff_on_429_and_503():
    def handler(req, n):
        if n == 1:
            return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}, headers={"retry-after": "4"})
        if n == 2:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
        return gemini_ok(req, n)

    http, seen, sleeps = mock(handler)
    complete_json(settings(llm_provider="gemini"), "s", "u", SCHEMA, http=http, sleep=sleeps.append)
    assert len(seen) == 3 and sleeps == [4, 2]


def test_gemini_gives_up_and_reports():
    http, seen, sleeps = mock(lambda req, n: httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}))
    with pytest.raises(LLMError, match="HTTP 503"):
        complete_json(settings(llm_provider="gemini"), "s", "u", SCHEMA, http=http, sleep=sleeps.append)
    assert len(seen) == 3


def test_gemini_safety_block_and_bad_json():
    http, _, _ = mock(lambda req, n: httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}))
    with pytest.raises(LLMError, match="declined"):
        complete_json(settings(llm_provider="gemini"), "s", "u", SCHEMA, http=http)
    http, _, _ = mock(lambda req, n: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}))
    with pytest.raises(LLMError, match="invalid JSON"):
        complete_json(settings(llm_provider="gemini"), "s", "u", SCHEMA, http=http)


def test_openai_request_and_configured_price():
    def handler(req, n):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(ANSWER)}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    http, seen, _ = mock(handler)
    s = settings(llm_provider="openai", openai_model="test-model", llm_price_input_per_mtok=1.0, llm_price_output_per_mtok=4.0)
    r = complete_json(s, "sys", "u", SCHEMA, http=http)
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions" and seen[0].headers["authorization"] == "Bearer any-non-empty-key"
    assert body["response_format"]["json_schema"]["strict"] is True and body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert r.cost_usd == round((1000 * 1.0 + 100 * 4.0) / 1e6, 6)
    with pytest.raises(LLMError, match="OPENAI_MODEL"):
        complete_json(settings(llm_provider="openai"), "s", "u", SCHEMA, http=http)


def test_missing_key_is_clear():
    with pytest.raises(LLMError, match="GEMINI_API_KEY is not set"):
        complete_json(Settings(_env_file=None, llm_provider="gemini"), "s", "u", SCHEMA, http=mock(gemini_ok)[0])


def test_claude_prices():
    s = Settings(_env_file=None)
    assert cost(s, "claude-sonnet-5-5", 10_000, 1_000) == 0.03
    assert cost(s, "claude-haiku-5-5", 10_000, 1_000) == 0.0015
    assert cost(s, "unknown-model", 10_000, 1_000) is None


@pytest.fixture
def fake_claude():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            raw = json.dumps({"id": "msg", "type": "message", "role": "assistant", "model": body["model"],
                              "content": [{"type": "text", "text": json.dumps(ANSWER)}], "stop_reason": "end_turn",
                              "stop_sequence": None, "usage": {"input_tokens": 2000, "output_tokens": 300}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import anthropic

    yield anthropic.Anthropic(api_key="any-non-empty-key", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0), seen
    server.shutdown()


def test_anthropic_request_with_sonnet_default(fake_claude):
    client, seen = fake_claude
    r = complete_json(settings(), "sys", "u", SCHEMA, anthropic_client=client)
    body = seen[0]["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0]["headers"]["anthropic-beta"]
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}
    assert (r.data, r.input_tokens, r.output_tokens, r.cost_usd) == (ANSWER, 2000, 300, 0.007)


def test_anthropic_haiku_gets_no_fallback(fake_claude):
    client, seen = fake_claude
    complete_json(settings(anthropic_model="claude-haiku-5-5"), "s", "u", SCHEMA, anthropic_client=client)
    assert "fallbacks" not in seen[0]["body"] and "anthropic-beta" not in seen[0]["headers"]
