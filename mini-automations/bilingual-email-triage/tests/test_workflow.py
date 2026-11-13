"""Runs the workflow on the local test bench (python3 _n8n/harness.py up).

    python3 -m unittest discover -s bilingual-email-triage/tests -v
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "_n8n"))
import harness  # noqa: E402

SAMPLES = HERE.parent / "samples"
PATH = "email-triage"
AUTH = {"X-Webhook-Token": "test-webhook-token"}


def sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def dry(name: str) -> dict:
    return harness.post(PATH, sample(name), {"dry_run": 1}, headers=AUTH)[1]


class DryRun(unittest.TestCase):
    def test_english_sales_inquiry(self):
        res = dry("en_sales_inquiry.json")
        self.assertEqual((res["category"], res["priority"], res["language"]), ("sales", "normal", "en"))
        self.assertTrue(res["draft_reply"].startswith("Dear Daniel,"))
        self.assertFalse(res["alert"])

    def test_arabic_complaint_is_urgent_with_arabic_draft(self):
        res = dry("ar_complaint_urgent.json")
        self.assertEqual((res["category"], res["priority"], res["language"]), ("complaint", "urgent", "ar"))
        self.assertTrue(res["draft_reply"].startswith("عزيزي مريم"))
        self.assertTrue(res["alert"])
        self.assertIn("Urgent complaint email (AR)", res["alert_text"])

    def test_billing_and_arabic_support(self):
        self.assertEqual(dry("en_billing.json")["category"], "billing")
        res = dry("ar_support.json")
        self.assertEqual((res["category"], res["language"]), ("support", "ar"))

    def test_newsletter_gets_no_draft(self):
        res = dry("newsletter.json")
        self.assertEqual((res["category"], res["priority"]), ("newsletter", "low"))
        self.assertIsNone(res["draft_reply"])

    def test_webhook_cannot_pose_as_gmail(self):
        payload = {**sample("en_billing.json"), "threadId": "t1", "id": "m1"}
        res = harness.post(PATH, payload, {"dry_run": 1}, headers=AUTH)[1]
        self.assertEqual(res["source"], "webhook")
        self.assertIsNone(res["gmail_id"])

    def test_webhook_requires_token(self):
        with self.assertRaises(Exception) as ctx:
            harness.post(PATH, sample("en_billing.json"), {"dry_run": 1})
        self.assertEqual(getattr(ctx.exception, "code", None), 403)

    def test_dry_run_sends_nothing(self):
        harness.mock_reset()
        dry("ar_complaint_urgent.json")
        self.assertEqual(harness.mock_requests(), [])


class LiveWithMockApis(unittest.TestCase):
    def setUp(self):
        harness.mock_reset()

    def test_claude_triage_and_urgent_alert(self):
        harness.mock_set("/anthropic/", harness.claude_reply({
            "category": "billing", "priority": "urgent", "language": "en",
            "summary_en": "Customer was charged twice for invoice PG-2210 and asks for a refund.",
            "draft_reply": "Dear Accounts Team, thank you for flagging this. "
                           "Our finance team is checking the duplicate charge and will reply within one business day."}))
        _, res = harness.post(PATH, sample("en_billing.json"), headers=AUTH)
        self.assertEqual(res["triage_source"], "claude")
        self.assertEqual(res["priority"], "urgent")
        self.assertIn("finance team", res["draft_reply"])

        claude = harness.mock_requests("/anthropic/v1/messages")[0]
        body = claude["body"]
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertNotIn("fallbacks", body)  # Haiku 5.5 has no server-side fallback
        self.assertEqual(claude["headers"]["anthropic-version"], "2026-10-21")
        self.assertNotIn("anthropic-beta", claude["headers"])
        self.assertEqual(body["output_config"]["format"]["schema"]["required"],
                         ["category", "priority", "language", "summary_en", "draft_reply"])
        self.assertIn("Charged twice", body["messages"][0]["content"])

        tg = harness.mock_requests("/telegram/")
        self.assertEqual(len(tg), 1)
        self.assertIn("charged twice for invoice PG-2210", json.dumps(tg[0]["body"]))

    def test_invalid_claude_output_falls_back_to_rules(self):
        harness.mock_set("/anthropic/", {"type": "message", "stop_reason": "end_turn", "content": [{"type": "text", "text": "not json"}]})
        _, res = harness.post(PATH, sample("ar_complaint_urgent.json"), headers=AUTH)
        self.assertEqual(res["triage_source"], "rules")
        self.assertIn("Claude failed", res["triage_note"])
        self.assertEqual(res["category"], "complaint")
        self.assertEqual(len(harness.mock_requests("/telegram/")), 1)

    def test_normal_email_sends_no_alert(self):
        harness.mock_set("/anthropic/", harness.claude_reply({
            "category": "sales", "priority": "normal", "language": "en",
            "summary_en": "Lease quotation request.", "draft_reply": "Dear Daniel, thank you."}))
        harness.post(PATH, sample("en_sales_inquiry.json"), headers=AUTH)
        self.assertEqual(harness.mock_requests("/telegram/"), [])


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
    """The same workflow with llm_provider = gemini (test bench copy on /email-triage-gemini)."""

    def setUp(self):
        harness.mock_reset()

    def test_gemini_triage_and_alert(self):
        harness.mock_set("/gemini/", harness.gemini_reply({
            "category": "complaint", "priority": "urgent", "language": "ar",
            "summary_en": "Tenant reports the AC has not worked for three days and asks for a technician today.",
            "draft_reply": "عزيزتي مريم، نعتذر عن الإزعاج وسيتواصل معك فريق الصيانة خلال يوم عمل واحد."}))
        _, res = harness.post(PATH + "-gemini", sample("ar_complaint_urgent.json"), headers=AUTH)
        self.assertEqual((res["triage_source"], res["category"], res["priority"]), ("gemini", "complaint", "urgent"))
        self.assertTrue(res["draft_reply"].startswith("عزيزتي مريم"))
        text = assert_gemini_request(self, harness.mock_requests("/gemini/")[0], "gemini-3.5-flash-lite",
                                     ["properties", "priority"], ["urgent", "normal", "low"])
        self.assertIn("التكييف لا يعمل", text)
        self.assertEqual(len(harness.mock_requests("/telegram/")), 1)

    def test_rate_limit_backs_off_then_uses_rules(self):
        harness.mock_set("/gemini/", harness.GEMINI_429, status=429)
        _, res = harness.post(PATH + "-gemini", sample("en_billing.json"), headers=AUTH)
        self.assertEqual(len(harness.mock_requests("/gemini/")), 3)
        self.assertEqual((res["triage_source"], res["category"]), ("rules", "billing"))
        self.assertIn("rate limited (429)", res["triage_note"])

    def test_safety_block_uses_rules(self):
        harness.mock_set("/gemini/", {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]})
        _, res = harness.post(PATH + "-gemini", sample("en_sales_inquiry.json"), headers=AUTH)
        self.assertEqual(res["triage_source"], "rules")
        self.assertIn("Gemini failed, used rules: model declined the request", res["triage_note"])


if __name__ == "__main__":
    unittest.main()
