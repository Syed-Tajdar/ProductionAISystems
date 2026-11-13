"""The setup-n8n script (.github/scripts/setup_n8n.py).

    python3 -m unittest discover -s tests -p 'test_setup_n8n.py' -v

SetupN8n runs it against a fresh n8n 2.45 in Docker: creates the owner and an API key with only the scopes
DEPLOY.md lists (through n8n's own editor endpoints, as a person would in the UI), runs the script twice, and
checks what it left behind. FallbackPaths runs it against a fake n8n API (bench/fake_n8n.py) whose public API
differs the way one instance on another host did: no credential list or update, read-only credential ids, no
publish route, slow activation, a firewall that blocks plain JSON bodies, and HTML error pages. The fallbacks for
these are not needed on a standard n8n. The secrets are fake.
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "setup_n8n.py"
sys.path[:0] = [str(SCRIPT.parent), str(Path(__file__).resolve().parent / "bench")]
import fake_n8n  # noqa: E402
import setup_n8n  # noqa: E402

CONTAINER, PORT = "n8n-leads-setup-test", 5681
URL = f"http://localhost:{PORT}"
SCOPES = ["credential:create", "credential:list", "credential:update", "credential:delete",
          "workflow:create", "workflow:list", "workflow:read", "workflow:update", "workflow:activate"]
SECRETS = {"N8N_SCHEDULER_TOKEN": "sched-token-7f3a9c1e", "ANTHROPIC_API_KEY": "sk-ant-fake-41d2b8",
           "SUPABASE_SERVICE_KEY": "sb_secret_fake_93e1", "TELEGRAM_BOT_TOKEN": "123456:fake-telegram-c0de"}


def rest(method, path, body=None, cookie=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", **({"cookie": cookie} if cookie else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.headers.get("set-cookie"), json.loads(r.read())


def setUpModule():
    global API_KEY
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "-p", f"{PORT}:5678", "-e", "N8N_ENCRYPTION_KEY=setup-test",
                    "-e", "N8N_SECURE_COOKIE=false", "-e", "N8N_DIAGNOSTICS_ENABLED=false", "-e", "N8N_LISTEN_ADDRESS=0.0.0.0",
                    "-e", "N8N_BLOCK_ENV_ACCESS_IN_NODE=false", "-e", "LEADS_DEMO_MODE=true",
                    f"n8nio/n8n:{os.environ.get('N8N_VERSION', '2.45.0')}"], check=True, capture_output=True)
    for _ in range(90):
        try:
            rest("GET", "/rest/settings")
            break
        except (OSError, ValueError):  # still starting: no connection yet, or not JSON yet
            time.sleep(2)
    cookie, _ = rest("POST", "/rest/owner/setup", {"email": "owner@example.com", "firstName": "Test", "lastName": "Owner",
                                                    "password": "Setup-test-123"})
    _, key = rest("POST", "/rest/api-keys", {"label": "setup-n8n", "expiresAt": None, "scopes": SCOPES}, cookie=cookie.split(";")[0])
    API_KEY = key["data"]["rawApiKey"]


def tearDownModule():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


def run_script(**extra):
    env = {**os.environ, "N8N_URL": URL, "N8N_API_KEY": API_KEY, **SECRETS, **extra}
    env.pop("N8N_HOST", None)
    out = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=300)
    return out.returncode, out.stdout + out.stderr


def client():
    return setup_n8n.N8n(URL, API_KEY, [])


class SetupN8n(unittest.TestCase):
    def test_1_first_run_creates_everything(self):
        # A credential made by hand under the same name is reused, not duplicated.
        n8n = client()
        n8n.api("POST", "/credentials", {"name": "Telegram Bot", "type": "telegramApi", "data": {"accessToken": "by-hand"}})
        code, out = run_script()
        self.assertEqual(code, 0, out)
        for name in ("Supabase", "Anthropic API", "Scheduler token", "Gemini API", "SMTP"):
            self.assertRegex(out, rf"credential  {name} +\S+ +created")
        self.assertRegex(out, r"credential  Telegram Bot +\S+ +updated")
        self.assertEqual(out.count("published"), 4)
        self.assertEqual(out.count("health      ok"), 3)
        for secret in [API_KEY, *SECRETS.values()]:
            self.assertNotIn(secret, out)

    def test_2_workflows_are_wired_and_active(self):
        n8n = client()
        workflows = {w["name"]: w for w in setup_n8n.list_all(n8n, "/workflows")}
        ledger = workflows.pop(setup_n8n.LEDGER_NAME)
        self.assertFalse(ledger["active"])
        self.assertEqual(len(workflows), 4)
        errors_id = workflows["Lead workflow errors (Workflow C)"]["id"]
        cred_ids = {c["id"] for c in setup_n8n.list_all(n8n, "/credentials")}
        for name, w in workflows.items():
            full = n8n.api("GET", f"/workflows/{w['id']}")
            self.assertTrue(full["active"], name)
            if name != "Lead workflow errors (Workflow C)":
                self.assertEqual(full["settings"].get("errorWorkflow"), errors_id, name)
            for node in full["nodes"]:
                for ref in (node.get("credentials") or {}).values():
                    self.assertIn(ref["id"], cred_ids, f"{name}: {node['name']}")

    def test_3_second_run_updates_without_duplicates(self):
        code, out = run_script(N8N_SCHEDULER_TOKEN="sched-token-rotated-55")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count(" created"), 0, out)
        self.assertEqual(out.count("kept (placeholder)"), 2)
        self.assertEqual(out.count("updated, published"), 4)
        n8n = client()
        self.assertEqual(len(setup_n8n.list_all(n8n, "/workflows")), 5)  # the four and the ledger
        self.assertEqual(len(setup_n8n.list_all(n8n, "/credentials")), 6)
        on_record = setup_n8n.Ledger(n8n).ids
        self.assertEqual(len(on_record), 5)  # every credential it created; Telegram Bot was made by hand
        # The webhook now takes the rotated token and refuses the old one. (Past the token check the run fails
        # here, since this n8n has no database to reach, so anything but 403 means the token was accepted.)
        def follow_up(token):
            return n8n.request("POST", "/webhook/lead-follow-up", {"max_leads": 0}, api=False, headers={"X-Scheduler-Token": token})[0]
        self.assertEqual(follow_up(SECRETS["N8N_SCHEDULER_TOKEN"]), 403)
        self.assertNotEqual(follow_up("sched-token-rotated-55"), 403)

    def test_missing_settings_are_named(self):
        code, out = run_script(TELEGRAM_BOT_TOKEN="", N8N_SCHEDULER_TOKEN=" ")
        self.assertEqual(code, 2)
        self.assertIn("missing settings: N8N_SCHEDULER_TOKEN, TELEGRAM_BOT_TOKEN", out)

    def test_wrong_api_key_stops_with_a_clear_message(self):
        code, out = run_script(N8N_API_KEY="not-a-valid-key")
        self.assertEqual(code, 1)
        self.assertIn("rejected N8N_API_KEY (HTTP 401). This host may not be your n8n instance", out)

    def test_gives_up_when_n8n_never_answers(self):
        code, out = run_script(N8N_URL="http://localhost:9", N8N_WAKE_SECONDS="12")
        self.assertEqual(code, 1)
        self.assertIn("waiting for n8n to start", out)
        self.assertIn("http://localhost:9 did not answer within 12 s", out)
        self.assertIn("Nothing was changed", out)

    def test_supabase_key_formats(self):
        self.assertEqual(json.loads(setup_n8n.supabase_auth("sb_secret_x")["json"]), {"headers": {"apikey": "sb_secret_x"}})
        legacy = json.loads(setup_n8n.supabase_auth("eyJhbGciOi.x.y")["json"])["headers"]
        self.assertEqual(legacy, {"apikey": "eyJhbGciOi.x.y", "Authorization": "Bearer eyJhbGciOi.x.y"})


SECRET_ENV = {"N8N_API_KEY": fake_n8n.API_KEY, **SECRETS}


def count(out: str, kind: str, action: str) -> int:
    return len(re.findall(rf"^{kind} .*  {re.escape(action)}", out, re.M))


def referenced(fake) -> set[str]:
    return {ref["id"] for w in fake.workflows.values() for node in w.get("nodes", [])
            for ref in (node.get("credentials") or {}).values()}


class FallbackPaths(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(setup_n8n, k) for k in ("POLL_SECONDS", "ACTIVATE_SECONDS", "WAKE_SECONDS")}
        setup_n8n.POLL_SECONDS, setup_n8n.ACTIVATE_SECONDS, setup_n8n.WAKE_SECONDS = 0.01, 5, 5
        self.fakes = []

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(setup_n8n, k, v)
        for fake in self.fakes:
            fake.stop()

    def fake(self, **quirks):
        fake = fake_n8n.FakeN8n(**quirks)
        self.fakes.append(fake)
        return fake

    def run_setup(self, fake, **env) -> str:
        log = []
        lines = setup_n8n.run({"N8N_URL": fake.url, **SECRET_ENV, **env}, log=log.append)
        out = "\n".join(log + lines)
        for secret in SECRET_ENV.values():
            self.assertNotIn(secret, out)
        return out

    def test_credential_list_405_falls_back_to_workflow_references(self):
        fake = self.fake(no_list=True, id_read_only=True)
        first = self.run_setup(fake)
        self.assertEqual(count(first, "credential", "created"), 6)
        second = self.run_setup(fake)
        # No list and no update: the real credentials are found through the workflows and replaced, the
        # placeholders kept, and the replaced copies deleted, so the instance holds exactly six again.
        self.assertEqual(count(second, "credential", "replaced"), 4)
        self.assertEqual(count(second, "credential", "kept (placeholder)"), 2)
        self.assertEqual(second.count("deleted (earlier copy)"), 4)
        self.assertEqual(len(fake.credentials), 6)
        self.assertEqual(set(fake.credentials), referenced(fake))
        self.assertFalse([r for r in fake.requests if r["method"] == "PATCH"])

    def test_read_only_id_creates_without_id_and_rewires_nodes(self):
        fake = self.fake(id_read_only=True)
        self.run_setup(fake)
        posts = [r["body"] for r in fake.requests if r["method"] == "POST" and r["path"] == "/api/v1/credentials"]
        self.assertIn("id", posts[0])                       # tried once with the id from the workflow files
        self.assertTrue(all("id" not in b for b in posts[1:]))  # then without
        self.assertEqual(len(fake.credentials), 6)
        self.assertEqual(set(fake.credentials), referenced(fake))
        self.assertFalse(referenced(fake) & {c["ref"] for c in setup_n8n.credentials(SECRET_ENV)})

    def test_publish_404_falls_back_to_activate(self):
        fake = self.fake(no_publish=True)
        out = self.run_setup(fake)
        activated = [r["path"] for r in fake.requests if r["path"].endswith("/activate")]
        self.assertEqual(len(activated), 4)
        self.assertEqual(out.count("activated"), 4)
        self.assertTrue(all(w["active"] for w in fake.workflows.values() if w["name"] != setup_n8n.LEDGER_NAME))

    def test_activation_is_read_back_until_active(self):
        fake = self.fake(activate_after_reads=3)
        self.run_setup(fake)
        reads = [r for r in fake.requests if r["method"] == "GET" and r["path"].count("/") == 4]
        self.assertGreaterEqual(len(reads), 12)  # three reads for each of the four workflows

    def test_activation_gives_up_after_the_timeout(self):
        setup_n8n.ACTIVATE_SECONDS = 0.3
        fake = self.fake(activate_after_reads=-1)
        with self.assertRaisesRegex(setup_n8n.SetupError, r"Lead workflow errors \(Workflow C\) is not active 0.3 s after"):
            self.run_setup(fake)

    def test_bodies_are_sent_gzip(self):
        fake = self.fake(require_gzip=True)
        self.run_setup(fake)
        api_bodies = [r for r in fake.requests if r["has_body"] and r["path"].startswith("/api/v1/")]
        self.assertGreater(len(api_bodies), 10)
        self.assertTrue(all(r["gzip"] and isinstance(r["body"], dict) for r in api_bodies))
        # The same request as plain JSON is what the firewall blocks.
        req = urllib.request.Request(fake.url + "/api/v1/workflows", data=b'{"name": "x"}', method="POST",
                                     headers={"X-N8N-API-KEY": fake_n8n.API_KEY, "content-type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as blocked:
            urllib.request.urlopen(req)
        self.assertEqual(blocked.exception.code, 403)

    def test_html_error_page_becomes_a_readable_message(self):
        fake = self.fake()
        fake.fail[("POST", "/api/v1/workflows")] = (502, "<html><head><title>502 Bad Gateway</title><style>p{}</style>"
                                                         "</head><body><h1>Bad Gateway</h1><p>Upstream timed out.</p></body></html>")
        with self.assertRaises(setup_n8n.SetupError) as stopped:
            self.run_setup(fake)
        self.assertEqual(str(stopped.exception), "POST /workflows: HTTP 502: 502 Bad Gateway: Bad Gateway Upstream timed out.")
        self.assertEqual(setup_n8n.readable(fake_n8n.WAF_PAGE), "Request blocked: Request blocked The firewall blocked this request.")
        self.assertEqual(setup_n8n.readable({"message": "request/body/id is read-only"}), "request/body/id is read-only")
        self.assertEqual(setup_n8n.readable("  plain\n text "), "plain text")

    def test_rerun_deletes_every_earlier_copy(self):
        fake = self.fake(no_list=True, id_read_only=True)
        self.run_setup(fake)
        # A run that stops after creating its credentials but before any workflow points at them: on an instance
        # that cannot list credentials, only the ledger knows about these copies.
        errors_wf = next(w["id"] for w in fake.workflows.values() if w["name"] == "Lead workflow errors (Workflow C)")
        fake.fail[("PUT", f"/api/v1/workflows/{errors_wf}")] = (504, "<html><head><title>Gateway Timeout</title></head></html>")
        with self.assertRaises(setup_n8n.SetupError):
            self.run_setup(fake)
        orphans = set(fake.credentials) - referenced(fake)
        self.assertEqual(len(orphans), 4)
        out = self.run_setup(fake)
        self.assertEqual(len(fake.credentials), 6)
        self.assertEqual(set(fake.credentials), referenced(fake))
        self.assertEqual(out.count("deleted (earlier copy)"), 8)  # the four orphans and the four replaced copies
        for cred_id in orphans:
            self.assertIn(f"credential  {cred_id}", out)
        ledger = setup_n8n.Ledger(setup_n8n.N8n(fake.url, fake_n8n.API_KEY, []))
        self.assertEqual(set(ledger.ids), set(fake.credentials))

    def test_a_credential_that_cannot_be_put_on_record_is_taken_back(self):
        fake = self.fake(no_list=True, id_read_only=True)
        self.run_setup(fake)
        ledger_id = next(w["id"] for w in fake.workflows.values() if w["name"] == setup_n8n.LEDGER_NAME)
        fake.fail[("PUT", f"/api/v1/workflows/{ledger_id}")] = (503, "<html><head><title>Unavailable</title></head></html>")
        with self.assertRaisesRegex(setup_n8n.SetupError, "HTTP 503: Unavailable"):
            self.run_setup(fake)
        self.assertEqual(len(fake.credentials), 6)  # the copy made just before the ledger failed was deleted
        self.assertEqual(set(fake.credentials), referenced(fake))

    def test_rejected_key_stops_before_anything_is_written(self):
        fake = self.fake(api_key="the-key-of-another-instance")
        with self.assertRaises(setup_n8n.SetupError) as stopped:
            self.run_setup(fake)
        message = str(stopped.exception)
        for part in ("rejected N8N_API_KEY (HTTP 401)", "may not be your n8n instance", "Render dashboard", "Nothing was changed"):
            self.assertIn(part, message)
        self.assertEqual(fake.writes(), [])
        self.assertEqual([r["path"] for r in fake.requests][:2], ["/healthz", "/api/v1/workflows"])

    def test_host_without_the_n8n_api_stops(self):
        fake = self.fake(no_api=True)
        with self.assertRaisesRegex(setup_n8n.SetupError, "has no n8n public API"):
            self.run_setup(fake)
        self.assertEqual(fake.writes(), [])

    def test_host_given_as_a_url_is_reduced_to_the_host(self):
        fake = self.fake(api_key="other")
        with self.assertRaisesRegex(setup_n8n.SetupError, r"^127\.0\.0\.1:\d+ rejected"):
            self.run_setup(fake, N8N_HOST=fake.url + "/home/workflows")


if __name__ == "__main__":
    unittest.main()
