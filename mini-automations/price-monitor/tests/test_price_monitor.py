import csv
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import price_monitor as pm  # noqa: E402

SAMPLES = ROOT / "samples"
GEMINI = {"status": 200}  # response mode of the fake Gemini endpoint
PAGES = SAMPLES / "pages"


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


def test_structured_extraction():
    assert pm.extract_structured(page("tv-55")) == {"price": 1149.0, "currency": "AED", "in_stock": True, "source": "json-ld"}
    assert pm.extract_structured(page("kettle"))["source"] == "meta"
    laptop = pm.extract_structured(page("laptop-14"))  # offer nested in a JSON-LD list
    assert (laptop["price"], laptop["in_stock"]) == (2199.0, False)
    assert pm.extract_structured(page("headset")) is None


def test_compare_back_in_stock_and_no_repeat_below_alert():
    item = {"name": "X", "url": "u", "our_price": "100", "currency": "AED", "drop_alert_pct": "5"}
    last = {"price": 90.0, "in_stock": False, "missing": False}
    alerts = pm.compare(item, {"price": 90.0, "currency": "AED", "in_stock": True}, last)
    assert len(alerts) == 1 and alerts[0].startswith("Competitor back in stock: X")


def test_dry_run_alerts_then_quiet_on_rerun(tmp_path, capsys):
    shutil.copytree(SAMPLES, tmp_path / "samples")
    watchlist = tmp_path / "samples" / "watchlist.csv"
    state = tmp_path / "prices.json"
    assert pm.main([str(watchlist), "--dry-run", "--state", str(state)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.strip().splitlines()[-1]) == {"checked": 4, "alerts": 4, "not_found": 1, "rate_limited": 0, "errors": 0}
    assert "AED 1,299.00 -> AED 1,149.00 (-11.5%)" in out
    assert "Competitor out of stock: Orbit 14 Laptop 16GB" in out

    pm.main([str(watchlist), "--dry-run", "--state", str(state)])
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["alerts"] == 0
    history = json.loads(state.read_text())["tv-55"]["history"]
    assert [h["price"] for h in history] == [1299.0, 1149.0, 1149.0]


@pytest.fixture
def fake_web(monkeypatch):
    """One local server playing the shop, the Claude API and the Telegram API."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, ctype="application/json"):
            raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            seen.append(("GET", self.path, None))
            if self.path == "/robots.txt":
                return self.reply(200, "User-agent: *\nDisallow: /private/\n", "text/plain")
            name = self.path.rsplit("/", 1)[-1]
            if (PAGES / f"{name}.html").exists():
                return self.reply(200, page(name), "text/html; charset=utf-8")
            self.reply(404, "missing", "text/plain")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append(("POST", self.path, body))
            if self.path.startswith("/v1beta/models/"):
                if GEMINI["status"] != 200:
                    return self.reply(GEMINI["status"], {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}})
                text = json.dumps({"found": True, "price": 89.5, "currency": "AED", "in_stock": True})
                return self.reply(200, {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]})
            if self.path.startswith("/v1/messages"):
                text = json.dumps({"found": True, "price": 89.5, "currency": "AED", "in_stock": True})
                return self.reply(200, {"id": "msg", "type": "message", "role": "assistant", "model": body["model"],
                                        "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                        "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
            self.reply(200, {"ok": True})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("TELEGRAM_API_BASE", base)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setattr(pm.time, "sleep", lambda s: None)
    GEMINI["status"] = 200
    import anthropic

    client = anthropic.Anthropic(api_key="test-key", base_url=base, max_retries=0)
    yield base, seen, client
    server.shutdown()


def live_watchlist(tmp_path, base, blocked=True):
    watchlist = tmp_path / "watchlist.csv"
    rows = list(csv.DictReader((SAMPLES / "watchlist.csv").open(encoding="utf-8")))
    for r in rows:
        r["url"] = f"{base}/p/{r['id']}"
    if blocked:
        rows.append({**rows[0], "id": "blocked", "url": f"{base}/private/blocked"})
    with watchlist.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    state = tmp_path / "prices.json"
    shutil.copy(SAMPLES / "state_before.json", state)
    return watchlist, state


def test_live_run_fetches_uses_claude_and_alerts(fake_web, tmp_path):
    base, seen, client = fake_web
    watchlist, state = live_watchlist(tmp_path, base)

    summary = pm.run(watchlist, state, dry_run=False, client=client)

    assert summary == {"checked": 4, "alerts": 4, "not_found": 0, "rate_limited": 0, "errors": 0}
    assert not any(path.startswith("/private") for _, path, _ in seen)  # robots.txt respected
    claude = [b for m, p, b in seen if p.startswith("/v1/messages")]
    assert len(claude) == 1  # only the page without structured data
    assert claude[0]["model"] == "claude-haiku-5-5" and "fallbacks" not in claude[0]
    assert "Now AED 89.50" in claude[0]["messages"][0]["content"] and "line-through" not in claude[0]["messages"][0]["content"]
    sent = [b["text"] for m, p, b in seen if p.endswith("/sendMessage")]
    assert len(sent) == 4
    assert any("Competitor price drop: Nova Wireless Headset" in t for t in sent)  # 95 -> 89.50
    assert json.loads(state.read_text())["headset"]["last"]["source"] == "claude"


def use_gemini(monkeypatch, base):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "any-non-empty-key")
    monkeypatch.setenv("GEMINI_API_BASE", base)


def test_live_run_with_gemini(fake_web, tmp_path, monkeypatch):
    base, seen, client = fake_web
    use_gemini(monkeypatch, base)
    watchlist, state = live_watchlist(tmp_path, base, blocked=False)
    summary = pm.run(watchlist, state, dry_run=False)

    assert summary == {"checked": 4, "alerts": 4, "not_found": 0, "rate_limited": 0, "errors": 0}
    gemini = [(p, b) for m, p, b in seen if p.startswith("/v1beta/models/")]
    assert len(gemini) == 1 and not any(p.startswith("/v1/messages") for _, p, _ in seen)
    path, body = gemini[0]
    assert path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json" and "additionalProperties" not in json.dumps(config)
    assert config["responseSchema"]["required"] == pm.LLM_SCHEMA["required"]
    assert "Now AED 89.50" in body["contents"][0]["parts"][0]["text"]
    assert json.loads(state.read_text())["headset"]["last"]["source"] == "gemini"


def test_gemini_rate_limit_skips_product_without_false_alert(fake_web, tmp_path, monkeypatch):
    base, seen, client = fake_web
    use_gemini(monkeypatch, base)
    sleeps = []
    monkeypatch.setattr(pm.time, "sleep", sleeps.append)
    GEMINI["status"] = 429
    watchlist, state = live_watchlist(tmp_path, base, blocked=False)
    summary = pm.run(watchlist, state, dry_run=False)

    assert len([p for m, p, b in seen if p.startswith("/v1beta/models/")]) == 3
    assert sleeps.count(1) == 1 and sleeps.count(2) >= 1  # Gemini backoff 1 s, 2 s (plus 2 s between shop requests)
    assert summary == {"checked": 3, "alerts": 3, "not_found": 0, "rate_limited": 1, "errors": 0}
    sent = [b["text"] for m, p, b in seen if p.endswith("/sendMessage")]
    assert not any("Price not found" in t for t in sent)
    assert json.loads(state.read_text())["headset"]["last"]["price"] == 95.0  # history untouched
