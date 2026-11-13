"""Runs the workflow on the local test bench (python3 _n8n/harness.py up).

    python3 -m unittest discover -s daily-report-bot/tests -v
"""

import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "_n8n"))
import harness  # noqa: E402

PATH = "daily-report"
AUTH = {"X-Webhook-Token": "test-webhook-token"}
REQUEST = json.loads((HERE.parent / "samples" / "dry_run_request.json").read_text(encoding="utf-8"))


def dry(payload: dict | None = None) -> dict:
    return harness.post(PATH, payload or REQUEST, {"dry_run": 1}, headers=AUTH)[1]


def expected_kpis(rows: list[dict], day: str) -> dict:
    """Independent recomputation of the headline numbers."""
    d = date.fromisoformat(day)
    week = [r for r in rows if (d - timedelta(days=7)).isoformat() <= r["day"] < day]
    days = len({r["day"] for r in week})
    y = [r for r in rows if r["day"] == day]
    revenue = sum(r["revenue_aed"] for r in y)
    avg = sum(r["revenue_aed"] for r in week) / days
    return {"revenue": revenue, "orders": sum(r["orders"] for r in y),
            "revenue_vs_avg_pct": round((revenue - avg) / avg * 100, 1)}


class DryRun(unittest.TestCase):
    def test_kpis_match_independent_calculation(self):
        res = dry()
        exp = expected_kpis(REQUEST["rows"], REQUEST["report_date"])
        self.assertEqual({k: res["kpis"][k] for k in exp}, exp)
        self.assertEqual(res["kpis"]["avg_order_value"], round(exp["revenue"] / exp["orders"], 2))

    def test_anomalies_and_message(self):
        res = dry()
        self.assertEqual(res["anomalies"], ["walk_in", "whatsapp"])
        self.assertEqual(res["commentary_source"], "template")
        msg = res["message"]
        self.assertTrue(msg.startswith("Daily report, Monday 9 November 2026"))
        self.assertIn("whatsapp: AED 7,800, 26 orders (+73.3%)", msg)
        self.assertIn("- Unusual: walk_in -44.3%, whatsapp +73.3% vs the 7-day average.", msg)
        self.assertNotIn("claude_body", res)

    def test_missing_day_is_reported(self):
        res = dry({**REQUEST, "report_date": "2026-11-10"})
        self.assertTrue(res["missing_data"])
        self.assertIn("No sales rows for 2026-11-10", res["message"])

    def test_requires_token_and_sends_nothing(self):
        harness.mock_reset()
        with self.assertRaises(Exception) as ctx:
            harness.post(PATH, REQUEST, {"dry_run": 1})
        self.assertEqual(getattr(ctx.exception, "code", None), 403)
        dry()
        self.assertEqual(harness.mock_requests(), [])


def live_rows() -> list[dict]:
    """The sample week shifted so that the report day is yesterday in Dubai."""
    yesterday = datetime.now(timezone(timedelta(hours=4))).date() - timedelta(days=1)
    shift = yesterday - date.fromisoformat(REQUEST["report_date"])
    return [{**r, "day": (date.fromisoformat(r["day"]) + shift).isoformat()} for r in REQUEST["rows"]]


class LiveWithMockApis(unittest.TestCase):
    def setUp(self):
        harness.mock_reset()
        harness.mock_set("/supabase/rest/v1/daily_sales", live_rows(), method="GET")

    def test_claude_bullets_are_posted(self):
        harness.mock_set("/anthropic/", harness.claude_reply({"bullets": [
            "Revenue reached AED 23,760, +5.2% vs the 7-day average.",
            "WhatsApp jumped +73.3% while walk_in fell -44.3%.",
            "Check walk-in staffing and the WhatsApp campaign today."]}))
        _, res = harness.post(PATH, {}, headers=AUTH)
        self.assertEqual(res["commentary_source"], "claude")
        self.assertIn("- WhatsApp jumped +73.3% while walk_in fell -44.3%.", res["message"])

        fetch = harness.mock_requests("/supabase/rest/v1/daily_sales")[0]
        self.assertIn("day.gte.", fetch["path"])
        claude = harness.mock_requests("/anthropic/v1/messages")[0]
        self.assertEqual(claude["body"]["model"], "claude-haiku-5-5")
        self.assertNotIn("fallbacks", claude["body"])
        self.assertIn("Channel whatsapp: revenue AED 7,800", claude["body"]["messages"][0]["content"])

        tg = harness.mock_requests("/telegram/")
        self.assertEqual(len(tg), 1)
        self.assertIn("Revenue: AED 23,760", json.dumps(tg[0]["body"], ensure_ascii=False))

    def test_invented_numbers_fall_back_to_template(self):
        harness.mock_set("/anthropic/", harness.claude_reply({"bullets": [
            "Revenue grew 18% thanks to the weekend promotion.", "Orders were 95.", "All good."]}))
        _, res = harness.post(PATH, {}, headers=AUTH)
        self.assertEqual(res["commentary_source"], "template")
        self.assertIn("numbers not in the facts: 18, 95", res["commentary_note"])
        self.assertIn("- Top channel: website with AED 12,600.", res["message"])
        self.assertEqual(len(harness.mock_requests("/telegram/")), 1)


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
    """The same workflow with llm_provider = gemini (test bench copy on /daily-report-gemini)."""

    def setUp(self):
        harness.mock_reset()
        harness.mock_set("/supabase/rest/v1/daily_sales", live_rows(), method="GET")

    def test_gemini_bullets_are_checked_and_posted(self):
        harness.mock_set("/gemini/", harness.gemini_reply({"bullets": [
            "Revenue AED 23,760, +5.2% vs the 7-day average.", "whatsapp +73.3%, walk_in -44.3%.", "Check walk-in footfall."]}))
        _, res = harness.post(PATH + "-gemini", {}, headers=AUTH)
        self.assertEqual(res["commentary_source"], "gemini")
        req = harness.mock_requests("/gemini/")[0]
        self.assertTrue(req["path"].endswith("/v1beta/models/gemini-3.5-flash-lite:generateContent"))
        schema = req["body"]["generationConfig"]["responseSchema"]
        self.assertFalse(has_key(schema, "additionalProperties"))
        self.assertEqual(schema["properties"]["bullets"]["type"], "array")
        self.assertIn("Channel whatsapp: revenue AED 7,800", req["body"]["contents"][0]["parts"][0]["text"])
        self.assertIn("- whatsapp +73.3%, walk_in -44.3%.", res["message"])

    def test_rate_limit_backs_off_then_uses_template(self):
        harness.mock_set("/gemini/", harness.GEMINI_429, status=429)
        _, res = harness.post(PATH + "-gemini", {}, headers=AUTH)
        self.assertEqual(len(harness.mock_requests("/gemini/")), 3)
        self.assertEqual(res["commentary_source"], "template")
        self.assertIn("Gemini commentary not used: rate limited (429)", res["commentary_note"])
        self.assertEqual(len(harness.mock_requests("/telegram/")), 1)  # the report still goes out


if __name__ == "__main__":
    unittest.main()
