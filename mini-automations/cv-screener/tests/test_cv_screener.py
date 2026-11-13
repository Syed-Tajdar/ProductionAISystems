import csv
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv_screener as cs  # noqa: E402

SAMPLES = ROOT / "samples"
JOB = yaml.safe_load((SAMPLES / "job.yaml").read_text(encoding="utf-8"))


def cv(name: str) -> str:
    return cs.redact(cs.read_cv(SAMPLES / "cvs" / name))


def test_redaction_removes_contact_and_personal_lines():
    text = cv("cv_01_layla.txt") + cv("cv_03_omar.txt")
    for leaked in ["@example.com", "+971", "052 000 3303", "linkedin.com", "Jordanian", "14/03/1993", "Male", "Single"]:
        assert leaked not in text
    assert "(2021 - present)" in text  # date ranges are not mistaken for phone numbers


def test_pdf_cv_is_read():
    text = cv("cv_04_daniel.pdf")
    assert "Salesforce" in text and "[email]" in text


def test_rules_years_and_verdicts():
    result = cs.assess_with_rules(JOB, cv("cv_03_omar.txt"))
    verdicts = {r["id"]: r["verdict"] for r in result["requirements"]}
    assert result["years_experience"] == 2
    assert verdicts == {"experience": "partial", "arabic_english": "met", "crm": "met", "saas": "missing", "driving_licence": "missing"}


def test_quote_not_in_cv_becomes_needs_review():
    text = cv("cv_02_arjun.txt")
    result = {"requirements": [
        {"id": "arabic_english", "verdict": "met", "evidence_quote": "Fluent in Arabic", "note": ""},
        {"id": "crm", "verdict": "met", "evidence_quote": "Built macros and   reports in Zoho Desk.", "note": ""},
        {"id": "saas", "verdict": "missing", "evidence_quote": "", "note": ""}]}
    checked = {r["id"]: r["verdict"] for r in cs.verify_quotes(result, text)["requirements"]}
    assert checked == {"arabic_english": "needs_review", "crm": "met", "saas": "missing"}


def test_scoring_rules():
    def result(**v):
        return {"requirements": [{"id": r["id"], "verdict": v.get(r["id"], "met")} for r in JOB["requirements"]]}
    assert cs.score(JOB, result()) == (100.0, "shortlist")
    assert cs.score(JOB, result(arabic_english="missing")) == (75.0, "not shortlisted")
    assert cs.score(JOB, result(experience="partial", saas="missing", driving_licence="missing")) == (62.5, "review")
    assert cs.score(JOB, result(crm="needs_review"))[1] == "review"


def test_dry_run_outputs(tmp_path, capsys):
    assert cs.main([str(SAMPLES / "job.yaml"), str(SAMPLES / "cvs"), "--dry-run", "--out", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == {"screened": 5, "shortlist": 3, "review": 1, "not shortlisted": 1, "failed": 0}
    ranking = list(csv.DictReader((tmp_path / "ranking.csv").open(encoding="utf-8")))
    assert [r["candidate"] for r in ranking][:1] == ["cv_01_layla"]
    assert ranking[-1]["candidate"] == "cv_02_arjun"
    report = (tmp_path / "shortlist.md").read_text(encoding="utf-8")
    assert "@" not in report and "+971" not in report
    # Known limit of keyword rules: "Arabic (basic)" counts as met. Claude is asked to judge fluency.
    daniel = next(r for r in ranking if r["candidate"] == "cv_04_daniel")
    assert daniel["arabic_english"] == "met"


@pytest.fixture
def fake_claude():
    seen, reply = [], {"data": {}, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            raw = json.dumps({"id": "msg", "type": "message", "role": "assistant", "model": body["model"],
                              "content": [{"type": "text", "text": json.dumps(reply["data"])}], "stop_reason": "end_turn",
                              "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
            if reply["status"] != 200:
                raw = json.dumps({"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}).encode()
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

    yield anthropic.Anthropic(api_key="test-key", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0), seen, reply
    server.shutdown()


def test_claude_request_and_quote_guard(fake_claude, tmp_path):
    client, seen, reply = fake_claude
    reply["data"] = {"years_experience": 7, "summary": "Strong SaaS customer success lead; Arabic is basic.", "requirements": [
        {"id": "experience", "verdict": "met", "evidence_quote": "7 years at SaaS companies", "note": ""},
        {"id": "arabic_english", "verdict": "partial", "evidence_quote": "Arabic (basic)", "note": "basic Arabic only"},
        {"id": "crm", "verdict": "met", "evidence_quote": "Manage 30 mid-market accounts in Salesforce", "note": ""},
        {"id": "saas", "verdict": "met", "evidence_quote": "Ledgerly SaaS", "note": ""},
        {"id": "driving_licence", "verdict": "met", "evidence_quote": "Holds a UAE driving licence", "note": "invented"}]}
    folder = tmp_path / "cvs"
    folder.mkdir()
    (folder / "cv_04_daniel.pdf").write_bytes((SAMPLES / "cvs" / "cv_04_daniel.pdf").read_bytes())

    summary = cs.run(SAMPLES / "job.yaml", folder, tmp_path / "out", dry_run=False, client=client)

    assert summary["review"] == 1  # the invented licence quote forces a human review
    body = seen[0]["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0]["headers"]["anthropic-beta"]
    assert body["output_config"]["format"]["schema"]["properties"]["requirements"]["items"]["properties"]["id"]["enum"] == [
        r["id"] for r in JOB["requirements"]]
    content = body["messages"][0]["content"]
    assert "daniel.cruz@example.com" not in content and "[email]" in content
    assert "Ignore names, age, gender, nationality" in body["system"]
    report = (tmp_path / "out" / "shortlist.md").read_text(encoding="utf-8")
    assert "**driving_licence**: needs_review" in report


DANIEL_RESULT = {"years_experience": 7, "summary": "SaaS customer success lead; basic Arabic.", "requirements": [
    {"id": "experience", "verdict": "met", "evidence_quote": "7 years at SaaS companies", "note": ""},
    {"id": "arabic_english", "verdict": "partial", "evidence_quote": "Arabic (basic)", "note": "basic only"},
    {"id": "crm", "verdict": "met", "evidence_quote": "Manage 30 mid-market accounts in Salesforce", "note": ""},
    {"id": "saas", "verdict": "met", "evidence_quote": "Ledgerly SaaS", "note": ""},
    {"id": "driving_licence", "verdict": "missing", "evidence_quote": "", "note": ""}]}


def one_cv(tmp_path, name="cv_04_daniel.pdf"):
    folder = tmp_path / "cvs"
    folder.mkdir()
    (folder / name).write_bytes((SAMPLES / "cvs" / name).read_bytes())
    return folder


def test_claude_rate_limit_falls_back_to_review(fake_claude, tmp_path):
    client, seen, reply = fake_claude
    reply["status"] = 429
    summary = cs.run(SAMPLES / "job.yaml", one_cv(tmp_path, "cv_01_layla.txt"), tmp_path / "out", dry_run=False, client=client)
    assert summary["review"] == 1 and summary["failed"] == 0  # rules would shortlist Layla; a person checks instead
    assert "Rate limited: scored with keyword rules" in (tmp_path / "out" / "shortlist.md").read_text(encoding="utf-8")


@pytest.fixture
def fake_gemini(monkeypatch):
    seen, sleeps, reply = [], [], {"status": 200, "data": {}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            payload = ({"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(reply["data"])}]}, "finishReason": "STOP"}]}
                       if reply["status"] == 200 else {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}})
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


def test_gemini_request_and_quote_guard(fake_gemini, tmp_path):
    seen, reply, _ = fake_gemini
    reply["data"] = DANIEL_RESULT
    summary = cs.run(SAMPLES / "job.yaml", one_cv(tmp_path), tmp_path / "out", dry_run=False)

    assert summary["shortlist"] == 1  # partial Arabic still scores 75, above the 70 threshold
    req = seen[0]
    assert req["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req["headers"]["x-goog-api-key"] == "any-non-empty-key"
    schema = req["body"]["generationConfig"]["responseSchema"]
    assert "additionalProperties" not in json.dumps(schema)
    assert schema["properties"]["requirements"]["items"]["properties"]["id"]["enum"] == [r["id"] for r in JOB["requirements"]]
    assert "Ignore names, age, gender, nationality" in req["body"]["systemInstruction"]["parts"][0]["text"]
    prompt = req["body"]["contents"][0]["parts"][0]["text"]
    assert "daniel.cruz@example.com" not in prompt and "[email]" in prompt
    ranking = list(csv.DictReader((tmp_path / "out" / "ranking.csv").open(encoding="utf-8")))
    assert ranking[0]["arabic_english"] == "partial"


def test_gemini_rate_limit_backs_off_then_review(fake_gemini, tmp_path):
    seen, reply, sleeps = fake_gemini
    reply["status"] = 429
    summary = cs.run(SAMPLES / "job.yaml", one_cv(tmp_path), tmp_path / "out", dry_run=False)
    assert len(seen) == 3 and sleeps == [1, 2]
    assert summary == {"screened": 1, "shortlist": 0, "review": 1, "not shortlisted": 0, "failed": 0}
