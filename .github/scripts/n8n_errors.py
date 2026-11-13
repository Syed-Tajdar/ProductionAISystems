"""Prints where recent n8n executions went wrong: node errors and error-shaped outputs. Read-only.

    N8N_HOST=<your-service>.onrender.com N8N_API_KEY=... LIMIT=3 python .github/scripts/n8n_errors.py

Lead data never reaches the log: personal fields (name, phone, email, message and the texts built from them) are
replaced by a length marker, and emails, phone numbers, names and messages inside any other text, such as an
error message, are masked. Values of secrets in the environment are masked too.
"""

import json
import os
import re
import sys
import urllib.parse
import urllib.request

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"\+\d[\d\s().-]{6,}\d|(?<![\w.])\d{9,15}(?![\w.])|\b0\d{2}[\s-]\d{3}[\s-]\d{4}\b")
# Lines the workflows build from a lead: "Name: ...", "Message: ...", the alert title and the email greeting.
LINES = [
    (re.compile(r"(\bName:\s*)[^\n<|]{1,80}"), r"\1<name>"),
    (re.compile(r"(\bMessage:\s*)[^\n<]{1,400}"), r"\1<message>"),
    (re.compile(r"((?:Hot lead|New lead)[^\n<]*:\s)[^\n<:]{1,80}"), r"\1<name>"),
    (re.compile(r"(\bHi\s+)[^\n,]{1,40},"), r"\1<name>,"),
    (re.compile(r"(مرحباً\s+)[^\n،]{1,40}،"), r"\1<name>،"),
]
# Fields that hold what a lead typed, or text built from it. Their values are never printed.
PERSONAL = {"name", "first_name", "last_name", "full_name", "phone", "email", "email_to", "to", "message", "summary", "reason",
            "text", "html", "subject", "alert_text", "alert_message", "last_message", "lead_payload", "lead", "body", "query"}
# Inside an error object these say what went wrong, so they stay readable and are masked by pattern instead.
ERROR_TEXT = {"name", "message", "reason"}
ERROR_WORDS = ('"error', "statuscode", "failed", "unauthorized", "not found", "permission")


def mask_text(text: str) -> str:
    """Emails, phone numbers and the lead lines inside free text."""
    text = EMAIL.sub("<email>", text)
    text = PHONE.sub("<phone>", text)
    for pattern, replacement in LINES:
        text = pattern.sub(replacement, text)
    return text


def mask(value, inside_error: bool = False):
    """A copy that is safe to print: personal fields are replaced as a whole, every other text is masked by pattern."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            personal = key.lower() in PERSONAL and not (inside_error and key.lower() in ERROR_TEXT)
            if personal and item not in (None, "", [], {}):
                size = len(item) if isinstance(item, str) else len(json.dumps(item, ensure_ascii=False))
                out[key] = f"<masked, {size} chars>"
            else:
                out[key] = mask(item, inside_error or key == "error")
        return out
    if isinstance(value, list):
        return [mask(item, inside_error) for item in value]
    if isinstance(value, str):
        return mask_text(value)
    return value


def errorish(value) -> bool:
    text = json.dumps(value).lower()
    return any(word in text for word in ERROR_WORDS)


def report(executions: list[dict], scrub=lambda text: text) -> list[str]:
    """One line per execution, then one per failed node or error-shaped output."""
    def show(value, inside_error=False) -> str:
        return scrub(json.dumps(mask(value, inside_error), ensure_ascii=False)[:600])

    lines = []
    for ex in executions:
        lines.append(f"== execution {ex['id']} {ex.get('status')} {ex.get('startedAt')} workflow {ex.get('workflowId')}")
        result = (ex.get("data") or {}).get("resultData") or {}
        if result.get("error"):
            lines.append("  workflow error: " + show(result["error"], True))
        for node, runs in (result.get("runData") or {}).items():
            for run in runs:
                if run.get("error"):
                    lines.append(f"  {node}: ERROR " + show(run["error"], True))
                    continue
                for branch in ((run.get("data") or {}).get("main") or []):
                    for item in branch or []:
                        output = item.get("json", {})
                        if errorish(output):
                            lines.append(f"  {node}: " + show(output))
    return lines


def main() -> int:
    host = os.environ["N8N_HOST"].strip().removeprefix("https://").strip("/")
    key = os.environ["N8N_API_KEY"].strip()
    secrets = [v.strip() for k, v in os.environ.items() if k.endswith(("_KEY", "_TOKEN")) and v.strip()]
    limit = os.environ.get("LIMIT", "3")

    def scrub(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, "***")
        return text

    request = urllib.request.Request(f"https://{host}/api/v1/executions?includeData=true&limit={urllib.parse.quote(limit)}",
                                     headers={"X-N8N-API-KEY": key, "accept": "application/json"})
    with urllib.request.urlopen(request, timeout=240) as response:
        executions = json.loads(response.read()).get("data", [])
    for line in report(executions, scrub):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
