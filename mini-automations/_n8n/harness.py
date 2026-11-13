"""Test bench for the n8n mini automations. Standard library only.

    python3 _n8n/harness.py up      # start n8n + mock, load every */workflow.json
    python3 _n8n/harness.py down    # stop and delete the test data

Workflows are loaded with external API hosts rewritten to the mock server, so
both dry runs and "live" runs work without real keys. Tests import this module
for post() and the mock helpers.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
N8N_URL = os.environ.get("N8N_TEST_URL", "http://localhost:5679")
MOCK_URL = os.environ.get("MOCK_URL", "http://localhost:8090")

# Production hosts -> mock routes (as seen from inside the n8n container).
REWRITES = {
    "https://api.anthropic.com": "http://mock:8090/anthropic",
    "https://graph.facebook.com": "http://mock:8090/graph",
    "https://api.hubapi.com": "http://mock:8090/hubspot",
    "https://sheets.googleapis.com": "http://mock:8090/sheets",
    "https://generativelanguage.googleapis.com": "http://mock:8090/gemini",
}
PROVIDER_SETTING = "llm_provider: 'anthropic'"
# Google OAuth cannot be mocked; test copies use a dummy header credential instead.
OAUTH_CREDENTIAL_TYPES = {"googleSheetsOAuth2Api"}
SUPABASE_PLACEHOLDER = "https://YOUR-PROJECT.supabase.co"
# Trigger nodes that poll real services are removed for tests.
SKIP_NODE_TYPES = {"n8n-nodes-base.gmailTrigger"}


def compose(*args: str, capture: bool = False) -> str:
    out = subprocess.run(
        ["docker", "compose", *args], cwd=HERE, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return out.stdout or ""


def n8n_cli(*args: str, attempts: int = 4) -> None:
    """Runs an n8n CLI command in the container. The CLI opens the same database as the running n8n, so right
    after a start it can fail once while n8n still holds it: retried with a pause, and the last error is shown."""
    for attempt in range(1, attempts + 1):
        done = subprocess.run(["docker", "compose", "exec", "-T", "n8n", "n8n", *args], cwd=HERE, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if done.returncode == 0:
            return
        if attempt < attempts:
            print(f"  n8n {args[0]} failed (attempt {attempt} of {attempts}), retrying: {done.stdout.strip()[-300:]}")
            time.sleep(3 * attempt)
    raise RuntimeError(f"n8n {' '.join(args)} failed {attempts} times:\n{done.stdout[-2000:]}")


def wait_healthy(timeout: int = 120) -> None:
    """Waits for /healthz/readiness: n8n answers it only once its database is connected and migrated.
    /healthz alone turns 200 as soon as the port is open, before the CLI can import anything."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{N8N_URL}/healthz/readiness", timeout=3) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(2)
    raise TimeoutError("n8n did not become healthy")


def test_copy(workflow: dict) -> dict:
    text = json.dumps(workflow)
    for real, mock in REWRITES.items():
        text = text.replace(real, mock)
    text = text.replace(SUPABASE_PLACEHOLDER, "http://mock:8090/supabase")
    wf = json.loads(text)
    keep = [n for n in wf["nodes"] if n["type"] not in SKIP_NODE_TYPES]
    names = {n["name"] for n in keep}
    for node in keep:
        params = node.get("parameters", {})
        if params.get("nodeCredentialType") in OAUTH_CREDENTIAL_TYPES:
            params.update(authentication="genericCredentialType", genericAuthType="httpHeaderAuth")
            params.pop("nodeCredentialType")
            node["credentials"] = {"httpHeaderAuth": {"id": "mnGoogleTestTok01", "name": "Google test token"}}
    wf["nodes"] = keep
    wf["connections"] = {k: v for k, v in wf["connections"].items() if k in names}
    return wf


def gemini_copy(wf: dict) -> dict:
    """Same workflow with llm_provider set to gemini, on webhook paths ending in -gemini."""
    copy = json.loads(json.dumps(wf).replace(PROVIDER_SETTING, "llm_provider: 'gemini'"))
    copy["id"] = wf["id"] + "Gem"
    copy["name"] = wf["name"] + " (gemini)"
    for node in copy["nodes"]:
        if node["type"] == "n8n-nodes-base.webhook":
            node["parameters"]["path"] += "-gemini"
            node["webhookId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, node["webhookId"] + "-gemini"))
    return copy


def up() -> None:
    compose("up", "-d", "--wait")
    wait_healthy()
    compose("cp", "credentials.json", "n8n:/tmp/credentials.json")
    n8n_cli("import:credentials", "--input=/tmp/credentials.json")
    tmp = HERE / ".tmp"
    tmp.mkdir(exist_ok=True)
    for path in sorted(ROOT.glob("*/workflow.json")):
        wf = test_copy(json.loads(path.read_text(encoding="utf-8")))
        copies = [(path.parent.name, wf)]
        if PROVIDER_SETTING in json.dumps(wf):
            copies.append((f"{path.parent.name}-gemini", gemini_copy(wf)))
        for name, copy in copies:
            out = tmp / f"{name}.json"
            out.write_text(json.dumps(copy), encoding="utf-8")
            compose("cp", str(out), f"n8n:/tmp/{out.name}")
            n8n_cli("import:workflow", f"--input=/tmp/{out.name}")
            n8n_cli("publish:workflow", f"--id={copy['id']}")
            print(f"loaded {name} ({copy['id']})")
    compose("restart", "n8n")
    wait_healthy()


def down() -> None:
    compose("down", "-v")


def _request(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw else None)


def post(path: str, payload: dict, query: dict | None = None, retries: int = 30, headers: dict | None = None):
    """POST JSON to a production webhook. Retries while n8n registers webhooks."""
    url = f"{N8N_URL}/webhook/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    body = json.dumps(payload).encode()
    for _ in range(retries):
        try:
            return _request(url, "POST", body, {"content-type": "application/json", **(headers or {})})
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            time.sleep(2)
    raise TimeoutError(f"webhook {path} not registered")


def get(path: str, query: dict | None = None):
    url = f"{N8N_URL}/webhook/{path}" + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def mock_reset() -> None:
    _request(f"{MOCK_URL}/__mock/reset", "POST", b"{}")


def mock_set(prefix: str, body, status: int = 200, method: str | None = None) -> None:
    rule = {"prefix": prefix, "body": body, "status": status, "method": method}
    _request(f"{MOCK_URL}/__mock/set", "POST", json.dumps(rule).encode(), {"content-type": "application/json"})


def mock_requests(prefix: str = "") -> list[dict]:
    _, reqs = _request(f"{MOCK_URL}/__mock/requests")
    return [r for r in reqs if r["path"].startswith(prefix)]


def claude_reply(data: dict) -> dict:
    """A Messages API response whose text block is the given JSON."""
    return {
        "type": "message",
        "stop_reason": "end_turn",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(data)}],
    }


def gemini_reply(data: dict) -> dict:
    """A generateContent response whose text part is the given JSON."""
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(data)}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
    }


GEMINI_429 = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Resource has been exhausted (e.g. check quota)."}}


if __name__ == "__main__":
    {"up": up, "down": down}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
