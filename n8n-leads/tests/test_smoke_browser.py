"""Browser smoke test: the demo form and its result timeline, in Chromium, under the page's own security policy.

    pip install playwright && playwright install chromium
    python3 -m unittest discover -s tests -p 'test_smoke_browser.py' -v

Serves landing/public with the headers from landing/vercel.json and a stand-in for the n8n webhook that answers
like Workflow A in demo mode, so no n8n is needed. The policy is the deployed one with the webhook's address in
place of *.onrender.com. Fails on any script error or blocked resource. Skipped when Playwright or its
browser is not installed, except in CI, where it must run.
"""

import functools
import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

LANDING = Path(__file__).resolve().parents[1] / "landing"
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("CI"):
        raise
    sync_playwright = None

# Vercel Web Analytics exists only on the deployed site. The wake-up request to /healthz is sent no-cors, only
# to reach the server: the browser keeps its JSON answer from the page and reports that as a failed request.
IGNORED = ("/_vercel/insights/", "/healthz")
ALERT = "<b>[DEMO]</b> Public demo form, made-up details.\n<b>Hot lead: rent: Sara Ahmed</b>\nCall: +971500000101"
ANSWER = {"ok": True, "demo": {
    "category": "hot", "intent": "rent", "reason": "Names a budget and wants to move next month.",
    "summary": "Wants a 2 bedroom in Dubai Marina, 120k, next month.", "duplicate": False, "alert": "sent", "alert_message": ALERT,
    "email": {"to": "sara@example.com", "subject": "Thanks for contacting Palm Grove Realty", "text": "Hi Sara,\n\nThank you for your message.",
              "language": "en", "dir": "ltr"}, "no_email": ""}}


class Webhook(http.server.BaseHTTPRequestHandler):
    """Answers the form post like the workflow in demo mode, and /healthz for the page's wake-up request."""
    received: list = []
    starting = 0  # this many posts get the answer of an n8n that is still starting

    def log_message(self, *args):
        pass

    def reply(self, status: int, body: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200, b'{"status":"ok"}')

    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["content-length"])).decode())
        self.received.append({k: v[0] for k, v in form.items()})
        if Webhook.starting:
            # n8n 2.45 opens its port before its routes exist; Express then answers 404 without a CORS header.
            Webhook.starting -= 1
            body = b"<!DOCTYPE html><html><head><title>Error</title></head><body><pre>Cannot POST /webhook/lead-intake</pre></body></html>"
            self.send_response(404)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.reply(200, json.dumps(ANSWER).encode())


class Site(http.server.SimpleHTTPRequestHandler):
    headers_for_all: dict = {}

    def log_message(self, *args):
        pass

    def end_headers(self):
        for key, value in self.headers_for_all.items():
            self.send_header(key, value)
        super().end_headers()


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class DemoPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook, hook_url = serve(Webhook)
        cls.folder = Path(tempfile.mkdtemp(prefix="lead-page-"))
        shutil.copytree(LANDING / "public", cls.folder / "site")
        (cls.folder / "site" / "config.js").write_text(f'window.LEAD_WEBHOOK_URL = "{hook_url}/webhook/lead-intake";\n')
        rules = json.loads((LANDING / "vercel.json").read_text(encoding="utf-8"))["headers"]
        headers = {h["key"]: h["value"] for h in rules[0]["headers"]}
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("https://*.onrender.com", hook_url)
        Site.headers_for_all = headers
        cls.site, cls.url = serve(functools.partial(Site, directory=str(cls.folder / "site")))

    def setUp(self):
        Webhook.starting = 0

    @classmethod
    def tearDownClass(cls):
        for server in (cls.hook, cls.site):
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_an_example_enquiry_shows_the_timeline(self):
        problems = []
        ignored = lambda text: any(part in text for part in IGNORED)  # noqa: E731
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # noqa: BLE001 - Playwright's own error type; only a missing browser is skipped
                if os.environ.get("CI") or "playwright install" not in str(e):
                    raise
                self.skipTest("Chromium is not installed: run `playwright install chromium`")
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
            page.on("console", lambda m: problems.append(f"console: {m.text}")
                    if m.type == "error" and not ignored(m.location.get("url", "") + " " + m.text) else None)
            page.on("requestfailed", lambda r: None if ignored(r.url) else problems.append(f"request failed: {r.url}"))
            page.on("response", lambda r: problems.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 and not ignored(r.url) else None)

            page.goto(self.url)
            self.assertFalse(page.locator("#result").is_visible())
            page.locator("[data-example]").first.click()
            self.assertTrue(page.input_value("[name=name]"))
            page.click("button[type=submit]")
            page.wait_for_selector("#result:not([hidden])")

            steps = page.locator("#result .timeline .step")
            self.assertEqual(steps.locator("h3").all_inner_texts(), ["Sorted", "Repeat check", "Sales alert", "Reply email"])
            self.assertEqual([steps.nth(i).get_attribute("data-state") for i in range(4)], ["done"] * 4)
            self.assertEqual(page.inner_text("#r-category"), "Hot")
            self.assertIn("Names a budget", page.inner_text("#r-reason"))
            # The Telegram text is shown as text: bold where the workflow sent <b>, nothing run as HTML.
            self.assertEqual(page.locator("#r-tg-text strong").all_inner_texts(), ["[DEMO]", "Hot lead: rent: Sara Ahmed"])
            self.assertEqual(page.locator("#r-tg-text b, #r-tg-text script").count(), 0)
            self.assertEqual(page.inner_text("#r-to"), "sara@example.com")

            sent = Webhook.received[-1]
            self.assertEqual((sent["source"], sent["consent"]), ("web_form", "yes"))
            self.assertTrue(sent["message"])

            page.click("#again")
            self.assertFalse(page.locator("#result").is_visible())
            self.assertEqual(page.input_value("[name=name]"), "")
            browser.close()
        self.assertEqual(problems, [])

    def test_a_server_that_is_still_starting_is_waited_for(self):
        """The browser cannot read n8n's start-up 404 (no CORS header) and rejects the request: the page must
        retry it, not show the error state."""
        errors = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:  # noqa: BLE001
                if os.environ.get("CI") or "playwright install" not in str(e):
                    raise
                self.skipTest("Chromium is not installed: run `playwright install chromium`")
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: errors.append(f"script error: {e}"))
            page.goto(self.url)
            before = len(Webhook.received)
            Webhook.starting = 2
            page.locator("[data-example]").first.click()
            page.click("button[type=submit]")
            page.wait_for_selector("#result:not([hidden])", timeout=30000)
            self.assertEqual(len(Webhook.received) - before, 3)  # two refused while starting, then answered
            self.assertEqual(page.inner_text("#r-category"), "Hot")
            browser.close()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
