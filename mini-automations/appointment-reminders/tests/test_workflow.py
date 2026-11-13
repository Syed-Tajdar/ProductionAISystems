"""Runs the workflow on the local test bench (python3 _n8n/harness.py up).

    python3 -m unittest discover -s appointment-reminders/tests -v
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "_n8n"))
import harness  # noqa: E402

PATH = "appointment-reminders"
AUTH = {"X-Webhook-Token": "test-webhook-token"}
REQUEST = json.loads((HERE.parent / "samples" / "dry_run_request.json").read_text(encoding="utf-8"))
DUBAI = timezone(timedelta(hours=4))


def appt_id(n: int) -> str:
    return f"00000000-0000-4000-8000-00000000000{n}"


def dry(now: str | None = None) -> dict:
    payload = {**REQUEST, "now": now or REQUEST["now"]}
    return harness.post(PATH, payload, {"dry_run": 1}, headers=AUTH)[1]


class DryRun(unittest.TestCase):
    def test_picks_due_reminders(self):
        res = dry()
        due = {r["appointment_id"]: r["type"] for r in res["reminders"]}
        self.assertEqual(due, {appt_id(2): "24h", appt_id(3): "2h", appt_id(7): "24h", appt_id(8): "24h"})

    def test_skips_with_reasons(self):
        reasons = {s["appointment_id"]: s["reason"] for s in dry()["skipped"]}
        self.assertEqual(reasons[appt_id(1)], "more than 24 hours away")
        self.assertEqual(reasons[appt_id(4)], "24h reminder already sent")
        self.assertEqual(reasons[appt_id(5)], "2h reminder already sent")
        self.assertEqual(reasons[appt_id(6)], "status cancelled")

    def test_arabic_template_and_preview(self):
        r = next(r for r in dry()["reminders"] if r["appointment_id"] == appt_id(3))
        self.assertEqual(r["language"], "ar")
        self.assertTrue(r["preview_text"].startswith("مرحباً فاطمة"))
        self.assertIn("الساعة 10:30", r["preview_text"])
        template = r["whatsapp_body"]["template"]
        self.assertEqual((template["name"], template["language"]["code"]), ("appointment_reminder", "ar"))
        self.assertEqual(len(template["components"][0]["parameters"]), 5)
        self.assertEqual(r["whatsapp_body"]["to"], "971500002003")

    def test_quiet_hours_defer_day_before_but_not_2h(self):
        res = dry("2026-11-10T23:00:00+04:00")
        self.assertTrue(res["quiet_hours"])
        self.assertEqual([(r["appointment_id"], r["type"]) for r in res["reminders"]], [(appt_id(8), "2h")])
        reasons = {s["appointment_id"]: s["reason"] for s in res["skipped"]}
        self.assertEqual(reasons[appt_id(2)], "quiet hours, will send later")

    def test_requires_token(self):
        with self.assertRaises(Exception) as ctx:
            harness.post(PATH, REQUEST, {"dry_run": 1})
        self.assertEqual(getattr(ctx.exception, "code", None), 403)

    def test_dry_run_sends_nothing(self):
        harness.mock_reset()
        dry()
        self.assertEqual(harness.mock_requests(), [])


def row(n: int, hours_from_now: float, **extra) -> dict:
    starts = datetime.now(DUBAI) + timedelta(hours=hours_from_now)
    base = {"id": appt_id(n), "patient_name": f"Patient {n}", "phone": f"+97150000300{n}", "language": "en",
            "starts_at": starts.isoformat(), "doctor": "Dr. Lina Haddad", "location": "Al Noor Family Clinic",
            "status": "booked", "reminder_24h_sent_at": None, "reminder_2h_sent_at": None}
    return {**base, **extra}


class LiveWithMockApis(unittest.TestCase):
    ROWS = [row(1, 20), row(2, 1), row(3, 5, reminder_24h_sent_at="2026-01-01T00:00:00+04:00")]

    def setUp(self):
        harness.mock_reset()
        harness.mock_set("/supabase/rest/v1/appointments", self.ROWS, method="GET")

    def test_sends_and_marks_reminders(self):
        _, res = harness.post(PATH, {}, headers=AUTH)
        expected = {appt_id(2)} | (set() if res["quiet_hours"] else {appt_id(1)})
        self.assertEqual({s["appointment_id"] for s in res["sent"]}, expected)
        self.assertEqual(res["counts"]["failed"], 0)

        fetch = harness.mock_requests("/supabase/rest/v1/appointments")[0]
        self.assertEqual(fetch["method"], "GET")
        self.assertIn("status=eq.booked", fetch["path"])
        self.assertIn("starts_at.gt.", fetch["path"])
        self.assertEqual(fetch["headers"]["apikey"], "test-key")

        sends = harness.mock_requests("/graph/v23.0/YOUR_PHONE_NUMBER_ID/messages")
        self.assertEqual(len(sends), len(expected))
        self.assertEqual(sends[0]["body"]["type"], "template")

        patches = [r for r in harness.mock_requests("/supabase/") if r["method"] == "PATCH"]
        self.assertIn(f"id=in.({appt_id(2)})", "".join(p["path"] for p in patches).replace("%28", "(").replace("%29", ")"))
        self.assertTrue(any("reminder_2h_sent_at" in p["body"] for p in patches))

    def test_failed_send_is_not_marked(self):
        harness.mock_set("/graph/", {"error": {"message": "Template not approved"}}, status=400)
        _, res = harness.post(PATH, {}, headers=AUTH)
        self.assertEqual(res["counts"]["sent"], 0)
        self.assertGreaterEqual(res["counts"]["failed"], 1)
        self.assertEqual([r for r in harness.mock_requests("/supabase/") if r["method"] == "PATCH"], [])


if __name__ == "__main__":
    unittest.main()
