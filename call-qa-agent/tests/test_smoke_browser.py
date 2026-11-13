"""Browser smoke test: the call list and the tabs on a call page, in Chromium, under the page's own security policy.

    pip install playwright && playwright install chromium
    pytest tests/test_smoke_browser.py

The app runs in this process on a free port with the tests' fake database, in demo mode. The test fails on any
script error or blocked resource, so it also catches a page that only works with JavaScript off. Skipped when
Playwright or its browser is not installed, except in CI, where it must run.
"""

import os
import socket
import threading
import time

import pytest
import uvicorn

from app.config import Settings, get_settings
from app.main import app, get_db
from tests.test_dashboard import FakeDB, call

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
    ignored = lambda url: any(part in url for part in IGNORED)  # noqa: E731
    page.on("pageerror", lambda e: problems.append(f"script error: {e}"))
    page.on("console", lambda m: problems.append(f"console: {m.text}")
            if m.type == "error" and not ignored(m.location.get("url", "") + " " + m.text) else None)
    page.on("requestfailed", lambda r: None if ignored(r.url) else problems.append(f"request failed: {r.url}"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 and not ignored(r.url) else None)


@pytest.fixture
def site():
    calls = [call(title="US insurance sales: pushy pitch", is_demo=True), call(id="c2", title="Utility billing dispute", is_demo=True)]
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, app_env="test", app_passcode="x" * 24, demo_mode=True)
    app.dependency_overrides[get_db] = lambda: FakeDB(calls)
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
    app.dependency_overrides.clear()


def test_call_list_and_tabs(site):
    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        problems = []
        watch(page, problems)

        page.goto(f"{site}/dashboard")
        assert page.locator("a.call-card").count() == 2
        page.locator("a.call-card", has_text="pushy pitch").click()
        page.wait_for_url("**/dashboard/calls/c1")

        tabs = page.get_by_role("tab")
        assert tabs.all_inner_texts() == ["Summary", "Transcript", "Checks"]
        assert page.get_by_role("tab", name="Summary").get_attribute("aria-selected") == "true"
        assert page.locator("#summary").is_visible() and not page.locator("#transcript").is_visible()

        page.get_by_role("tab", name="Transcript").click()
        assert page.get_by_role("tab", name="Transcript").get_attribute("aria-selected") == "true"
        assert page.locator("#transcript").is_visible() and not page.locator("#summary").is_visible()
        assert "guaranteed approval" in page.locator("#transcript").inner_text()
        assert page.locator("#transcript script").count() == 0  # the transcript's <script> text stayed text

        page.keyboard.press("ArrowRight")  # the tabs follow the arrow keys
        assert page.get_by_role("tab", name="Checks").get_attribute("aria-selected") == "true"
        assert page.locator("#checks").is_visible()

        toggle = page.locator("[data-du-theme]")
        before = page.evaluate("document.documentElement.dataset.theme || ''")
        toggle.click()
        assert page.evaluate("document.documentElement.dataset.theme") in ("dark", "light") and \
            page.evaluate("document.documentElement.dataset.theme") != before

        browser.close()
    assert problems == []
