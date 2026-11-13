import csv
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import invoice_to_sheet as its  # noqa: E402

import gemini  # noqa: E402  (the shared module, on the path once the script is imported)

SAMPLES = ROOT / "samples" / "invoices"


def read(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


def test_rules_english_invoice():
    inv = its.extract_with_rules(read("inv_001_desert_line.txt"))
    assert inv["supplier"] == "Desert Line Office Supplies LLC"
    assert inv["supplier_trn"] == "100234567800003"
    assert inv["invoice_number"] == "DL-2026-0412"
    assert (inv["invoice_date"], inv["due_date"]) == ("2026-11-02", "2026-12-02")
    assert (inv["currency"], inv["subtotal"], inv["vat"], inv["total"]) == ("AED", 1050.0, 52.5, 1102.5)
    assert its.validate(inv) == []


def test_rules_bilingual_invoice():
    inv = its.extract_with_rules(read("inv_003_elite_bilingual.txt"))
    assert inv["invoice_number"] == "EM-1093"
    assert inv["supplier_trn"] == "100555111200003"
    assert inv["invoice_date"] == "2026-10-30"
    assert (inv["currency"], inv["subtotal"], inv["vat"], inv["total"]) == ("AED", 2400.0, 120.0, 2520.0)


def test_validate_flags_wrong_total():
    inv = its.extract_with_rules(read("inv_002_gulf_fresh.txt"))
    assert its.validate(inv) == ["subtotal + VAT does not equal total"]


def test_validate_flags_vat_rate_and_missing_fields():
    issues = its.validate({"supplier": "X", "currency": "AED", "subtotal": 100.0, "vat": 10.0, "total": 110.0})
    assert "missing invoice_number" in issues
    assert "VAT is not 5% of subtotal" in issues


def test_dry_run_writes_csv_and_skips_duplicates(tmp_path, capsys):
    out = tmp_path / "invoices.csv"
    assert its.main([str(SAMPLES), "--dry-run", "--csv", str(out)]) == 0
    first = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first == {"added": 3, "duplicates": 1, "needs_check": 1, "failed": 0}

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["status"] for r in rows] == ["ok", "check", "ok"]

    its.main([str(SAMPLES), "--dry-run", "--csv", str(out)])
    second = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert second["added"] == 0 and second["duplicates"] == 4
    assert len(list(csv.DictReader(out.open(encoding="utf-8")))) == 3


class FakeWorksheet:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return self.values

    def append_rows(self, rows, value_input_option=None):
        self.values.extend(rows)


def test_sheet_sink_appends_new_rows_only():
    ws = FakeWorksheet([its.COLUMNS, ["old.txt", "Gulf Fresh Catering", "100987654300003", "GFC-7781"]])
    summary = its.run(SAMPLES, its.SheetSink(ws), dry_run=True)
    assert summary["added"] == 2 and summary["duplicates"] == 2
    assert len(ws.values) == 4


@pytest.fixture
def fake_claude():
    """Local stand-in for the Messages API that records requests."""
    seen = []
    reply = {"stop_reason": "end_turn", "data": {}, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            payload = {
                "id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": json.dumps(reply["data"])}],
                "stop_reason": reply["stop_reason"], "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
            if reply["status"] != 200:
                payload = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
            raw = json.dumps(payload).encode()
            self.send_response(reply["status"])
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import anthropic

    client = anthropic.Anthropic(api_key="test-key", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0)
    yield client, seen, reply
    server.shutdown()


def test_claude_request_for_pdf(fake_claude):
    client, seen, reply = fake_claude
    reply["data"] = {"supplier": "Blue Harbor IT Services", "supplier_trn": "100777888900003",
                     "invoice_number": "BH-0107", "invoice_date": "2026-11-01", "due_date": "",
                     "currency": "AED", "subtotal": 1800, "vat": 90, "total": 1890}
    inv = its.extract_with_claude(SAMPLES / "inv_005_blue_harbor.pdf", client)

    assert inv["invoice_number"] == "BH-0107" and inv["total"] == 1890
    assert "due_date" not in inv  # empty values are dropped
    req = seen[0]
    assert req["path"].startswith("/v1/messages")
    headers = req["headers"]
    assert "test-key" in headers.get("x-api-key", headers.get("authorization", ""))
    assert headers["anthropic-version"] == "2026-10-21"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    body = req["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"]["format"]["schema"]["required"] == its.SCHEMA["required"]
    doc = body["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["source"]["media_type"] == "application/pdf"


def test_haiku_gets_no_fallback(fake_claude, monkeypatch):
    client, seen, reply = fake_claude
    reply["data"] = {"supplier": "X", "invoice_number": "1", "invoice_date": "2026-11-01", "total": 1}
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    its.extract_with_claude(SAMPLES / "inv_001_desert_line.txt", client)
    assert seen[0]["body"]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in seen[0]["body"]
    assert "anthropic-beta" not in seen[0]["headers"]


def test_claude_refusal_raises(fake_claude):
    client, _, reply = fake_claude
    reply["stop_reason"] = "refusal"
    with pytest.raises(RuntimeError, match="declined"):
        its.extract_with_claude(SAMPLES / "inv_001_desert_line.txt", client)


def test_claude_rate_limit_falls_back_to_rules(fake_claude, tmp_path, capsys):
    client, seen, reply = fake_claude
    reply["status"] = 429
    summary = its.run(SAMPLES, its.CsvSink(tmp_path / "x.csv"), dry_run=False, client=client)
    assert summary["added"] == 3 and summary["failed"] == 1  # the PDF has no rule fallback
    rows = list(csv.DictReader((tmp_path / "x.csv").open(encoding="utf-8")))
    assert {r["source"] for r in rows} == {"rules (rate limited)"}


@pytest.fixture
def fake_gemini(monkeypatch):
    """Local stand-in for Gemini generateContent; records requests and sleeps."""
    seen, sleeps = [], []
    reply = {"status": 200, "data": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            if reply["status"] == 200:
                payload = {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(reply["data"])}]},
                                           "finishReason": "STOP"}]}
            else:
                payload = {"error": {"code": reply["status"], "status": "RESOURCE_EXHAUSTED", "message": "quota"}}
            raw = json.dumps(payload).encode()
            self.send_response(reply["status"])
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "any-non-empty-key")
    monkeypatch.setenv("GEMINI_API_BASE", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr("time.sleep", sleeps.append)
    yield seen, reply, sleeps
    server.shutdown()


def test_gemini_request_for_pdf(fake_gemini, tmp_path):
    seen, reply, _ = fake_gemini
    reply["data"] = {"supplier": "Blue Harbor IT Services", "supplier_trn": "100777888900003", "invoice_number": "BH-0107",
                     "invoice_date": "2026-11-01", "due_date": "2026-11-15", "currency": "AED", "subtotal": 1800, "vat": 90, "total": 1890}
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "inv.pdf").write_bytes((SAMPLES / "inv_005_blue_harbor.pdf").read_bytes())
    summary = its.run(folder, its.CsvSink(tmp_path / "x.csv"), dry_run=False)

    assert summary["added"] == 1
    row = next(csv.DictReader((tmp_path / "x.csv").open(encoding="utf-8")))
    assert (row["invoice_number"], row["status"], row["source"]) == ("BH-0107", "ok", "gemini")
    req = seen[0]
    assert req["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req["headers"]["x-goog-api-key"] == "any-non-empty-key"
    config = req["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert "additionalProperties" not in json.dumps(config["responseSchema"])
    assert config["responseSchema"]["required"] == its.SCHEMA["required"]
    parts = req["body"]["contents"][0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "application/pdf" and parts[1]["text"] == its.INSTRUCTION


def test_gemini_rate_limit_backs_off_then_falls_back(fake_gemini, tmp_path):
    seen, reply, sleeps = fake_gemini
    reply["status"] = 429
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "inv.txt").write_text(read("inv_001_desert_line.txt"), encoding="utf-8")
    summary = its.run(folder, its.CsvSink(tmp_path / "x.csv"), dry_run=False)

    assert len(seen) == 3 and sleeps == [1, 2]  # two short waits, then give up
    assert summary == {"added": 1, "duplicates": 0, "needs_check": 0, "failed": 0}
    row = next(csv.DictReader((tmp_path / "x.csv").open(encoding="utf-8")))
    assert (row["invoice_number"], row["source"]) == ("DL-2026-0412", "rules (rate limited)")


def test_missing_gemini_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        gemini.gemini_key()
