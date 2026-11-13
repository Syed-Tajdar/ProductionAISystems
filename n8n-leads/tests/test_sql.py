"""The Supabase functions against a real Postgres 17 (Docker), with every migration applied.

    python3 -m unittest discover -s tests -p 'test_sql.py' -v
"""

import json
import subprocess
import threading
import time
import unittest
from pathlib import Path

MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "supabase" / "migrations").glob("*.sql"))
CONTAINER = "n8n-leads-sql-test"


def psql(sql: str) -> str:
    out = subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1",
                          "-tAq", "-f", "-"], input=sql, text=True, capture_output=True)
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip()


def upsert(**lead) -> dict:
    return json.loads(psql(f"select upsert_lead('{json.dumps(lead).replace(chr(39), chr(39) * 2)}'::jsonb);"))


def events(lead_id: str) -> list[str]:
    return psql(f"select event from lead_events where lead_id = '{lead_id}' order by id;").split()


def setUpModule():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "-e", "POSTGRES_PASSWORD=test", "postgres:17-alpine"],
                   check=True, capture_output=True)
    for _ in range(60):
        if subprocess.run(["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
            break
        time.sleep(1)
    time.sleep(1)
    psql("create role anon; create role authenticated; create role service_role;")  # as on Supabase
    for path in MIGRATIONS:
        psql(path.read_text(encoding="utf-8"))


def tearDownModule():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


CLASSIFIED = {"status": "classified", "intent": "rent", "urgency": "hot", "language": "en",
              "summary": "Wants a 2 bedroom in Dubai Marina next month.", "provider": "anthropic", "model": "claude-haiku-5-5"}


class UpsertLead(unittest.TestCase):
    def setUp(self):
        psql("truncate leads cascade;")

    def test_new_lead_with_classification(self):
        res = upsert(name="Sara Ahmed", phone="+971500000101", email="sara@example.com", message="2 bed in Marina",
                     meta={"page": "https://example.com"}, classification=CLASSIFIED)
        lead = res["lead"]
        self.assertEqual(res["action"], "created")
        self.assertEqual((lead["intent"], lead["urgency"], lead["language"], lead["status"]), ("rent", "hot", "en", "new"))
        self.assertEqual(events(lead["id"]), ["created", "classified"])

    def test_failed_classification_is_stored_as_unclassified(self):
        res = upsert(phone="+971500000101", message="hello", language="ar",
                     classification={"status": "failed", "provider": "anthropic", "error": "HTTP 529"})
        lead = res["lead"]
        self.assertEqual((lead["intent"], lead["urgency"], lead["summary"], lead["language"]), ("unclassified", None, None, "ar"))
        self.assertEqual(events(lead["id"]), ["created", "classification_failed"])

    def test_without_classification_phase_2(self):
        lead = upsert(email="omar@example.com", message="price list?")["lead"]
        self.assertEqual(lead["intent"], "unclassified")
        self.assertEqual(events(lead["id"]), ["created"])

    def test_dedupe_by_phone_then_by_email(self):
        first = upsert(name="Sara", phone="+971500000101", message="first", classification=CLASSIFIED)["lead"]
        by_phone = upsert(phone="+971500000101", email="sara@example.com", message="second")
        self.assertEqual((by_phone["action"], by_phone["lead"]["id"]), ("updated", first["id"]))
        self.assertEqual(by_phone["lead"]["email"], "sara@example.com")  # missing email filled in
        self.assertEqual(by_phone["lead"]["message"], "second")
        self.assertEqual(by_phone["lead"]["intent"], "rent")  # no new classification: old one kept
        by_email = upsert(name="Sara A.", email="sara@example.com", message="third")
        self.assertEqual((by_email["action"], by_email["lead"]["id"], by_email["lead"]["name"]), ("updated", first["id"], "Sara A."))
        self.assertEqual(psql("select count(*) from leads;"), "1")

    def test_phone_wins_and_owned_email_is_not_moved(self):
        a = upsert(phone="+971500000104", message="a")["lead"]
        b = upsert(email="b@example.com", message="b")["lead"]
        res = upsert(phone="+971500000104", email="b@example.com", message="both")
        self.assertEqual(res["lead"]["id"], a["id"])
        self.assertIsNone(res["lead"]["email"])  # b@example.com belongs to lead b
        self.assertEqual(psql(f"select email from leads where id = '{b['id']}';"), "b@example.com")

    def test_failed_reclassification_keeps_the_previous_one(self):
        upsert(phone="+971500000101", message="first", classification=CLASSIFIED)
        lead = upsert(phone="+971500000101", message="again", classification={"status": "failed", "error": "timeout"})["lead"]
        self.assertEqual((lead["intent"], lead["urgency"]), ("rent", "hot"))

    def test_rejects_a_lead_without_contact(self):
        with self.assertRaisesRegex(RuntimeError, "phone or email is required"):
            upsert(name="Nobody", message="hi")

    def test_race_for_the_same_phone_creates_one_lead(self):
        # Session 1 inserts and holds its transaction open; session 2 runs meanwhile, waits on the
        # unique index, gets a unique_violation when session 1 commits, and retries as an update.
        slow = threading.Thread(target=psql, args=(
            "begin; select upsert_lead('{\"phone\": \"+971502222222\", \"message\": \"one\"}'); select pg_sleep(2); commit;",))
        slow.start()
        time.sleep(0.5)
        second = upsert(phone="+971502222222", message="two")
        slow.join()
        self.assertEqual(second["action"], "updated")
        self.assertEqual(psql("select count(*) || ' ' || max(message) from leads;"), "1 two")

    def test_only_the_service_role_can_call_it(self):
        grants = psql("select grantee from information_schema.routine_privileges "
                      "where routine_name = 'upsert_lead' order by grantee;").split()
        self.assertIn("service_role", grants)
        self.assertNotIn("anon", grants)
        self.assertNotIn("authenticated", grants)
        self.assertNotIn("PUBLIC", grants)


def lead(email, *, hours_old=30, status="new", intent="rent", phone=None) -> str:
    row = upsert(email=email, phone=phone, message="hi",
                 classification={"status": "classified", "intent": intent, "urgency": "warm", "language": "en", "summary": "x"})
    lead_id = row["lead"]["id"]
    psql(f"update leads set created_at = now() - interval '{hours_old} hours', status = '{status}' where id = '{lead_id}';")
    return lead_id


def claim(max_leads=20) -> list[str]:
    out = psql(f"select coalesce(json_agg(email order by email), '[]') from claim_follow_ups({max_leads});")
    return json.loads(out)


def finish(results) -> dict:
    return json.loads(psql(f"select finish_follow_ups('{json.dumps(results)}'::jsonb);"))


class FollowUps(unittest.TestCase):
    def setUp(self):
        psql("truncate leads cascade;")

    def test_claims_only_due_leads_once(self):
        lead("due@example.com")
        lead("fresh@example.com", hours_old=2)
        lead("contacted@example.com", status="contacted")
        lead("spam@example.com", intent="spam")
        lead(None, phone="+971503333333")  # no email: cannot be emailed
        self.assertEqual(claim(), ["due@example.com"])
        self.assertEqual(psql("select status from leads where email = 'due@example.com';"), "followed_up")
        self.assertEqual(claim(), [])  # already claimed

    def test_limit_takes_the_oldest_first(self):
        lead("old@example.com", hours_old=72)
        lead("older@example.com", hours_old=96)
        lead("newer@example.com", hours_old=30)
        self.assertEqual(claim(max_leads=2), ["old@example.com", "older@example.com"])

    def test_overlapping_runs_never_claim_the_same_lead(self):
        for i in range(4):
            lead(f"l{i}@example.com")
        out = {}
        slow = threading.Thread(target=lambda: out.update(first=psql(
            "begin; select json_agg(email) from claim_follow_ups(2); select pg_sleep(2); commit;")))
        slow.start()
        time.sleep(0.5)
        second = claim()  # runs while the first run still holds its rows
        slow.join()
        first = json.loads(out["first"].splitlines()[0])
        self.assertEqual(len(first), 2)
        self.assertEqual(sorted(first + second), [f"l{i}@example.com" for i in range(4)])

    def test_finish_logs_and_returns_failures_to_new(self):
        ok_id, bad_id = lead("ok@example.com"), lead("bad@example.com")
        claim()
        res = finish([{"lead_id": ok_id, "ok": True, "message_id": "<m1>"},
                      {"lead_id": bad_id, "ok": False, "error": "550 mailbox unavailable"}])
        self.assertEqual(res, {"followed_up": 1, "failed": 1})
        self.assertEqual(psql(f"select status from leads where id = '{ok_id}';"), "followed_up")
        self.assertEqual(psql(f"select status || ' ' || coalesce(followed_up_at::text, 'none') from leads where id = '{bad_id}';"), "new none")
        self.assertEqual(events(ok_id)[-1], "followed_up")
        self.assertEqual(events(bad_id)[-1], "follow_up_failed")
        self.assertIn("550", psql(f"select payload->>'error' from lead_events where lead_id = '{bad_id}' and event = 'follow_up_failed';"))

    def test_three_failures_stop_the_retries(self):
        bad_id = lead("bounce@example.com")
        for _ in range(3):
            self.assertEqual(claim(), ["bounce@example.com"])
            finish([{"lead_id": bad_id, "ok": False, "error": "550"}])
        self.assertEqual(claim(), [])

    def test_daily_summary_view(self):
        lead("a@example.com", hours_old=1)
        lead("b@example.com", hours_old=30)
        claim()
        row = psql("select row_to_json(s) from lead_daily_summary s;")
        self.assertEqual(json.loads(row), {"new_leads": 1, "hot_leads": 0, "won": 0, "awaiting_follow_up": 0, "followed_up": 1})

    def test_new_event_types_and_grants(self):
        lead_id = lead("e@example.com")
        psql(f"insert into lead_events (lead_id, event) values ('{lead_id}', 'auto_replied'), ('{lead_id}', 'auto_reply_failed');")
        grants = psql("select routine_name || ':' || grantee from information_schema.routine_privileges "
                      "where routine_name in ('claim_follow_ups', 'finish_follow_ups') order by 1;").split()
        self.assertEqual(grants, ["claim_follow_ups:postgres", "claim_follow_ups:service_role",
                                  "finish_follow_ups:postgres", "finish_follow_ups:service_role"])


def take(client="203.0.113.7", per_client=2, window=600, daily=5) -> dict:
    return json.loads(psql(f"select lead_demo_take('{client}', {per_client}, {window}, {daily});"))


class Demo(unittest.TestCase):
    def setUp(self):
        psql("truncate leads cascade; truncate lead_demo_requests;")

    def test_per_visitor_limit_then_daily_cap(self):
        self.assertTrue(take()["allowed"])
        self.assertTrue(take()["allowed"])
        refused = take()
        self.assertEqual((refused["allowed"], refused["reason"]), (False, "client"))
        self.assertTrue(0 < refused["retry_after"] <= 601)
        for i in range(3):
            self.assertTrue(take(client=f"198.51.100.{i}")["allowed"])
        daily = take(client="192.0.2.1")
        self.assertEqual((daily["allowed"], daily["reason"]), (False, "daily"))
        self.assertTrue(0 < daily["retry_after"] <= 86401)

    def test_old_requests_leave_the_window(self):
        take(), take()
        psql("update lead_demo_requests set at = now() - interval '11 minutes';")
        self.assertTrue(take()["allowed"])

    def test_addresses_are_stored_hashed(self):
        take(client="203.0.113.7")
        stored = psql("select client from lead_demo_requests;")
        self.assertNotIn("203.0.113.7", stored)
        self.assertEqual(len(stored), 64)

    def test_alert_cap(self):
        taken = [json.loads(psql("select lead_demo_take_alert(2);"))["allowed"] for _ in range(3)]
        self.assertEqual(taken, [True, True, False])
        self.assertTrue(take(daily=1)["allowed"])  # alerts do not count as submissions

    def test_old_demo_leads_are_deleted_and_real_ones_kept(self):
        old_demo = upsert(email="old@example.com", message="x", meta={"demo": True})["lead"]["id"]
        new_demo = upsert(email="new@example.com", message="x", meta={"demo": True})["lead"]["id"]
        real = upsert(email="real@example.com", message="x")["lead"]["id"]
        psql(f"update leads set created_at = now() - interval '8 days' where id in ('{old_demo}', '{real}');")
        take()
        self.assertEqual(psql("select string_agg(email, ',' order by email) from leads;"), "new@example.com,real@example.com")
        self.assertEqual(psql(f"select count(*) from lead_events where lead_id = '{old_demo}';"), "0")
        self.assertTrue(new_demo)

    def test_demo_leads_get_no_follow_up_and_do_not_wait_for_one(self):
        demo_id = lead("demo@example.com")
        psql(f"update leads set meta = meta || '{{\"demo\": true}}' where id = '{demo_id}';")
        lead("real@example.com")
        self.assertEqual(claim(), ["real@example.com"])
        self.assertEqual(json.loads(psql("select row_to_json(s) from lead_daily_summary s;"))["awaiting_follow_up"], 0)

    def test_n8n_schema_and_grants(self):
        self.assertEqual(psql("select count(*) from pg_namespace where nspname = 'n8n';"), "1")
        self.assertEqual(psql("select has_schema_privilege('anon', 'n8n', 'usage');"), "f")
        grants = psql("select routine_name || ':' || grantee from information_schema.routine_privileges "
                      "where routine_name like 'lead_demo_%' order by 1;").split()
        self.assertEqual(grants, ["lead_demo_take:postgres", "lead_demo_take:service_role",
                                  "lead_demo_take_alert:postgres", "lead_demo_take_alert:service_role"])


if __name__ == "__main__":
    unittest.main()
