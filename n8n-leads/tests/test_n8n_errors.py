"""What the n8n-errors script (.github/scripts/n8n_errors.py) prints: failures stay readable, lead data does not.

    python3 -m unittest discover -s tests -p 'test_n8n_errors.py' -v

The executions are made up; no n8n is needed.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github" / "scripts"))
import n8n_errors  # noqa: E402

NAME, PHONE, EMAIL, MESSAGE = "Sara Ahmed", "+971 50 000 0142", "sara@example.com", "2 bedroom to rent in Dubai Marina next month"
LEAD = {"name": NAME, "phone": PHONE, "email": EMAIL, "message": MESSAGE, "source": "web_form"}
ALERT = "\n".join([f"<b>Hot lead: rent: {NAME}</b>", "Wants a 2 bedroom in Dubai Marina.", f"Call: {PHONE}",
                   "WhatsApp: https://wa.me/971500000142", f"Email: {EMAIL}", f"Message: {MESSAGE}", "Source: web_form"])


def execution(run_data, error=None):
    return {"id": "41", "status": "error", "startedAt": "2026-11-10T08:00:00Z", "workflowId": "wf1",
            "data": {"resultData": {"runData": run_data, **({"error": error} if error else {})}}}


def output(json):
    return [{"data": {"main": [[{"json": json}]]}}]


class Masking(unittest.TestCase):
    def assertNoLeadData(self, text):
        for value in (NAME, "Sara", PHONE, "971500000142", "0142", EMAIL, "sara@", MESSAGE, "Dubai Marina"):
            self.assertNotIn(value, text)

    def test_personal_fields_in_a_node_output_are_replaced(self):
        lines = n8n_errors.report([execution({"Save lead": output({"error": {"message": "permission denied for table leads"},
                                                                  "lead": LEAD, "alert_text": ALERT, "email_to": EMAIL})})])
        text = "\n".join(lines)
        self.assertNoLeadData(text)
        self.assertIn("permission denied for table leads", text)
        self.assertIn("Save lead:", text)
        self.assertIn("<masked,", text)

    def test_an_error_message_keeps_its_wording_but_not_the_lead(self):
        error = {"name": "NodeApiError", "message": f"Bad Request: chat not found for {NAME} <{EMAIL}> {PHONE}",
                 "description": f"Name: {NAME}\nPhone: {PHONE}\nEmail: {EMAIL}\nMessage: {MESSAGE}",
                 "context": {"request": {"body": {"chat_id": "-100200300", "text": ALERT}}}}
        text = "\n".join(n8n_errors.report([execution({"Alert sales": [{"error": error}]})]))
        self.assertIn("Alert sales: ERROR", text)
        self.assertIn("NodeApiError", text)
        self.assertIn("chat not found", text)
        for value in (PHONE, "971500000142", EMAIL, MESSAGE, "Dubai Marina"):
            self.assertNotIn(value, text)
        self.assertNotIn(f"Name: {NAME}", text)
        self.assertIn("Name: <name>", text)
        self.assertIn("Message: <message>", text)

    def test_the_workflow_error_is_masked_too(self):
        text = "\n".join(n8n_errors.report([execution({}, {"message": f"Could not email {EMAIL}", "stack": f"at lead {PHONE}"})]))
        self.assertIn("workflow error:", text)
        self.assertIn("Could not email <email>", text)
        self.assertNotIn(PHONE, text)

    def test_alert_and_greeting_lines(self):
        masked = n8n_errors.mask_text(ALERT + f"\nHi Sara,\nمرحباً سارة،\n<b>New lead, not classified (anthropic failed) (returning lead): {NAME}</b>")
        for value in (NAME, "Sara", "سارة", PHONE, "971500000142", EMAIL, MESSAGE):
            self.assertNotIn(value, masked)
        self.assertIn("Hot lead: rent: <name>", masked)
        self.assertIn("Hi <name>,", masked)
        self.assertIn("Source: web_form", masked)

    def test_phone_formats(self):
        for phone in ("+971 50 000 0142", "+971-50-000-0142", "00971500000142", "971500000142", "050 000 0142", "+44 20 7946 0958"):
            self.assertEqual(n8n_errors.mask_text(f"call {phone} now"), "call <phone> now", phone)

    def test_numbers_that_are_not_phones_stay(self):
        text = "HTTP 404 after 3 tries in 1200 ms on 2026-11-10T08:00:00Z, node v22.11.0, 1.5 s"
        self.assertEqual(n8n_errors.mask_text(text), text)

    def test_outputs_without_an_error_are_not_printed(self):
        lines = n8n_errors.report([execution({"Normalize lead": output({"lead": LEAD, "ok": True})})])
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("== execution 41 error"))

    def test_secrets_are_scrubbed_after_masking(self):
        lines = n8n_errors.report([execution({}, {"message": "401 for key sk-fake-123"})], lambda t: t.replace("sk-fake-123", "***"))
        self.assertIn("401 for key ***", lines[1])


class Answer:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class WaitsForAStartingN8n(unittest.TestCase):
    """While n8n starts, /api/v1 answers 404 (measured: about 80 s after /healthz on 0.1 CPU)."""

    def run_fetch(self, answers, wait_seconds=60):
        now = [0.0]
        calls, lines = [], []

        def opener(request, timeout):
            calls.append(request)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return Answer(answer)

        def sleep(seconds):
            now[0] += seconds

        body = n8n_errors.fetch("req", wait_seconds=wait_seconds, pause=10, opener=opener, sleep=sleep,
                                clock=lambda: now[0], log=lines.append)
        return body, calls, lines

    @staticmethod
    def http(code):
        return n8n_errors.urllib.error.HTTPError("https://x/api/v1/executions", code, "x", {}, None)

    def test_404_and_gateway_errors_are_retried_until_n8n_answers(self):
        body, calls, lines = self.run_fetch([self.http(404), self.http(502), ConnectionResetError(), b'{"data": []}'])
        self.assertEqual(body, b'{"data": []}')
        self.assertEqual(len(calls), 4)
        self.assertEqual(lines[0], "n8n is starting (HTTP 404), retrying in 10 s")

    def test_a_rejected_key_is_not_retried(self):
        with self.assertRaises(n8n_errors.urllib.error.HTTPError) as caught:
            self.run_fetch([self.http(401)])
        self.assertEqual(caught.exception.code, 401)

    def test_it_gives_up_at_the_deadline(self):
        with self.assertRaises(n8n_errors.urllib.error.HTTPError):
            self.run_fetch([self.http(404)] * 10, wait_seconds=30)


if __name__ == "__main__":
    unittest.main()
