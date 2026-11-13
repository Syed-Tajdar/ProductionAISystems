"""Runs the workflow on the local test bench (python3 _n8n/harness.py up).

    python3 -m unittest discover -s sheet-to-crm-sync/tests -v
"""

import copy
import json
import random
import string
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "_n8n"))
import harness  # noqa: E402

PATH = "sheet-crm-sync"
AUTH = {"X-Webhook-Token": "test-webhook-token"}
VALUES = json.loads((HERE.parent / "samples" / "dry_run_request.json").read_text(encoding="utf-8"))["values"]
SHEET_GET = "/sheets/v4/spreadsheets/YOUR_SPREADSHEET_ID/values/"


def dry() -> dict:
    return harness.post(PATH, {"values": VALUES}, {"dry_run": 1}, headers=AUTH)[1]


def unique_values() -> list[list[str]]:
    """Sample sheet with fresh email addresses, so stored sync state never collides between runs."""
    tag = "".join(random.choices(string.ascii_lowercase, k=6))
    values = copy.deepcopy(VALUES)
    for row in values[1:]:
        if "@" in row[1]:
            row[1] = row[1].replace("@", f"+{tag}@")
    return values


def hubspot_ok(values: list[list[str]]) -> dict:
    emails = [r[1].lower() for r in values[1:] if r[1].endswith(".com")]
    return {"status": "COMPLETE", "results": [{"id": str(100 + i), "properties": {"email": e}} for i, e in enumerate(emails)]}


class DryRun(unittest.TestCase):
    def test_valid_rows_and_phone_normalization(self):
        res = dry()
        props = {u["row"]: u["properties"] for u in res["upserts"]}
        self.assertEqual(sorted(props), [2, 3, 9])
        self.assertEqual(props[2]["phone"], "+971500000101")  # local 050 number
        self.assertEqual(props[3]["phone"], "+966550000101")  # KSA number kept
        self.assertEqual(props[9]["phone"], "+971500000102")  # 00 prefix
        self.assertEqual(props[2]["email"], "sara.ahmed@example.com")  # lowercased
        self.assertEqual((props[2]["lifecyclestage"], props[2]["hubspot_owner_id"]), ("salesqualifiedlead", "1001"))
        self.assertEqual((props[9]["firstname"], props[9]["lastname"]), ("أحمد", "علي"))
        self.assertNotIn("hubspot_owner_id", props[3])  # no owner in the sheet

    def test_invalid_rows_get_readable_errors(self):
        errors = {x["row"]: x["status"] for x in dry()["invalid"]}
        self.assertEqual(errors, {
            4: 'error: invalid email "priya.nair@example"',
            5: 'error: invalid phone "12345"',
            6: 'error: unknown stage "Hot", use New, Qualified, Opportunity, Customer',
            7: "error: duplicate of row 2",
        })

    def test_batches_use_email_as_id(self):
        batch = dry()["hubspot_batches"][0]["inputs"]
        self.assertEqual(len(batch), 3)
        self.assertEqual({x["idProperty"] for x in batch}, {"email"})

    def test_requires_token_and_sends_nothing(self):
        harness.mock_reset()
        with self.assertRaises(Exception) as ctx:
            harness.post(PATH, {"values": VALUES}, {"dry_run": 1})
        self.assertEqual(getattr(ctx.exception, "code", None), 403)
        dry()
        self.assertEqual(harness.mock_requests(), [])


class LiveWithMockApis(unittest.TestCase):
    def setUp(self):
        harness.mock_reset()

    def run_sync(self, values, hubspot=None, status=200):
        harness.mock_set(SHEET_GET, {"range": "Leads!A1:H9", "values": values}, method="GET")
        if hubspot is not None:
            harness.mock_set("/hubspot/", hubspot, status=status)
        return harness.post(PATH, {}, headers=AUTH)[1]

    def test_upsert_write_back_and_skip_unchanged(self):
        values = unique_values()
        res = self.run_sync(values, hubspot_ok(values))
        self.assertEqual((res["synced"], res["failed"]), (3, 4))
        self.assertEqual(res["status_cells_written"], 7)

        upsert = harness.mock_requests("/hubspot/crm/v3/objects/contacts/batch/upsert")[0]
        self.assertEqual(upsert["headers"]["authorization"], "Bearer test-hubspot-token")
        self.assertEqual(len(upsert["body"]["inputs"]), 3)

        write = [r for r in harness.mock_requests("/sheets/") if r["method"] == "POST"][0]
        self.assertTrue(write["path"].endswith("/values:batchUpdate"))
        cells = {d["range"]: d["values"][0][0] for d in write["body"]["data"]}
        self.assertTrue(cells["Leads!H2"].startswith("synced "))
        self.assertEqual(cells["Leads!H7"], "error: duplicate of row 2")

        # Second run: the sheet now shows those statuses and nothing changed.
        for row_ref, text in cells.items():
            values[int(row_ref.split("H")[1]) - 1][7] = text
        harness.mock_reset()
        res = self.run_sync(values, hubspot_ok(values))
        self.assertEqual(res["unchanged"], 3)
        self.assertEqual(harness.mock_requests("/hubspot/"), [])
        self.assertEqual([r for r in harness.mock_requests("/sheets/") if r["method"] == "POST"], [])

    def test_hubspot_error_marks_rows_and_retries_next_run(self):
        values = unique_values()
        res = self.run_sync(values, {"status": "error", "message": "Property values were not valid"}, status=400)
        self.assertEqual(res["synced"], 0)
        write = [r for r in harness.mock_requests("/sheets/") if r["method"] == "POST"][0]
        cells = {d["range"]: d["values"][0][0] for d in write["body"]["data"]}
        self.assertTrue(cells["Leads!H2"].startswith("error: "))

        harness.mock_reset()
        res = self.run_sync(values, hubspot_ok(values))
        self.assertEqual(res["synced"], 3)  # nothing was remembered as synced, so it retries


if __name__ == "__main__":
    unittest.main()
