"""Workflow A in demo mode (LEADS_DEMO_MODE=true), loaded by the bench on /webhook/lead-intake-demo.

    python3 tests/bench/harness.py up
    python3 -m unittest discover -s tests -p 'test_demo.py' -v
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "bench")]
from test_lead_intake import alerts, claude, form, logged_events, saved_leads  # noqa: E402

import harness  # noqa: E402
import mock_api  # noqa: E402

M = mock_api.MOCK
PATH = "lead-intake-demo"
# The address comes from CF-Connecting-IP only; the forwarded list is what a caller could have written.
VISITOR = {"cf-connecting-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.9, 10.0.0.1"}


def post(**fields):
    return harness.post(PATH, form(**fields), headers=VISITOR)


def setUpModule():
    global SERVER
    SERVER = mock_api.start()


def tearDownModule():
    SERVER.shutdown()


class Demo(unittest.TestCase):
    def setUp(self):
        M.reset()

    def test_hot_lead_shows_category_reason_alert_and_the_email_instead_of_sending_it(self):
        M.set("/anthropic/", mock_api.claude_reply({**mock_api.HOT_RENT, "reason": "Wants to move next month and gives a budget."}))
        status, body = post(email="sara@example.com")
        self.assertEqual(status, 200)
        demo = body["demo"]
        self.assertEqual((demo["category"], demo["intent"], demo["duplicate"], demo["alert"]), ("hot", "rent", False, "sent"))
        self.assertEqual(demo["reason"], "Wants to move next month and gives a budget.")
        email = demo["email"]
        self.assertEqual((email["to"], email["language"], email["dir"]), ("sara@example.com", "en", "ltr"))
        self.assertEqual(email["subject"], "Thanks for contacting Palm Grove Realty")
        self.assertTrue(email["text"].startswith("Hi Sara,"))
        self.assertEqual(M.mail, [])  # nothing went out
        self.assertNotIn("auto_replied", [e["event"] for e in logged_events()])
        self.assertTrue(alerts()[0]["text"].startswith("<b>[DEMO]</b>"))
        self.assertEqual(demo["alert_message"], alerts()[0]["text"])  # the message as sales received it
        self.assertIs(saved_leads()[0]["meta"]["demo"], True)

    def test_limits_are_checked_per_visitor_before_the_llm(self):
        post()
        take = M.calls("/supabase/rest/v1/rpc/lead_demo_take")[0]["body"]
        self.assertEqual(take, {"p_client": "203.0.113.7", "p_per_client": 5, "p_window_seconds": 600, "p_daily_cap": 100})
        self.assertEqual(M.calls("/supabase/rest/v1/rpc/lead_demo_take_alert")[0]["body"], {"p_daily_cap": 20})

    def test_headers_a_caller_can_set_do_not_change_the_visitor(self):
        spoofed = {"x-forwarded-for": "198.51.100.1", "true-client-ip": "198.51.100.2", "x-real-ip": "198.51.100.3"}
        harness.post(PATH, form(), headers=spoofed)
        harness.post(PATH, form(phone="+971500000188"), headers={**spoofed, "cf-connecting-ip": "203.0.113.50"})
        takes = [c["body"]["p_client"] for c in M.calls("/supabase/rest/v1/rpc/lead_demo_take") if "p_client" in c["body"]]
        self.assertEqual(takes, ["unknown", "203.0.113.50"])  # no trusted header: one shared allowance

    def test_over_the_limit_answers_429_without_calling_the_llm(self):
        M.set("/supabase/rest/v1/rpc/lead_demo_take", {"allowed": False, "reason": "client", "retry_after": 420})
        status, body = post()
        self.assertEqual((status, body), (429, {"ok": False, "error": "limit", "reason": "client", "retry_after": 420}))
        self.assertEqual((M.calls("/anthropic/"), saved_leads(), alerts()), ([], [], []))

    def test_limits_unavailable_answers_503(self):
        M.set("/supabase/rest/v1/rpc/lead_demo_take", {"message": "connection refused"}, status=500)
        status, body = post()
        self.assertEqual(status, 503)
        self.assertIn("not available", body["error"])
        self.assertEqual(M.calls("/anthropic/"), [])

    def test_duplicate_gets_no_second_email(self):
        post(email="sara@example.com")
        status, body = post(email="sara@example.com", message="Any 3 bed options too?")
        demo = body["demo"]
        self.assertEqual((status, demo["duplicate"], demo["email"], demo["no_email"]), (200, True, None, "returning"))

    def test_used_up_alert_cap_skips_telegram(self):
        M.set("/supabase/rest/v1/rpc/lead_demo_take_alert", {"allowed": False})
        _, body = post()
        self.assertEqual(body["demo"]["alert"], "capped")
        self.assertIsNone(body["demo"]["alert_message"])  # nothing was sent, so nothing to show
        self.assertEqual(alerts(), [])

    def test_warm_lead_needs_no_alert(self):
        M.set("/anthropic/", claude(urgency="warm"))
        _, body = post(email="omar@example.com")
        self.assertEqual((body["demo"]["category"], body["demo"]["alert"]), ("warm", "not_needed"))
        self.assertEqual((alerts(), M.calls("/supabase/rest/v1/rpc/lead_demo_take_alert")), ([], []))
        self.assertEqual(body["demo"]["reason"], "")  # an answer without a reason still counts

    def test_llm_failure_is_unclassified_and_alerted(self):
        M.set("/anthropic/", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, status=529)
        _, body = post()
        demo = body["demo"]
        self.assertEqual((demo["category"], demo["alert"]), ("unclassified", "sent"))
        self.assertIn("could not classify", demo["reason"])

    def test_arabic_email_preview_is_right_to_left(self):
        M.set("/anthropic/", claude(intent="buy", urgency="warm", language="ar", summary="Wants to buy."))
        _, body = post(name="خالد", email="khalid@example.com", message="أبحث عن شقة للشراء في دبي")
        email = body["demo"]["email"]
        self.assertEqual((email["language"], email["dir"]), ("ar", "rtl"))
        self.assertIn("خالد", email["text"])

    def test_spam_and_phone_only_leads_get_no_email(self):
        M.set("/anthropic/", claude(intent="spam", urgency="cold", summary="SEO offer."))
        _, body = post(email="seo@example.com")
        self.assertEqual((body["demo"]["email"], body["demo"]["no_email"]), (None, "spam"))
        M.reset()
        _, body = post()
        self.assertEqual((body["demo"]["email"], body["demo"]["no_email"]), (None, "no_email"))

    def test_honeypot_still_answers_plain_ok(self):
        status, body = post(company="Acme SEO")
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(M.calls(), [])

    def test_normal_mode_has_no_demo_steps(self):
        status, body = harness.post("lead-intake", form(email="sara@example.com"), headers=VISITOR)
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(M.calls("/supabase/rest/v1/rpc/lead_demo"), [])
        self.assertEqual(len(M.mail), 1)
        self.assertNotIn("[DEMO]", alerts()[0]["text"])
        self.assertNotIn("demo", saved_leads()[0]["meta"])


if __name__ == "__main__":
    unittest.main()
