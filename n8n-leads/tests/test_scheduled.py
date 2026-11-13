"""Workflow B (follow-up, daily summary) and Workflow C (errors) on the test bench.

    python3 tests/bench/harness.py up
    python3 -m unittest discover -s tests -p 'test_scheduled.py' -v
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "bench"))
import harness  # noqa: E402
import mock_api  # noqa: E402

M = mock_api.MOCK
AUTH = {"X-Scheduler-Token": "test-scheduler-token"}  # what the scheduler (GitHub Actions or Cloud Scheduler) sends
OPS_CHAT = "-1001000000002"
SALES_CHAT = "-1001000000001"


def due_lead(n: int, language: str = "en", name: str = "Sara Ahmed") -> dict:
    """A row as claim_follow_ups returns it."""
    return {"id": f"00000000-0000-4000-8000-00000000000{n}", "name": name, "email": f"lead{n}@example.com",
            "phone": None, "language": language, "status": "followed_up", "intent": "rent"}


def ops_alerts(timeout: float = 15) -> list[dict]:
    """Workflow C runs after the failed run has answered, so wait for it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [r["body"] for r in M.calls("/telegram/") if r["body"].get("chat_id") == OPS_CHAT]
        if found:
            return found
        time.sleep(0.5)
    return []


def setUpModule():
    global SERVER
    SERVER = mock_api.start()


def tearDownModule():
    SERVER.shutdown()


class FollowUp(unittest.TestCase):
    PATH = "lead-follow-up"

    def setUp(self):
        M.reset()

    def test_requires_the_scheduler_token(self):
        for headers in ({}, {"X-Scheduler-Token": "wrong"}):
            status, _ = harness.post(self.PATH, {}, form=False, headers=headers)
            self.assertEqual(status, 403)
        self.assertEqual(M.calls(), [])

    def test_nothing_due(self):
        status, body = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertEqual((status, body), (200, {"ok": True, "claimed": 0, "sent": 0, "failed": 0}))
        self.assertEqual(M.calls("/supabase/rest/v1/rpc/claim_follow_ups")[0]["body"], {"max_leads": 20})
        self.assertEqual((M.mail, M.calls("/supabase/rest/v1/rpc/finish_follow_ups")), ([], []))

    def test_sends_follow_ups_in_each_language_and_records_them(self):
        M.set("/supabase/rest/v1/rpc/claim_follow_ups", [due_lead(1), due_lead(2, "ar", "مريم")])
        status, body = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertEqual((status, body), (200, {"ok": True, "claimed": 2, "sent": 2, "failed": 0}))
        en, ar = sorted(M.mail, key=lambda m: m["to"][0])
        self.assertEqual(en["to"], ["lead1@example.com"])
        self.assertEqual(en["subject"], "Following up on your enquiry with Palm Grove Realty")
        self.assertTrue(en["text"].startswith("Hi Sara,"))
        self.assertEqual(en["reply_to"], "advisors@palmgrove.example")
        self.assertEqual(ar["subject"], "متابعة لطلبك لدى Palm Grove Realty")
        self.assertTrue(ar["text"].startswith("مرحباً مريم،"))
        self.assertIn('dir="rtl"', ar["html"])
        results = M.calls("/supabase/rest/v1/rpc/finish_follow_ups")[0]["body"]["results"]
        self.assertEqual([(r["lead_id"], r["ok"]) for r in results], [(due_lead(1)["id"], True), (due_lead(2)["id"], True)])

    def test_email_failure_is_reported_per_lead(self):
        M.set("/supabase/rest/v1/rpc/claim_follow_ups", [due_lead(1), due_lead(2)])
        M.smtp_reject["lead2@example.com"] = "550 5.1.1 mailbox unavailable"
        status, body = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertEqual((status, body), (200, {"ok": True, "claimed": 2, "sent": 1, "failed": 1}))
        results = M.calls("/supabase/rest/v1/rpc/finish_follow_ups")[0]["body"]["results"]
        self.assertEqual([r["ok"] for r in results], [True, False])
        self.assertIn("550", results[1]["error"])  # finish_follow_ups puts this lead back to new

    def test_batch_size_can_be_lowered(self):
        harness.post(self.PATH, {"max_leads": 5}, form=False, headers=AUTH)
        self.assertEqual(M.calls("/supabase/rest/v1/rpc/claim_follow_ups")[0]["body"], {"max_leads": 5})

    def test_supabase_failure_fails_the_run_and_alerts_ops(self):
        M.set("/supabase/rest/v1/rpc/claim_follow_ups", {"message": "relation does not exist"}, status=500)
        status, _ = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertGreaterEqual(status, 500)  # the scheduler records the failed attempt
        self.assertEqual(M.mail, [])
        text = ops_alerts()[0]["text"]
        self.assertIn("Workflow failed: Lead follow-up (Workflow B)", text)
        self.assertIn("Node: Claim due leads", text)
        self.assertIn("Error:", text)



class FollowUpWithoutSender(unittest.TestCase):
    """The public demo leaves LEADS_EMAIL_FROM empty on purpose: the scheduled call must finish cleanly."""
    PATH = "lead-follow-up-nosender"

    def setUp(self):
        M.reset()

    def test_claims_and_sends_nothing_and_answers_skipped(self):
        M.set("/supabase/rest/v1/rpc/claim_follow_ups", [due_lead(1)])  # would be claimed if it were called
        status, body = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertEqual((status, body), (200, {"ok": True, "claimed": 0, "sent": 0, "failed": 0, "skipped": "no_sender"}))
        # claim_follow_ups is what moves a lead from new to followed_up: never called, so no status changed.
        self.assertEqual(M.calls("/supabase/"), [])
        self.assertEqual(M.mail, [])
        self.assertEqual(ops_alerts(timeout=8), [])  # no failed run, so Workflow C posts nothing

    def test_still_requires_the_scheduler_token(self):
        status, _ = harness.post(self.PATH, {}, form=False, headers={"X-Scheduler-Token": "wrong"})
        self.assertEqual(status, 403)

class DailySummary(unittest.TestCase):
    PATH = "lead-daily-summary"

    def setUp(self):
        M.reset()

    def test_requires_the_scheduler_token(self):
        status, _ = harness.post(self.PATH, {}, form=False)
        self.assertEqual(status, 403)
        self.assertEqual(M.calls(), [])

    def test_posts_the_numbers_to_sales(self):
        status, body = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, **mock_api.SUMMARY})
        self.assertEqual(M.calls("/supabase/")[0]["path"], "/supabase/rest/v1/lead_daily_summary?select=*")
        post = M.calls("/telegram/")[0]["body"]
        self.assertEqual((post["chat_id"], post["parse_mode"]), (SALES_CHAT, "HTML"))
        for line in ("New leads: 7", "Hot leads: 2", "Conversions (won): 1", "Follow-ups sent: 4", "Waiting for follow-up: 3"):
            self.assertIn(line, post["text"])
        self.assertTrue(post["text"].startswith("<b>Daily lead summary, "))

    def test_telegram_failure_fails_the_run_and_tells_ops(self):
        M.set("/telegram/", {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, status=400, times=2)
        status, _ = harness.post(self.PATH, {}, form=False, headers=AUTH)
        self.assertGreaterEqual(status, 500)
        text = ops_alerts()[0]["text"]  # the ops message itself gets through (override used up)
        self.assertIn("Workflow failed: Lead daily summary (Workflow B)", text)
        self.assertIn("Node: Post summary", text)


class ErrorWorkflow(unittest.TestCase):
    def setUp(self):
        M.reset()

    def test_error_message_is_escaped(self):
        M.set("/supabase/rest/v1/lead_daily_summary", {"message": "<script>boom</script>"}, status=500)
        harness.post("lead-daily-summary", {}, form=False, headers=AUTH)
        text = ops_alerts()[0]["text"]
        self.assertNotIn("<script>", text)
        self.assertIn("Node: Read summary", text)


if __name__ == "__main__":
    unittest.main()
