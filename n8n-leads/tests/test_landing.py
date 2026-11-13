"""The static demo page (landing/): built from page.html with the shared demo UI, and its Vercel config step.

    pip install jinja2
    python3 -m unittest discover -s tests -p 'test_landing.py' -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LANDING = Path(__file__).resolve().parents[1] / "landing"
sys.path.insert(0, str(LANDING))
import build  # noqa: E402

NOTE = ("Demo mode: this email is shown here instead of sent, so this public form can't be used to send messages "
        "to someone else's address. In a real setup, this step sends it automatically.")


class Page(unittest.TestCase):
    html = (LANDING / "public" / "index.html").read_text(encoding="utf-8")

    def test_built_page_is_current(self):
        self.assertEqual(build.render(), self.html, "run `python landing/build.py`")

    def test_demo_note_and_card_point_about_email(self):
        self.assertIn(NOTE, self.html)
        card = re.search(r'<section class="du-card".*?</section>', self.html, re.S).group(0)
        self.assertIn("shown on this page instead of sent", card)
        self.assertIn("someone else&#39;s address", card)

    def test_shared_demo_ui_and_no_inline_code(self):
        for part in ('class="du-footer"', 'data-du-card="leads"', 'class="du-state du-limit"', 'class="du-state du-error"'):
            self.assertIn(part, self.html)
        self.assertNotIn("style=", self.html)  # the CSP allows no inline styles
        scripts = re.findall(r"<script([^>]*)>(.*?)</script>", self.html, re.S)
        inline = [body for attrs, body in scripts if body.strip() and "application/json" not in attrs]
        self.assertEqual(inline, [])

    def test_result_is_a_timeline_of_the_reported_steps(self):
        steps = re.findall(r'<li class="step" id="step-(\w+)">\s*<h3>([^<]+)</h3>', self.html)
        self.assertEqual(steps, [("sorted", "Sorted"), ("repeat", "Repeat check"), ("alert", "Sales alert"), ("email", "Reply email")])
        self.assertIn('<figure class="tg" id="r-tg" hidden>', self.html)  # shown only when the alert was sent
        self.assertIn('<button type="button" class="again-button" id="again">Try another example</button>', self.html)
        js = (LANDING / "public" / "landing.js").read_text(encoding="utf-8")
        self.assertIn("d.alert_message", js)
        self.assertNotIn("innerHTML", js)  # response text is only ever added as text

    def test_form_works_without_script(self):
        self.assertIn('<form id="lead-form" class="lead-form" method="post" action="#webhook-not-set"', self.html)
        self.assertNotIn("novalidate", self.html)  # the browser checks the fields when the script does not run
        self.assertIn("<noscript>", self.html)
        csp = json.loads((LANDING / "vercel.json").read_text())["headers"][0]["headers"][0]["value"]
        self.assertIn("form-action https://*.onrender.com", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertLess(self.html.index('src="theme.js"'), self.html.index('href="landing.css"'))

    def test_limits_match_the_workflow_defaults(self):
        wf = (LANDING.parent / "workflows" / "lead-intake.json").read_text(encoding="utf-8")
        for setting, value in (("LEADS_DEMO_PER_VISITOR", build.LIMITS["per_visitor"]),
                               ("LEADS_DEMO_WINDOW_MINUTES", build.LIMITS["window_minutes"]),
                               ("LEADS_DEMO_DAILY_CAP", build.LIMITS["daily_cap"])):
            self.assertIn(f"env('{setting}', '{value}')", wf)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class VercelConfig(unittest.TestCase):
    def run_build(self, url: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(LANDING / "build-config.mjs", tmp)
            (Path(tmp) / "public").mkdir()
            shutil.copy(LANDING / "public" / "index.html", Path(tmp) / "public" / "index.html")
            env = {k: v for k, v in os.environ.items() if k != "LEAD_WEBHOOK_URL"}
            if url is not None:
                env["LEAD_WEBHOOK_URL"] = url
            run = subprocess.run(["node", "build-config.mjs"], cwd=tmp, env=env, capture_output=True, text=True)
            config = Path(tmp, "public", "config.js")
            self.page = Path(tmp, "public", "index.html").read_text(encoding="utf-8")
            return run.returncode, config.read_text() if config.exists() else None

    def test_writes_the_webhook_url(self):
        code, config = self.run_build("https://my-n8n-x1y2.onrender.com/webhook/lead-intake")
        self.assertEqual((code, config), (0, 'window.LEAD_WEBHOOK_URL = "https://my-n8n-x1y2.onrender.com/webhook/lead-intake";\n'))
        # Without script the form posts straight to the same webhook.
        self.assertIn('method="post" action="https://my-n8n-x1y2.onrender.com/webhook/lead-intake"', self.page)

    def test_fails_without_a_valid_url(self):
        for url in (None, "", "http://n8n.example.com/webhook/lead-intake", 'https://x.onrender.com/webhook/a";alert(1)//'):
            self.assertEqual(self.run_build(url), (1, None), url)


if __name__ == "__main__":
    unittest.main()
