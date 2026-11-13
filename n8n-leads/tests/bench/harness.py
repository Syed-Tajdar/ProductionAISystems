"""Test bench for the lead workflows. Standard library only.

    python3 tests/bench/harness.py up      # start n8n, load ../../workflows/*.json
    python3 tests/bench/harness.py down    # stop and delete the test data

Workflows are loaded with the Supabase, Claude and Gemini hosts rewritten to the mock server
(mock_api.py), which the tests run in their own process. Each workflow that has an
llm_provider setting is also loaded as a Gemini copy on webhook paths ending in -gemini, and
each one with a demo_mode setting as a demo copy on paths ending in -demo. Workflow B's follow-up is also loaded
with an empty sender (LEADS_EMAIL_FROM) on /webhook/lead-follow-up-nosender.
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
WORKFLOWS = HERE.parents[1] / "workflows"
N8N_URL = os.environ.get("N8N_TEST_URL", "http://localhost:5680")
MOCK_FROM_N8N = "http://host.docker.internal:8091"

REWRITES = {  # the Supabase URL and chat ids come from LEADS_* variables in docker-compose.yml
    "https://api.anthropic.com": f"{MOCK_FROM_N8N}/anthropic",
    "https://generativelanguage.googleapis.com": f"{MOCK_FROM_N8N}/gemini",
}
# setting in the workflow -> (value in the copy, webhook path suffix, name suffix, only these files or None)
VARIANTS = {
    "llm_provider: 'anthropic'": ("llm_provider: 'gemini'", "gemini", "Gem", None),
    "demo_mode: env('LEADS_DEMO_MODE') === 'true'": ("demo_mode: true", "demo", "Demo", None),
    "email_from: env('LEADS_EMAIL_FROM')": ("email_from: ''", "nosender", "NoMail", {"lead-follow-up.json"}),
}


def compose(*args: str) -> str:
    return subprocess.run(["docker", "compose", *args], cwd=HERE, check=True, text=True, stdout=subprocess.PIPE).stdout


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


def test_copy(wf: dict) -> dict:
    text = json.dumps(wf)
    for real, mock in REWRITES.items():
        text = text.replace(real, mock)
    return json.loads(text)


def variant(wf: dict, setting: str) -> dict:
    value, suffix, tag, _ = VARIANTS[setting]
    copy = json.loads(json.dumps(wf).replace(setting, value))
    copy["id"], copy["name"] = wf["id"] + tag, f"{wf['name']} ({suffix})"
    for node in copy["nodes"]:
        if node["type"] == "n8n-nodes-base.webhook":
            node["parameters"]["path"] += f"-{suffix}"
            node["webhookId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, node["webhookId"] + f"-{suffix}"))
    return copy


def up() -> None:
    compose("up", "-d", "--wait")
    wait_healthy()
    compose("cp", "credentials.json", "n8n:/tmp/credentials.json")
    n8n_cli("import:credentials", "--input=/tmp/credentials.json")
    tmp = HERE / ".tmp"
    tmp.mkdir(exist_ok=True)
    for path in sorted(WORKFLOWS.glob("*.json")):
        wf = test_copy(json.loads(path.read_text(encoding="utf-8")))
        copies = [wf] + [variant(wf, k) for k, v in VARIANTS.items()
                         if k in json.dumps(wf) and (v[3] is None or path.name in v[3])]
        for copy in copies:
            out = tmp / f"{copy['id']}.json"
            out.write_text(json.dumps(copy), encoding="utf-8")
            compose("cp", str(out), f"n8n:/tmp/{out.name}")
            n8n_cli("import:workflow", f"--input=/tmp/{out.name}")
            try:
                n8n_cli("publish:workflow", f"--id={copy['id']}", attempts=1)
            except RuntimeError:
                print(f"  {copy['name']} not published (no trigger to activate)")
            print(f"loaded {copy['name']} ({copy['id']})")
    compose("restart", "n8n")
    wait_healthy()


def down() -> None:
    compose("down", "-v")


def post(path: str, data: dict, *, form: bool = True, retries: int = 30, headers: dict | None = None):
    """POST to a production webhook, form-encoded like the landing page. Returns (status, json)."""
    body = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
    ctype = "application/x-www-form-urlencoded" if form else "application/json"
    for _ in range(retries):
        req = urllib.request.Request(f"{N8N_URL}/webhook/{path}", data=body, method="POST",
                                     headers={"content-type": ctype, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read().decode()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            if e.code == 404 and ("not registered" in raw or "Cannot POST" in raw):  # n8n still starting
                time.sleep(2)
                continue
            return e.code, json.loads(raw) if raw.startswith("{") else raw
    raise TimeoutError(f"webhook {path} not registered")


if __name__ == "__main__":
    {"up": up, "down": down}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
