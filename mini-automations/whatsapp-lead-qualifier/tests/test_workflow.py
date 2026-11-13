"""Runs the workflow on the local test bench (python3 _n8n/harness.py up).

    python3 -m unittest discover -s whatsapp-lead-qualifier/tests -v
"""

import hashlib
import hmac
import json
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "_n8n"))
import harness  # noqa: E402

SAMPLES = HERE.parent / "samples"
PATH = "whatsapp-lead"
APP_SECRET = b"test-app-secret"  # set on the test bench n8n as WHATSAPP_APP_SECRET
DRY_TOKEN = {"X-Dry-Run-Token": "test-dry-run-token"}  # WHATSAPP_DRY_RUN_TOKEN on the test bench


def sample(name: str, phone: str | None = None) -> dict:
    data = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
    if phone:
        value = data["entry"][0]["changes"][0]["value"]
        value["contacts"][0]["wa_id"] = phone
        value["messages"][0]["from"] = phone
    return data


def signed(payload: dict, secret: bytes = APP_SECRET) -> dict:
    """The X-Hub-Signature-256 header Meta would send for this exact body."""
    digest = hmac.new(secret, json.dumps(payload).encode(), hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}"}


def live(payload: dict):
    return harness.post(PATH, payload, headers=signed(payload))


def dry(payload: dict, headers: dict | None = DRY_TOKEN):
    return harness.post(PATH, payload, {"dry_run": 1}, headers=headers)


def new_phone() -> str:
    # Each test run uses fresh numbers so stored lead profiles never collide.
    return "9715" + "".join(random.choices("0123456789", k=8))


class DryRun(unittest.TestCase):
    def test_verify_token(self):
        ok_status, body = harness.get(PATH, {"hub.mode": "subscribe", "hub.verify_token": "change-me-to-a-random-string", "hub.challenge": "42"})
        bad_status, _ = harness.get(PATH, {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "42"})
        self.assertEqual((ok_status, body), (200, "42"))
        self.assertEqual(bad_status, 403)

    def test_status_update_is_ignored(self):
        _, res = dry(sample("status_update.json"))
        self.assertIn("status update", res["ignored"])

    def test_english_conversation_becomes_hot(self):
        phone = new_phone()
        _, first = dry(sample("en_first_message.json", phone))
        self.assertEqual(first["profile"]["intent"], "rent")
        self.assertEqual(first["profile"]["area"], "Dubai Marina")
        self.assertEqual(first["profile"]["bedrooms"], 2)
        self.assertEqual(first["score"], "warm")
        self.assertTrue(first["reply_text"].startswith("Hi Sara, thanks for contacting"))
        self.assertIn("yearly budget in AED", first["reply_text"])
        self.assertFalse(first["alert"])

        # Dry runs keep nothing, so the second message brings the profile from the first answer.
        _, second = dry({**sample("en_follow_up.json", phone), "profile": first["profile"]})
        self.assertEqual(second["profile"]["budget_aed"], 120000)
        self.assertEqual(second["profile"]["timeline"], "1_3_months")
        self.assertEqual(second["score"], "hot")
        self.assertTrue(second["alert"])
        self.assertIn("within 15 minutes", second["reply_text"])
        self.assertIn(f"https://wa.me/{phone}", second["alert_text"])

    def test_arabic_message_gets_arabic_reply(self):
        _, res = dry(sample("ar_message.json", new_phone()))
        self.assertEqual(res["profile"]["language"], "ar")
        self.assertEqual(res["profile"]["intent"], "buy")
        self.assertEqual(res["profile"]["area"], "Business Bay")
        self.assertEqual(res["profile"]["budget_aed"], 1500000)
        self.assertEqual(res["score"], "warm")
        self.assertIn("متى تخطط للشراء؟", res["reply_text"])

    def test_spam_gets_no_reply(self):
        _, res = dry(sample("spam_message.json", new_phone()))
        self.assertEqual(res["score"], "spam")
        self.assertIsNone(res["reply_text"])
        self.assertFalse(res["alert"])

    def test_dry_run_needs_the_token(self):
        harness.mock_reset()
        for headers in (None, {"X-Dry-Run-Token": "wrong"}, {"X-Dry-Run-Token": ""}):
            with self.assertRaises(Exception) as ctx:
                dry(sample("en_first_message.json", new_phone()), headers)
            self.assertEqual(getattr(ctx.exception, "code", None), 401, headers)
        self.assertEqual(harness.mock_requests(), [])

    def test_dry_run_stores_nothing(self):
        phone = new_phone()
        _, first = dry(sample("en_first_message.json", phone))
        self.assertEqual(first["profile"]["messages"], 1)
        _, again = dry(sample("en_follow_up.json", phone))  # no profile sent back: starts from nothing
        self.assertEqual(again["profile"]["messages"], 1)
        self.assertNotIn("area", again["profile"])
        self.assertTrue(again["reply_text"].startswith("Hi Sara, thanks for contacting"))
        harness.mock_reset()
        _, real = live(sample("en_follow_up.json", phone))  # a real message for the same number finds no profile either
        self.assertEqual(real["profile"]["messages"], 1)
        self.assertNotIn("area", real["profile"])


class Signature(unittest.TestCase):
    def setUp(self):
        harness.mock_reset()

    def assert_rejected(self, headers: dict | None):
        payload = sample("en_first_message.json", new_phone())
        with self.assertRaises(Exception) as ctx:
            harness.post(PATH, payload, headers=headers)
        self.assertEqual(getattr(ctx.exception, "code", None), 401)
        self.assertEqual(harness.mock_requests(), [])  # nothing reached Claude, WhatsApp or Telegram

    def test_valid_signature_is_processed(self):
        status, res = live(sample("status_update.json"))
        self.assertEqual(status, 200)
        self.assertIn("status update", res["ignored"])

    def test_wrong_secret_is_rejected(self):
        payload = sample("en_first_message.json")
        self.assert_rejected(signed(payload, b"wrong-secret"))

    def test_tampered_body_is_rejected(self):
        # assert_rejected posts a body with a new phone number; this signature covers the original body.
        self.assert_rejected(signed(sample("en_first_message.json")))

    def test_missing_signature_is_rejected(self):
        self.assert_rejected(None)


class LiveWithMockApis(unittest.TestCase):
    def setUp(self):
        harness.mock_reset()

    def test_claude_extraction_reply_and_alert(self):
        harness.mock_set("/anthropic/", harness.claude_reply({
            "intent": "buy", "area": "Palm Jumeirah", "bedrooms": 3, "budget_aed": 5000000,
            "timeline": "this_month", "language": "en"}))
        phone = new_phone()
        _, res = live(sample("en_first_message.json", phone))
        self.assertEqual(res["extraction_source"], "claude")
        self.assertEqual(res["score"], "hot")

        claude = harness.mock_requests("/anthropic/v1/messages")[0]
        self.assertEqual(claude["headers"]["x-api-key"], "test-key")
        self.assertEqual(claude["headers"]["anthropic-version"], "2026-10-21")
        self.assertEqual(claude["body"]["model"], "claude-haiku-5-5")
        # Haiku 5.5 has no server-side fallback, so neither the parameter nor its beta header is sent.
        self.assertNotIn("fallbacks", claude["body"])
        self.assertNotIn("anthropic-beta", claude["headers"])
        self.assertEqual(claude["body"]["output_config"]["format"]["type"], "json_schema")

        wa = harness.mock_requests("/graph/v23.0/YOUR_PHONE_NUMBER_ID/messages")[0]
        self.assertEqual(wa["headers"]["authorization"], "Bearer test-token")
        self.assertEqual(wa["body"]["to"], phone)
        self.assertIn("within 15 minutes", wa["body"]["text"]["body"])

        tg = harness.mock_requests("/telegram/")
        self.assertEqual(len(tg), 1)
        self.assertTrue(tg[0]["path"].endswith("/sendMessage"))
        self.assertIn("Hot lead: Sara", json.dumps(tg[0]["body"]))

    def test_claude_failure_falls_back_to_rules(self):
        harness.mock_set("/anthropic/", {"type": "error", "error": {"type": "overloaded_error"}}, status=529)
        _, res = live(sample("en_first_message.json", new_phone()))
        self.assertEqual(res["extraction_source"], "rules")
        self.assertIn("Claude failed", res["extraction_note"])
        self.assertEqual(res["profile"]["area"], "Dubai Marina")
        self.assertEqual(len(harness.mock_requests("/anthropic/")), 3)  # retried
        self.assertEqual(len(harness.mock_requests("/graph/")), 1)  # still replied


def has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


def assert_gemini_request(test, req: dict, model: str, schema_enum_path: list[str], expected_enum: list[str]):
    test.assertEqual(req["method"], "POST")
    test.assertTrue(req["path"].endswith(f"/v1beta/models/{model}:generateContent"), req["path"])
    test.assertEqual(req["headers"]["x-goog-api-key"], "test-gemini-key")
    config = req["body"]["generationConfig"]
    test.assertEqual(config["responseMimeType"], "application/json")
    schema = config["responseSchema"]
    test.assertFalse(has_key(schema, "additionalProperties"))  # not accepted by Gemini's responseSchema
    node = schema
    for key in schema_enum_path:
        node = node[key]
    test.assertEqual(node["enum"], expected_enum)
    test.assertTrue(req["body"]["systemInstruction"]["parts"][0]["text"])
    return req["body"]["contents"][0]["parts"][0]["text"]


class LiveGemini(unittest.TestCase):
    """The same workflow with llm_provider = gemini (test bench copy on /whatsapp-lead-gemini)."""

    def setUp(self):
        harness.mock_reset()

    def post(self, payload):
        return harness.post(PATH + "-gemini", payload, headers=signed(payload))

    def test_gemini_extraction_reply_and_alert(self):
        harness.mock_set("/gemini/", harness.gemini_reply({
            "intent": "rent", "area": "JVC", "bedrooms": 1, "budget_aed": 75000, "timeline": "this_month", "language": "en"}))
        phone = new_phone()
        _, res = self.post(sample("en_first_message.json", phone))
        self.assertEqual((res["extraction_source"], res["score"]), ("gemini", "hot"))
        req = harness.mock_requests("/gemini/")[0]
        text = assert_gemini_request(self, req, "gemini-3.5-flash-lite", ["properties", "intent"],
                                     ["buy", "rent", "info", "spam", "unknown"])
        self.assertIn("Dubai Marina", text)
        self.assertEqual(harness.mock_requests("/anthropic/"), [])
        self.assertEqual(harness.mock_requests("/graph/")[0]["body"]["to"], phone)
        self.assertEqual(len(harness.mock_requests("/telegram/")), 1)

    def test_rate_limit_backs_off_then_uses_rules(self):
        harness.mock_set("/gemini/", harness.GEMINI_429, status=429)
        _, res = self.post(sample("en_first_message.json", new_phone()))
        self.assertEqual(len(harness.mock_requests("/gemini/")), 3)  # first try + 2 retries, 2 s apart
        self.assertEqual(res["extraction_source"], "rules")
        self.assertIn("Gemini failed, used rules: rate limited (429)", res["extraction_note"])
        self.assertEqual(res["profile"]["area"], "Dubai Marina")
        self.assertEqual(len(harness.mock_requests("/graph/")), 1)  # the lead still gets a reply


if __name__ == "__main__":
    unittest.main()
