"""Sets up the lead workflows on an n8n instance through its public API (the setup-n8n workflow).

    N8N_HOST=<your-service>.onrender.com N8N_API_KEY=... N8N_SCHEDULER_TOKEN=... ANTHROPIC_API_KEY=... \
    SUPABASE_SERVICE_KEY=... TELEGRAM_BOT_TOKEN=... python .github/scripts/setup_n8n.py

1. Host check, before anything is written: waits for /healthz (a sleeping instance on Render's free plan takes
   2 to 3 minutes), then reads the workflow list with N8N_API_KEY. A rejected key stops the run: the host may
   not be your instance.
2. Credentials: the six the workflow files use, by name. Where n8n can list and update credentials, existing ones
   are updated in place (the normal case); where it cannot (an instance answering 405), a fresh copy replaces each one. The
   Gemini and SMTP credentials are placeholders (the demo calls neither) and are kept once they exist.
3. Workflows: the four are created or updated by name, every node is pointed at its credential, Workflow C is set
   as the error workflow of A and both B workflows, and all four are published (or activated, where the publish
   route is missing) and read back until n8n shows them active.
4. Cleanup: every credential this script created earlier and no longer uses is deleted. Their ids are kept in a
   small inactive workflow, "setup-n8n credential ledger", because the API cannot always list credentials.
5. Health check: /healthz, the form webhook (a honeypot submission, which stores nothing and calls no API), and
   the follow-up webhook without a token (must answer 403).

Safe to run again. Request bodies are sent gzip-compressed (a firewall in front of one instance blocked some
workflow code sent as plain JSON). Prints names, ids and counts, never a secret value. N8N_URL (e.g. http://localhost:5678) replaces
https://N8N_HOST for local tests. Writes the summary to $GITHUB_STEP_SUMMARY when that is set.
Exit codes: 0 done, 1 failed, 2 missing settings.
"""

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "n8n-leads" / "workflows"
ORDER = ["lead-errors.json", "lead-intake.json", "lead-follow-up.json", "lead-daily-summary.json"]
ERRORS_FILE = "lead-errors.json"
LEDGER_NAME = "setup-n8n credential ledger"
SECRETS = ["N8N_API_KEY", "N8N_SCHEDULER_TOKEN", "ANTHROPIC_API_KEY", "SUPABASE_SERVICE_KEY", "TELEGRAM_BOT_TOKEN"]

# Timing; module values so tests can shorten them.
WAKE_SECONDS = int(os.environ.get("N8N_WAKE_SECONDS", "420"))          # n8n waking up from sleep
ACTIVATE_SECONDS = int(os.environ.get("N8N_ACTIVATE_SECONDS", "300"))  # one workflow becoming active
POLL_SECONDS = 10
REQUEST_SECONDS = 60
ACTIVATE_REQUEST_SECONDS = 240  # activation on 0.1 CPU can outlast a normal request

NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


class SetupError(RuntimeError):
    pass


def supabase_auth(key: str) -> dict:
    # New sb_secret_ keys go in apikey only; a legacy service_role JWT also goes in Authorization.
    headers = {"apikey": key}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return {"json": json.dumps({"headers": headers})}


def credentials(env: dict) -> list[dict]:
    """ref: the id the workflow files use; placeholder=True means: create once, then keep."""
    return [
        {"ref": "nlSupabaseKeys01", "name": "Supabase", "type": "httpCustomAuth", "data": supabase_auth(env["SUPABASE_SERVICE_KEY"])},
        {"ref": "nlAnthropicKey01", "name": "Anthropic API", "type": "httpHeaderAuth",
         "data": {"name": "x-api-key", "value": env["ANTHROPIC_API_KEY"]}},
        {"ref": "nlTelegramBot001", "name": "Telegram Bot", "type": "telegramApi", "data": {"accessToken": env["TELEGRAM_BOT_TOKEN"]}},
        {"ref": "nlSchedulerTok01", "name": "Scheduler token", "type": "httpHeaderAuth",
         "data": {"name": "X-Scheduler-Token", "value": env["N8N_SCHEDULER_TOKEN"]}},
        {"ref": "nlGeminiKey00001", "name": "Gemini API", "type": "httpHeaderAuth",
         "data": {"name": "x-goog-api-key", "value": "unused"}, "placeholder": True},
        {"ref": "nlSmtpServer0001", "name": "SMTP", "type": "smtp",
         "data": {"host": "localhost", "port": 25, "secure": False, "user": "", "password": ""}, "placeholder": True},
    ]


def readable(out) -> str:
    """The message of an n8n error, or the title and text of an HTML error page (a proxy or firewall answer)."""
    if isinstance(out, dict):
        return str(out.get("message") or json.dumps(out))[:300]
    text = str(out)
    if "<" not in text:
        return " ".join(text.split())[:300]
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    body = re.sub(r"<(script|style|title|head)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    body = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    title_text = " ".join(title.group(1).split()) if title else ""
    return (f"{title_text}: {body}" if title_text and body else title_text or body)[:300]


class N8n:
    def __init__(self, base: str, api_key: str, secrets: list[str]):
        self.base, self.key, self.secrets = base.rstrip("/"), api_key, [s for s in secrets if s]

    def scrub(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text

    def request(self, method: str, path: str, body=None, *, api=True, headers=None, form=None, timeout=None):
        """Returns (status, parsed JSON or text). Network errors are raised to the caller."""
        url = self.base + ("/api/v1" if api else "") + path
        data, h = None, {"accept": "application/json", **(headers or {})}
        if api:
            h["X-N8N-API-KEY"] = self.key
        if body is not None:
            data = gzip.compress(json.dumps(body).encode())
            h["content-type"], h["content-encoding"] = "application/json", "gzip"
        elif form is not None:
            data, h["content-type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout or REQUEST_SECONDS) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        text = raw.decode(errors="replace")
        try:
            return status, json.loads(text) if text.strip().startswith(("{", "[")) else text
        except ValueError:
            return status, text

    def api(self, method: str, path: str, body=None):
        status, out = self.request(method, path, body)
        if status >= 400:
            raise SetupError(self.scrub(f"{method} {path.split('?')[0]}: HTTP {status}: {readable(out)}"))
        return out


def check_host(n8n: N8n, host: str, log=print) -> int:
    """Nothing is written before this passes: /healthz answers, then an authenticated read with the API key works.
    Waits while a sleeping instance starts. Returns the seconds waited."""
    started, said, last = time.monotonic(), False, "no answer"
    deadline = started + WAKE_SECONDS
    while True:
        try:
            status, _ = n8n.request("GET", "/healthz", api=False)
            if status == 200:
                status, out = n8n.request("GET", "/workflows?limit=1")
                if status == 200:
                    return round(time.monotonic() - started)
                if status == 401:
                    raise SetupError(
                        f"{host} rejected N8N_API_KEY (HTTP 401). This host may not be your n8n instance: copy the exact "
                        "URL from your service's page in the Render dashboard (Render may add a suffix to the name, "
                        "e.g. <name>-<suffix>.onrender.com) into N8N_HOST, and create the API key on that instance. "
                        "Nothing was changed.")
                if status == 403:
                    raise SetupError("N8N_API_KEY lacks a scope (HTTP 403): create a key with all scopes. Nothing was changed.")
                if status == 404:
                    raise SetupError(f"{host} answers /healthz but has no n8n public API at /api/v1: is N8N_HOST "
                                     "an n8n instance? Nothing was changed.")
            last = f"HTTP {status}"
        except NETWORK_ERRORS as e:
            last = type(e).__name__
        if time.monotonic() > deadline:
            raise SetupError(f"{host} did not answer within {WAKE_SECONDS} s (last: {last}). Nothing was changed.")
        if not said:
            log(f"waiting for n8n to start ({last}); a sleeping free instance takes 2 to 3 minutes")
            said = True
        time.sleep(POLL_SECONDS)


def list_all(n8n: N8n, path: str) -> list[dict]:
    items, cursor = [], None
    while True:
        sep = "&" if "?" in path else "?"
        page = n8n.api("GET", f"{path}{sep}limit=250" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else ""))
        items += page.get("data", [])
        cursor = page.get("nextCursor")
        if not cursor:
            return items


def find_workflow(n8n: N8n, name: str) -> dict | None:
    matches = [w for w in list_all(n8n, "/workflows?name=" + urllib.parse.quote(name)) if w["name"] == name]
    if len(matches) > 1:
        raise SetupError(f'{len(matches)} workflows are named "{name}": delete the extra ones in n8n, then run again')
    return matches[0] if matches else None


class Ledger:
    """Ids of the credentials this script created, kept in an inactive workflow with one sticky note, so a later
    run can delete copies it no longer uses even where the API cannot list credentials."""

    NOTE = ("Kept by the setup-n8n GitHub Actions workflow: the credentials it created, so a rerun can delete "
            "old copies. Do not edit or activate.\n\n")

    def __init__(self, n8n: N8n):
        self.n8n, self.workflow = n8n, find_workflow(n8n, LEDGER_NAME)
        self.ids: dict[str, str] = {}
        if self.workflow:
            full = n8n.api("GET", f'/workflows/{self.workflow["id"]}')
            for node in full.get("nodes", []):
                found = re.search(r"```json\s*(\{.*?\})\s*```", node.get("parameters", {}).get("content", ""), re.S)
                if found:
                    self.ids = json.loads(found.group(1)).get("credentials", {})

    def body(self) -> dict:
        content = self.NOTE + "```json\n" + json.dumps({"credentials": self.ids}, indent=1, sort_keys=True) + "\n```"
        note = {"id": "5e7d1a2c-0b8f-4c3e-9a51-2f6d8e4b7c90", "name": "Credential ledger", "type": "n8n-nodes-base.stickyNote",
                "typeVersion": 1, "position": [0, 0], "parameters": {"content": content, "width": 520, "height": 320}}
        return {"name": LEDGER_NAME, "nodes": [note], "connections": {}, "settings": {"executionOrder": "v1"}}

    def save(self) -> None:
        if self.workflow:
            self.n8n.api("PUT", f'/workflows/{self.workflow["id"]}', self.body())
        else:
            self.workflow = self.n8n.api("POST", "/workflows", self.body())

    def add(self, cred_id: str, name: str) -> None:
        self.ids[cred_id] = name
        self.save()  # at once, so a run that fails later still leaves its copies on record


def credential_refs_in_use(n8n: N8n) -> dict[str, list[str]]:
    """Credential name -> ids referenced by the four lead workflows on the instance (more than one after a run
    that stopped halfway)."""
    names = {json.loads((WORKFLOWS / f).read_text(encoding="utf-8"))["name"] for f in ORDER}
    refs: dict[str, list[str]] = {}
    for wf in list_all(n8n, "/workflows"):
        if wf["name"] not in names:
            continue
        nodes = wf.get("nodes") or n8n.api("GET", f'/workflows/{wf["id"]}').get("nodes", [])
        for node in nodes:
            for ref in (node.get("credentials") or {}).values():
                if ref.get("id") and ref.get("name") and ref["id"] not in refs.setdefault(ref["name"], []):
                    refs[ref["name"]].append(ref["id"])
    return refs


class CredentialSetup:
    """Creates, updates or replaces the credentials; learns which API routes the instance has as it goes."""

    def __init__(self, n8n: N8n, ledger: Ledger):
        self.n8n, self.ledger = n8n, ledger
        self.can_patch, self.id_writable = True, True
        self.superseded: list[str] = []  # ids the workflows used before this run and no longer use

    def listed(self) -> list[dict] | None:
        status, out = self.n8n.request("GET", "/credentials?limit=1")
        if status in (404, 405):  # an instance without a credential list (and then without update either)
            self.can_patch = False
            return None
        if status >= 400:
            raise SetupError(f"GET /credentials: HTTP {status}: {readable(out)}")
        return list_all(self.n8n, "/credentials")

    def create(self, c: dict) -> str:
        body = {"name": c["name"], "type": c["type"], "data": c["data"]}
        new_id = None
        if self.id_writable:
            status, out = self.n8n.request("POST", "/credentials", {"id": c["ref"], **body})
            if status == 400 and "read-only" in readable(out):  # this build assigns ids itself
                self.id_writable = False
            elif status >= 400:
                raise SetupError(self.n8n.scrub(f'create credential {c["name"]}: HTTP {status}: {readable(out)}'))
            else:
                new_id = out["id"]
        if new_id is None:
            new_id = self.n8n.api("POST", "/credentials", body)["id"]
        try:
            self.ledger.add(new_id, c["name"])
        except (SetupError, *NETWORK_ERRORS):
            # Not on record, so no later run could find it: take it back before stopping.
            self.n8n.request("DELETE", f"/credentials/{new_id}")
            raise
        return new_id

    def update(self, cred_id: str, c: dict) -> bool:
        """False where the instance has no update route."""
        if not self.can_patch:
            return False
        status, out = self.n8n.request("PATCH", f"/credentials/{cred_id}", {"name": c["name"], "type": c["type"], "data": c["data"]})
        if status == 405:
            self.can_patch = False
            return False
        if status >= 400:
            raise SetupError(self.n8n.scrub(f'update credential {c["name"]}: HTTP {status}: {readable(out)}'))
        return True

    def run(self, wanted: list[dict]) -> tuple[dict, list[str]]:
        """Returns (id in the workflow files -> id on the instance, report lines)."""
        existing, in_use = self.listed(), credential_refs_in_use(self.n8n)
        ids, report = {}, []
        for c in wanted:
            used = in_use.get(c["name"], [])
            if existing is not None:
                by_id = {e["id"]: e for e in existing}
                match = (by_id.get(c["ref"]) or next((by_id[i] for i in used if i in by_id), None)
                         or next((e for e in existing if e["name"] == c["name"] and e["type"] == c["type"]), None))
                if match and match["type"] != c["type"]:
                    raise SetupError(f'credential {match["id"]} ("{match["name"]}") has type {match["type"]}, expected {c["type"]}')
                current = match["id"] if match else None
            else:
                current = used[0] if used else None
            if current and c.get("placeholder"):
                cred_id, action = current, "kept (placeholder)"
            elif current and self.update(current, c):
                cred_id, action = current, "updated"
            else:
                cred_id = self.create(c)
                action = "replaced" if current else "created"
            self.superseded += [i for i in [current, *used] if i and i != cred_id]
            ids[c["ref"]] = cred_id
            report.append(f'credential  {c["name"]:<16} {cred_id:<17} {action}')
        return ids, report

    def stale(self, ids: dict) -> list[str]:
        """Earlier copies: credentials this script created, or the workflows used before this run, that the
        workflows no longer use."""
        in_use = set(ids.values())
        return [i for i in dict.fromkeys([*self.ledger.ids, *self.superseded]) if i not in in_use]


def prepared(wf: dict, cred_ids: dict, error_workflow: str | None) -> dict:
    nodes = json.loads(json.dumps(wf["nodes"]))
    for node in nodes:
        for ref in (node.get("credentials") or {}).values():
            if ref.get("id") not in cred_ids:
                raise SetupError(f'{wf["name"]}: node "{node["name"]}" uses unknown credential {ref.get("id")}')
            ref["id"] = cred_ids[ref["id"]]
    settings = {k: v for k, v in (wf.get("settings") or {}).items() if k != "errorWorkflow"}
    if error_workflow:
        settings["errorWorkflow"] = error_workflow
    return {"name": wf["name"], "nodes": nodes, "connections": wf["connections"], "settings": settings}


def activate(n8n: N8n, wid: str, name: str) -> str:
    """Publishes a workflow, or activates it where the publish route is missing (an instance answering 404),
    then reads it back until n8n shows it active: on 0.1 CPU activation can outlast the request and still finish.
    Returns the route used."""
    route = "published"
    try:
        status, out = n8n.request("POST", f"/workflows/{wid}/publish", timeout=ACTIVATE_REQUEST_SECONDS)
        if status in (404, 405):
            route = "activated"
            status, out = n8n.request("POST", f"/workflows/{wid}/activate", timeout=ACTIVATE_REQUEST_SECONDS)
        if status >= 400 and status not in (502, 503, 504):  # a gateway timeout can still finish on the server
            raise SetupError(n8n.scrub(f"{route[:-1]} {name}: HTTP {status}: {readable(out)}"))
    except NETWORK_ERRORS:
        pass  # read back below
    deadline = time.monotonic() + ACTIVATE_SECONDS
    while True:
        try:
            status, out = n8n.request("GET", f"/workflows/{wid}")
            if status == 200 and isinstance(out, dict) and out.get("active"):
                return route
        except NETWORK_ERRORS:
            pass
        if time.monotonic() >= deadline:
            raise SetupError(f"{name} is not active {ACTIVATE_SECONDS} s after it was {route}: check the n8n "
                             "executions list and the Render logs, then run setup-n8n again")
        time.sleep(POLL_SECONDS)


def setup_workflows(n8n: N8n, cred_ids: dict) -> tuple[dict, list[str]]:
    """Returns (file name -> workflow id, report lines)."""
    ids, report, error_id = {}, [], None
    for file in ORDER:
        wf = json.loads((WORKFLOWS / file).read_text(encoding="utf-8"))
        body = prepared(wf, cred_ids, None if file == ERRORS_FILE else error_id)
        found = find_workflow(n8n, wf["name"])
        if found:
            wid, action = found["id"], "updated"
            n8n.api("PUT", f"/workflows/{wid}", body)
        else:
            wid, action = n8n.api("POST", "/workflows", body)["id"], "created"
        route = activate(n8n, wid, wf["name"])
        if file == ERRORS_FILE:
            error_id = wid
        ids[file] = wid
        note = ", error workflow C" if file != ERRORS_FILE else ""
        report.append(f'workflow    {wf["name"]:<36} {wid:<17} {action}, {route}{note}')
    return ids, report


def delete_stale(n8n: N8n, ledger: Ledger, stale: list[str]) -> list[str]:
    report = []
    for cred_id in stale:
        status, out = n8n.request("DELETE", f"/credentials/{cred_id}")
        if status < 400 or status == 404:
            ledger.ids.pop(cred_id, None)
            report.append(f"credential  {cred_id:<17} {'deleted (earlier copy)' if status < 400 else 'already gone'}")
        else:
            report.append(f"credential  {cred_id:<17} left in place, deleted on the next run (HTTP {status}: {readable(out)})")
    return report


def health(n8n: N8n, log=print) -> list[str]:
    """Checks that cause no side effects. Webhooks can take a few seconds to register after publishing."""
    checks = []
    status, _ = n8n.request("GET", "/healthz", api=False)
    checks.append(("n8n /healthz", status == 200, f"HTTP {status}"))
    honeypot = {"name": "Setup check", "email": "setup-check@example.com", "message": "setup check", "company": "setup-check"}
    for name, path, kwargs, want in (
        ("form webhook (honeypot, stores nothing)", "/webhook/lead-intake", {"form": honeypot}, 200),
        ("follow-up webhook rejects a missing token", "/webhook/lead-follow-up", {"body": {}}, 403),
    ):
        for _ in range(12):
            status, out = n8n.request("POST", path, api=False, **kwargs)
            if status != 404:
                break
            time.sleep(POLL_SECONDS / 2)
        ok = status == want and (want != 200 or (isinstance(out, dict) and out.get("ok") is True))
        checks.append((name, ok, f"HTTP {status}"))
    lines = [f'health      {"ok  " if ok else "FAIL"} {name} ({detail})' for name, ok, detail in checks]
    if not all(ok for _, ok, _ in checks):
        for line in lines:
            log(line)
        raise SetupError("health check failed. A form webhook error usually means n8n cannot read its settings: check "
                         "N8N_BLOCK_ENV_ACCESS_IN_NODE=false and the LEADS_* values on Render, and run the n8n-errors "
                         "workflow for the failing node")
    return lines


def run(env: dict, log=print) -> list[str]:
    required = SECRETS if env.get("N8N_URL") else ["N8N_HOST", *SECRETS]
    missing = [k for k in required if not env.get(k, "").strip()]
    if missing:
        raise SystemExit(f"missing settings: {', '.join(missing)}")
    clean = {k: env[k].strip() for k in SECRETS}
    host = env.get("N8N_HOST", "").strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    base = env.get("N8N_URL") or f"https://{host}"
    n8n = N8n(base, clean["N8N_API_KEY"], list(clean.values()))

    waited = check_host(n8n, host or base, log)
    lines = [f"n8n         {base} answered after {waited} s, API key accepted"]
    ledger = Ledger(n8n)
    creds = CredentialSetup(n8n, ledger)
    cred_ids, report = creds.run(credentials(clean))
    lines += report
    _, report = setup_workflows(n8n, cred_ids)
    lines += report
    lines += delete_stale(n8n, ledger, creds.stale(cred_ids))
    ledger.save()
    lines.append(f'ledger      {LEDGER_NAME:<36} {ledger.workflow["id"]:<17} {len(ledger.ids)} credential(s) on record')
    lines += health(n8n, log)
    return lines


def main() -> int:
    try:
        lines = run(dict(os.environ))
    except SystemExit as e:
        print(f"::error::{e}")
        return 2
    except SetupError as e:
        print(f"::error::setup stopped: {e}")
        return 1
    for line in lines:
        print(line)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as f:
            f.write("### n8n setup\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
