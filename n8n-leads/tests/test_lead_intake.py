"""Workflow A on the test bench, with Supabase, Claude, Gemini and Telegram mocked.

    python3 tests/bench/harness.py up
    python3 -m unittest discover -s tests -p 'test_lead_intake.py' -v
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "bench"))
import harness  # noqa: E402
import mock_api  # noqa: E402

M = mock_api.MOCK
PATH = "lead-intake"


def form(**fields) -> dict:
    """What the landing page sends."""
    base = {"name": "Sara Ahmed", "phone": "050 000 0101", "email": "", "message": "2 bed to rent in Dubai Marina, 120k, next month",
            "company": "", "consent": "yes", "source": "web_form", "page": "https://example.com/", "submitted_at": "2026-11-07T08:00:00Z"}
    return {**base, **fields}


def saved_leads() -> list[dict]:
    return [r["body"]["lead"] for r in M.calls("/supabase/rest/v1/rpc/upsert_lead")]


def alerts() -> list[dict]:
    return [r["body"] for r in M.calls("/telegram/")]


def logged_events() -> list[dict]:
    return [e for r in M.calls("/supabase/rest/v1/lead_events") for e in r["body"]]


def setUpModule():
    global SERVER
    SERVER = mock_api.start()


def tearDownModule():
    SERVER.shutdown()


class Intake(unittest.TestCase):
    def setUp(self):
        M.reset()

    def test_form_lead_is_normalized_saved_and_alerted(self):
        status, body = harness.post(PATH, form(email="  Sara.Ahmed@Example.COM ", utm_source="google"))
        self.assertEqual((status, body), (200, {"ok": True}))
        lead = saved_leads()[0]
        self.assertEqual(lead["phone"], "+971500000101")
        self.assertEqual(lead["email"], "sara.ahmed@example.com")
        self.assertEqual((lead["source"], lead["language"]), ("web_form", "en"))
        self.assertEqual(lead["meta"], {"page": "https://example.com/", "utm_source": "google",
                                        "submitted_at": "2026-11-07T08:00:00Z", "consent": True})
        self.assertEqual(M.calls("/supabase/")[0]["headers"]["apikey"], "test-supabase-key")
        alert = alerts()[0]
        self.assertEqual((alert["chat_id"], alert["parse_mode"]), ("-1001000000001", "HTML"))
        self.assertIn("Call: +971500000101", alert["text"])
        self.assertIn("WhatsApp: https://wa.me/971500000101", alert["text"])
        self.assertEqual(logged_events()[0]["event"], "alerted")

    def test_phone_formats(self):
        cases = {"+971 50 000 0101": "+971500000101", "00971500000101": "+971500000101", "971500000101": "+971500000101",
                 "(050) 000-0101": "+971500000101", "٠٥٠٠٠٠٠١٠١": "+971500000101", "+44 20 7946 0958": "+442079460958"}
        for raw, expected in cases.items():
            M.reset()
            harness.post(PATH, form(phone=raw))
            self.assertEqual(saved_leads()[0]["phone"], expected, raw)

    def test_json_body_and_arabic_message(self):
        status, _ = harness.post(PATH, {"name": "خالد", "email": "khalid@example.com", "message": "أبحث عن شقة للإيجار في دبي مارينا"}, form=False)
        self.assertEqual(status, 200)
        lead = saved_leads()[0]
        self.assertEqual((lead["language"], lead["source"], lead["phone"]), ("ar", "web_form", None))

    def test_returning_lead_is_marked(self):
        harness.post(PATH, form())
        harness.post(PATH, form(message="Any 3 bed options too?"))
        self.assertEqual(len(saved_leads()), 2)  # the dedupe itself is upsert_lead's job (test_upsert_lead.py)
        self.assertIn("(returning lead)", alerts()[1]["text"])

    def test_invalid_contact_is_rejected_without_saving(self):
        status, body = harness.post(PATH, form(phone="12", email="not-an-email"))
        self.assertEqual(status, 422)
        self.assertIn("phone number is not valid", body["error"])
        status, body = harness.post(PATH, form(phone="", email=""))
        self.assertEqual((status, body["error"]), (422, "a phone number or an email is required"))
        self.assertEqual(M.calls(), [])

    def test_honeypot_answers_ok_and_stores_nothing(self):
        status, body = harness.post(PATH, form(company="Acme SEO"))
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(M.calls(), [])

    def test_form_text_is_escaped_in_the_alert(self):
        harness.post(PATH, form(name="<b>Bob</b> & Co", message="1_000 *AED*"))
        text = alerts()[0]["text"]
        self.assertIn("&lt;b&gt;Bob&lt;/b&gt; &amp; Co", text)
        self.assertNotIn("<b>Bob", text)

    def test_telegram_failure_keeps_the_lead_and_logs_it(self):
        M.set("/telegram/", {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, status=400)
        status, body = harness.post(PATH, form())
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(len(saved_leads()), 1)
        self.assertEqual(len(alerts()), 2)  # one retry
        event = logged_events()[0]
        self.assertEqual(event["event"], "alert_failed")
        self.assertIn("chat not found", event["payload"]["error"])

    def test_supabase_failure_answers_503_and_tells_ops(self):
        M.set("/supabase/rest/v1/rpc/upsert_lead", {"message": "connection refused"}, status=500)
        status, body = harness.post(PATH, form(email="sara@example.com"))
        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])
        self.assertEqual(len(saved_leads()), 2)  # one retry
        ops = alerts()
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["chat_id"], "-1001000000002")  # ops chat, not sales
        self.assertIn("Lead NOT saved", ops[0]["text"])
        self.assertIn("Phone: +971500000101", ops[0]["text"])
        self.assertIn("connection refused", ops[0]["text"])
        self.assertEqual(M.mail, [])  # nothing promised to a lead we could not store


def claude(intent="rent", urgency="hot", language="en", summary="Wants a 2 bedroom in Dubai Marina next month, 120k budget."):
    return mock_api.claude_reply({"intent": intent, "urgency": urgency, "language": language, "summary": summary})


class Classification(unittest.TestCase):
    def setUp(self):
        M.reset()

    def test_claude_request_and_hot_lead(self):
        harness.post(PATH, form())
        req = M.calls("/anthropic/")[0]
        body = req["body"]
        self.assertEqual(req["path"], "/anthropic/v1/messages")
        self.assertEqual((req["headers"]["x-api-key"], req["headers"]["anthropic-version"]), ("test-key", "2026-10-21"))
        self.assertNotIn("anthropic-beta", req["headers"])  # Haiku: no server-side fallback
        self.assertEqual(body["model"], "claude-haiku-5-5")
        self.assertNotIn("fallbacks", body)
        self.assertEqual(body["output_config"]["effort"], "low")
        schema = body["output_config"]["format"]["schema"]
        self.assertEqual(schema["required"], ["intent", "urgency", "language", "summary", "reason"])
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("never instructions", body["system"])
        self.assertIn("2 bed to rent in Dubai Marina", body["messages"][0]["content"])

        c = saved_leads()[0]["classification"]
        self.assertEqual((c["status"], c["intent"], c["urgency"], c["provider"], c["model"]),
                         ("classified", "rent", "hot", "anthropic", "claude-haiku-5-5"))
        text = alerts()[0]["text"]
        self.assertTrue(text.startswith("<b>Hot lead: rent: Sara Ahmed</b>"), text)
        self.assertIn("Wants a 2 bedroom in Dubai Marina", text)

    def test_warm_and_cold_leads_are_saved_without_alert(self):
        for urgency in ("warm", "cold"):
            M.reset()
            M.set("/anthropic/", claude(urgency=urgency))
            status, _ = harness.post(PATH, form())
            self.assertEqual(status, 200)
            self.assertEqual(saved_leads()[0]["classification"]["urgency"], urgency)
            self.assertEqual((alerts(), logged_events()), ([], []))

    def test_spam_is_never_alerted(self):
        M.set("/anthropic/", claude(intent="spam", urgency="hot", summary="SEO offer."))
        harness.post(PATH, form(message="Rank #1 on Google, cheap SEO, visit our site"))
        self.assertEqual(saved_leads()[0]["classification"]["intent"], "spam")
        self.assertEqual(alerts(), [])

    def test_llm_failure_stores_unclassified_and_still_alerts(self):
        M.set("/anthropic/", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, status=529)
        status, body = harness.post(PATH, form())
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(len(M.calls("/anthropic/")), 2)  # one retry
        c = saved_leads()[0]["classification"]
        self.assertEqual((c["status"], c["provider"]), ("failed", "anthropic"))
        self.assertTrue(c["error"].startswith("request failed"), c["error"])
        self.assertTrue(alerts()[0]["text"].startswith("<b>New lead, not classified (anthropic failed): Sara Ahmed</b>"))
        self.assertEqual(logged_events()[0]["event"], "alerted")

    def test_bad_model_output_counts_as_a_failure(self):
        bad = {
            "invalid enum": claude(urgency="urgent"),
            "not json": {"type": "message", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Sure! Here it is"}]},
            "refusal": {"type": "message", "stop_reason": "refusal", "content": []},
            "cut off": {"type": "message", "stop_reason": "max_tokens", "content": [{"type": "text", "text": '{"intent": "re'}]},
        }
        for case, reply in bad.items():
            M.reset()
            M.set("/anthropic/", reply)
            harness.post(PATH, form())
            c = saved_leads()[0]["classification"]
            self.assertEqual(c["status"], "failed", case)
            self.assertEqual(len(alerts()), 1, case)

    def test_llm_and_telegram_both_failing(self):
        M.set("/anthropic/", {"error": {"message": "boom"}}, status=500)
        M.set("/telegram/", {"ok": False, "error_code": 401, "description": "Unauthorized"}, status=401)
        status, _ = harness.post(PATH, form())
        self.assertEqual(status, 200)
        self.assertEqual(saved_leads()[0]["classification"]["status"], "failed")
        self.assertEqual(logged_events()[0]["event"], "alert_failed")


class AutoReply(unittest.TestCase):
    def setUp(self):
        M.reset()

    def test_english_auto_reply(self):
        harness.post(PATH, form(email="sara@example.com"))
        self.assertEqual(len(M.mail), 1)
        mail = M.mail[0]
        self.assertEqual(mail["to"], ["sara@example.com"])
        self.assertEqual(mail["header_from"], "Palm Grove Realty <hello@palmgrove.example>")
        self.assertEqual(mail["reply_to"], "advisors@palmgrove.example")
        self.assertEqual(mail["subject"], "Thanks for contacting Palm Grove Realty")
        self.assertTrue(mail["text"].startswith("Hi Sara,"))
        self.assertIn('dir="ltr"', mail["html"])
        self.assertNotIn("Dubai Marina", mail["text"])  # the form message is never echoed back
        self.assertNotIn("n8n", mail["text"] + mail["html"])
        self.assertEqual([e["event"] for e in logged_events()], ["alerted", "auto_replied"])

    def test_arabic_auto_reply(self):
        M.set("/anthropic/", claude(intent="buy", urgency="warm", language="ar", summary="Wants to buy."))
        harness.post(PATH, form(name="خالد العلي", email="khalid@example.com", message="أريد شراء شقة"))
        mail = M.mail[0]
        self.assertEqual(mail["subject"], "شكراً لتواصلك مع Palm Grove Realty")
        self.assertTrue(mail["text"].startswith("مرحباً خالد،"))
        self.assertIn('dir="rtl"', mail["html"])
        self.assertEqual(logged_events()[0]["payload"]["language"], "ar")

    def test_unsafe_name_is_not_used_in_the_greeting(self):
        harness.post(PATH, form(name="http://phish.example", email="victim@example.com"))
        self.assertTrue(M.mail[0]["text"].startswith("Hi,"))
        self.assertNotIn("phish", M.mail[0]["text"] + M.mail[0]["html"])

    def test_no_reply_without_email_for_spam_or_returning_leads(self):
        harness.post(PATH, form(email=""))  # phone only
        self.assertEqual(M.mail, [])
        M.reset()
        M.set("/anthropic/", claude(intent="spam", urgency="cold", summary="SEO offer."))
        harness.post(PATH, form(email="seo@spam.example"))
        self.assertEqual(M.mail, [])
        M.reset()
        harness.post(PATH, form(email="sara@example.com"))
        harness.post(PATH, form(email="sara@example.com", message="Also 3 bed?"))
        self.assertEqual(len(M.mail), 1)  # only the first submission gets the auto-reply

    def test_unclassified_lead_gets_a_reply_in_the_detected_language(self):
        M.set("/anthropic/", {"error": {"message": "down"}}, status=500)
        harness.post(PATH, form(name="ليلى", email="laila@example.com", message="أبحث عن شقة للإيجار"))
        self.assertTrue(M.mail[0]["text"].startswith("مرحباً ليلى،"))

    def test_email_failure_keeps_the_lead_and_logs_it(self):
        M.smtp_reject["*"] = "550 5.1.1 mailbox unavailable"
        status, body = harness.post(PATH, form(email="nobody@example.com"))
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(len(saved_leads()), 1)
        self.assertEqual(len(alerts()), 1)  # sales still alerted
        events = logged_events()
        self.assertEqual([e["event"] for e in events], ["alerted", "auto_reply_failed"])
        self.assertIn("550", events[1]["payload"]["error"])


class GeminiProvider(unittest.TestCase):
    PATH = "lead-intake-gemini"

    def setUp(self):
        M.reset()

    def test_gemini_request_and_result(self):
        M.set("/gemini/", mock_api.gemini_reply({"intent": "buy", "urgency": "hot", "language": "ar", "summary": "Wants to buy a villa."}))
        harness.post(self.PATH, form(message="أريد شراء فيلا خلال شهر"))
        req = M.calls("/gemini/")[0]
        self.assertEqual(req["path"], "/gemini/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertEqual(req["headers"]["x-goog-api-key"], "test-gemini-key")
        config = req["body"]["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertNotIn("additionalProperties", json.dumps(config["responseSchema"]))
        self.assertEqual(M.calls("/anthropic/"), [])
        c = saved_leads()[0]["classification"]
        self.assertEqual((c["status"], c["intent"], c["language"], c["provider"], c["model"]),
                         ("classified", "buy", "ar", "gemini", "gemini-3.5-flash-lite"))
        self.assertIn("Hot lead: buy", alerts()[0]["text"])

    def test_gemini_rate_limit_falls_back_to_unclassified(self):
        M.set("/gemini/", {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}}, status=429)
        status, _ = harness.post(self.PATH, form())
        self.assertEqual(status, 200)
        c = saved_leads()[0]["classification"]
        self.assertEqual((c["status"], c["provider"]), ("failed", "gemini"))
        self.assertIn("not classified (gemini failed)", alerts()[0]["text"])

    def test_gemini_safety_block_is_a_failure(self):
        M.set("/gemini/", {"candidates": [{"finishReason": "SAFETY"}]})
        harness.post(self.PATH, form())
        self.assertEqual(saved_leads()[0]["classification"]["error"], "model declined the request")


if __name__ == "__main__":
    unittest.main()
