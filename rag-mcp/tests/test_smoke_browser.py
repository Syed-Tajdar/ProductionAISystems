"""Browser smoke test: the question thread, in Chromium, under the page's own security policy.

    pip install playwright && playwright install chromium
    pytest tests/test_smoke_browser.py

The app runs in this process on a free port with a stubbed answer service, so no database or keys are needed.
The test fails on any script error or blocked resource. Skipped when Playwright or its browser is not installed, except in CI,
where it must run.
"""

import os
import socket
import threading
import time

import pytest
import uvicorn

from rag.api import create_app
from rag.config import Settings
from rag.demo import AnswerCache, DemoLimiter

from .test_page_ui import CITED, Stub

if os.environ.get("CI"):
    from playwright.sync_api import sync_playwright
else:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

# Vercel Web Analytics exists only on the deployed site.
IGNORED = ("/_vercel/insights/",)


def launch(playwright):
    """Chromium, or a skipped test when Playwright is installed without its browser (outside CI)."""
    try:
        return playwright.chromium.launch()
    except Exception as e:  # noqa: BLE001 - Playwright's own error type; anything but a missing browser is raised again
        if os.environ.get("CI") or "playwright install" not in str(e):
            raise
        pytest.skip("Chromium is not installed: run `playwright install chromium`")


def watch(page, problems: list) -> None:
    """Collects what must not happen on a page: script errors, console errors (a blocked script or style shows
    up there), failed requests and error responses."""
    ignored = lambda text: any(part in text for part in IGNORED)  # noqa: E731
    page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
    page.on("console", lambda m: problems.append(f"console: {m.text}")
            if m.type == "error" and not ignored(m.location.get("url", "") + " " + m.text) else None)
    page.on("requestfailed", lambda r: None if ignored(r.url) else problems.append(f"request failed: {r.url}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 and not ignored(r.url) else None)


@pytest.fixture
def site():
    app = create_app(Settings(_env_file=None), service=Stub(CITED), limiter=DemoLimiter(per_client=20, daily_cap=100), cache=AnswerCache())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_questions_build_a_thread(site):
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        problems = []
        watch(page, problems)

        page.goto(site)
        assert page.locator("#thread .turn").count() == 0
        page.fill("#q", "What does a returned cheque cost?")
        page.click("form.ask button[type=submit]")
        page.wait_for_selector("#thread .turn .answer")
        assert page.url.rstrip("/") == site  # answered in place, without loading a new page
        first = page.locator("#thread .turn").first
        assert "What does a returned cheque cost?" in first.locator(".ask-bubble").inner_text()
        assert "A returned cheque costs AED 500." in first.locator(".answer").inner_text()
        assert page.input_value("#q") == ""

        page.fill("#q", "And how soon must it be replaced?")
        page.keyboard.press("Enter")
        page.locator("#thread .turn .answer").nth(1).wait_for()  # a locator wait: the page's policy forbids eval
        turns = page.locator("#thread .turn")
        assert turns.count() == 2 and "And how soon" in turns.nth(1).locator(".ask-bubble").inner_text()
        ids = page.evaluate("[...document.querySelectorAll('#thread [id]')].map((e) => e.id)")
        assert len(ids) == len(set(ids))  # each question and answer pair keeps its own ids

        # A citation opens the cited passage of its own answer, with the quote marked.
        turns.nth(1).locator("a.cite").first.click()
        sheet = turns.nth(1).locator("dialog.sheet")
        assert sheet.evaluate("d => d.open") and "A returned cheque costs AED 500" in sheet.locator("mark").first.inner_text()

        browser.close()
    assert problems == []
