"""Uptime check for the portfolio and the demos: each page must return real content, not just a 200.

The Call QA list and the RAG page read from the database (the demo calls; the day's question count and the answer
cache), so these checks also keep the Supabase free project from pausing for inactivity. The RAG example question
is one the warm-rag-cache workflow keeps in the answer cache, so asking it costs nothing and uses no visitor limit.

The lead demo is checked only as its static page on Vercel (with LEADS_DEMO_URL set), never n8n itself: n8n sleeps
on Render's free plan when idle, and that is expected, not an outage.

    python .github/scripts/uptime.py            # prints one line per check, writes failures to $UPTIME_FAILURES
"""

import http.client
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PORTFOLIO = os.environ.get("PORTFOLIO_URL", "https://syed-tajdar.vercel.app").rstrip("/")
CALL_QA = os.environ.get("CALL_QA_URL", "https://syed-call-qa.vercel.app").rstrip("/")
RAG = os.environ.get("RAG_URL", "https://syed-rag.vercel.app").rstrip("/")
LEADS = os.environ.get("LEADS_DEMO_URL", "").strip().rstrip("/")
EXAMPLE = "What is the fee for a returned cheque?"  # the first example chip (rag-mcp/rag/demo.py EXAMPLES)
HEADERS = {"User-Agent": "portfolio-uptime-check"}


def fetch(url: str, data: dict | None = None, timeout: int = 60) -> tuple[int, str]:
    body = urllib.parse.urlencode(data).encode() if data else None
    request = urllib.request.Request(url, data=body, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# name, url, form data (POST) or None, text that must be on the page
CHECKS = [
    ("Portfolio home page", PORTFOLIO + "/", None, ["AI Automation Engineer", "What I can automate for your team"]),
    ("Call QA call list (database)", CALL_QA + "/dashboard", None, ['class="call-card"']),
    ("RAG question page (database)", RAG + "/", None, ["Ask the company documents", 'role="meter"']),
    ("RAG cached example answer (database)", RAG + "/", {"q": EXAMPLE}, ['class="statement"', 'class="source"']),
] + ([
    ("Lead demo page", LEADS + "/", None, ["Send a test enquiry", 'data-du-card="leads"']),
    ("Lead demo webhook address", LEADS + "/config.js", None, ["/webhook/lead-intake"]),
] if LEADS else [])


def run() -> list[str]:
    failures = []
    for name, url, data, needles in CHECKS:
        for _ in range(2):  # one retry: a cold serverless start can time out once
            started = time.monotonic()
            try:
                status, text = fetch(url, data)
                missing = [n for n in needles if n not in text]
                problem = f"HTTP {status}" if status != 200 else (f"missing {', '.join(missing)}" if missing else "")
            except (OSError, http.client.HTTPException) as e:  # no connection, timeout, or a broken answer
                problem = f"{type(e).__name__}: {e}"
            ms = round((time.monotonic() - started) * 1000)
            if not problem:
                break
            time.sleep(5)
        print(f"{'ok  ' if not problem else 'FAIL'} {name} ({ms} ms){': ' + problem if problem else ''}")
        if problem:
            failures.append(f"{name}: {problem}")
    return failures


if __name__ == "__main__":
    found = run()
    if path := os.environ.get("UPTIME_FAILURES"):
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(found))
    sys.exit(1 if found else 0)
