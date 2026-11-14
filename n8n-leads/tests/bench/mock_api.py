"""Stand-in for Supabase, Claude, Gemini, Telegram and an SMTP server, run inside the test process.

Records every request and answers with canned responses; tests override them with set().
upsert_lead is emulated closely enough for the workflow (created on first sight of a phone
or email, updated afterwards); the real function is tested against Postgres separately.

Live mode (LIVE_ANTHROPIC=1): /anthropic/* is relayed to api.anthropic.com with the key from
this process's ANTHROPIC_API_KEY, so the key never reaches n8n or a file.
"""

import email
import email.policy
import json
import os
import socketserver
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("MOCK_PORT", "8091"))
SMTP_PORT = int(os.environ.get("MOCK_SMTP_PORT", "2525"))
LIVE_ANTHROPIC = os.environ.get("LIVE_ANTHROPIC") == "1"


def claude_reply(data: dict) -> dict:
    return {"type": "message", "model": "claude-haiku-5-5", "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(data)}],
            "usage": {"input_tokens": 400, "output_tokens": 60}}


def gemini_reply(data: dict) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(data)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 400, "candidatesTokenCount": 60}}


SUMMARY = {"new_leads": 7, "hot_leads": 2, "won": 1, "awaiting_follow_up": 3, "followed_up": 4}

HOT_RENT = {"intent": "rent", "urgency": "hot", "language": "en", "summary": "Wants a 2 bedroom in Dubai Marina next month, 120k budget."}


class Mock:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.requests, self.overrides, self.leads = [], [], {}
            self.mail, self.smtp_reject = [], {}  # smtp_reject: recipient (or "*") -> SMTP reply
            self.demo_slots, self.demo_alerts = [], 0
            self.relayed = []  # live mode: model and token usage of each answer from api.anthropic.com

    def set(self, prefix: str, body=None, status: int = 200, times: int | None = None):
        """Answer requests whose path starts with prefix; times=None means until reset()."""
        with self.lock:
            self.overrides.insert(0, {"prefix": prefix, "body": body, "status": status, "times": times})

    def calls(self, prefix: str = "") -> list[dict]:
        with self.lock:
            return [r for r in self.requests if r["path"].startswith(prefix)]

    def respond(self, method: str, path: str, headers: dict, body):
        with self.lock:
            self.requests.append({"method": method, "path": path, "headers": headers, "body": body})
            for o in self.overrides:
                if path.startswith(o["prefix"]) and o["times"] != 0:
                    if o["times"]:
                        o["times"] -= 1
                    return o["status"], o["body"]
        if path.startswith("/anthropic/"):
            return 200, claude_reply(HOT_RENT)
        if path.startswith("/gemini/"):
            return 200, gemini_reply(HOT_RENT)
        if path.startswith("/telegram/"):
            return 200, {"ok": True, "result": {"message_id": 101, "chat": {"id": -100}, "text": (body or {}).get("text")}}
        if path.startswith("/supabase/rest/v1/rpc/upsert_lead"):
            return 200, self.upsert((body or {}).get("lead") or {})
        if path.startswith("/supabase/rest/v1/rpc/lead_demo_take_alert"):
            with self.lock:
                self.demo_alerts += 1
                return 200, {"allowed": self.demo_alerts <= (body or {}).get("p_daily_cap", 0)}
        if path.startswith("/supabase/rest/v1/rpc/lead_demo_take"):
            return 200, self.demo_take(body or {})
        if path.startswith("/supabase/rest/v1/lead_events"):
            return 201, None
        if path.startswith("/supabase/rest/v1/rpc/claim_follow_ups"):
            return 200, []
        if path.startswith("/supabase/rest/v1/rpc/finish_follow_ups"):
            results = (body or {}).get("results") or []
            return 200, {"followed_up": sum(bool(r.get("ok")) for r in results), "failed": sum(not r.get("ok") for r in results)}
        if path.startswith("/supabase/rest/v1/lead_daily_summary"):
            return 200, [SUMMARY]
        return 404, {"error": f"no mock for {path}"}

    def demo_take(self, b: dict) -> dict:
        """lead_demo_take without the time window: every slot counts until reset()."""
        with self.lock:
            if len(self.demo_slots) >= b.get("p_daily_cap", 0):
                return {"allowed": False, "reason": "daily", "retry_after": 3600}
            if self.demo_slots.count(b.get("p_client")) >= b.get("p_per_client", 0):
                return {"allowed": False, "reason": "client", "retry_after": 600}
            self.demo_slots.append(b.get("p_client"))
            return {"allowed": True, "reason": "", "retry_after": 0}

    def upsert(self, lead: dict) -> dict:
        with self.lock:
            key = next((self.leads[k] for k in (lead.get("phone"), lead.get("email")) if k and k in self.leads), None)
            action = "updated" if key else "created"
            row = key or {"id": str(uuid.uuid4()), "status": "new", "intent": "unclassified", "urgency": None, "summary": None}
            c = lead.get("classification") or {}
            row.update({k: lead.get(k) or row.get(k) for k in ("name", "phone", "email", "source", "message", "language")})
            if c.get("status") == "classified":
                row.update({k: c[k] for k in ("intent", "urgency", "language", "summary")})
            for k in (lead.get("phone"), lead.get("email")):
                if k:
                    self.leads[k] = row
            return {"action": action, "lead": dict(row)}


MOCK = Mock()


class SmtpHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP for nodemailer: EHLO, MAIL, RCPT, DATA, RSET, NOOP, QUIT. No TLS, no auth."""

    def reply(self, line: str):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.reply("220 mock ESMTP")
        sender, rcpts = None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            cmd = raw.decode(errors="replace").strip()
            verb = cmd.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.wfile.write(b"250-mock\r\n250-8BITMIME\r\n250 SMTPUTF8\r\n")
            elif verb == "MAIL":
                sender, rcpts = cmd.split(":", 1)[1].split()[0].strip("<>"), []
                self.reply("250 OK")
            elif verb == "RCPT":
                rcpt = cmd.split(":", 1)[1].split()[0].strip("<>")
                with MOCK.lock:
                    reject = MOCK.smtp_reject.get(rcpt) or MOCK.smtp_reject.get("*")
                if reject:
                    self.reply(reject)
                else:
                    rcpts.append(rcpt)
                    self.reply("250 OK")
            elif verb == "DATA":
                self.reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while (line := self.rfile.readline()) not in (b".\r\n", b".\n", b""):
                    lines.append(line[1:] if line.startswith(b"..") else line)
                msg = email.message_from_bytes(b"".join(lines), policy=email.policy.default)
                text = msg.get_body(("plain",))
                html = msg.get_body(("html",))
                with MOCK.lock:
                    MOCK.mail.append({"from": sender, "to": rcpts, "subject": str(msg["subject"]),
                                      "reply_to": str(msg["reply-to"] or ""), "header_from": str(msg["from"]),
                                      "text": text.get_content() if text else "", "html": html.get_content() if html else ""})
                self.reply(f"250 OK queued as mock{len(MOCK.mail)}")
            elif verb in ("RSET", "NOOP"):
                self.reply("250 OK")
            elif verb == "QUIT":
                self.reply("221 Bye")
                return
            else:
                self.reply("502 Command not implemented")


class SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def relay_to_anthropic(path: str, headers: dict, raw: bytes):
    req = urllib.request.Request("https://api.anthropic.com" + path[len("/anthropic"):], data=raw, method="POST")
    for h in ("anthropic-version", "anthropic-beta", "content-type"):
        if headers.get(h):
            req.add_header(h, headers[h])
    req.add_header("x-api-key", os.environ["ANTHROPIC_API_KEY"])
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


class Handler(BaseHTTPRequestHandler):
    def _handle(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode(errors="replace")
        headers = {k.lower(): v for k, v in self.headers.items()}
        if LIVE_ANTHROPIC and self.path.startswith("/anthropic/"):
            with MOCK.lock:
                MOCK.requests.append({"method": self.command, "path": self.path, "headers": {}, "body": body})
            status, out = relay_to_anthropic(self.path, headers, raw)
            if isinstance(out, dict) and out.get("usage"):
                with MOCK.lock:
                    MOCK.relayed.append({"model": out.get("model"), "usage": out["usage"]})
        else:
            status, out = MOCK.respond(self.command, self.path, headers, body)
        data = b"" if out is None else json.dumps(out).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = _handle

    def log_message(self, *args):
        pass


class Servers:
    def __init__(self):
        ThreadingHTTPServer.allow_reuse_address = True
        self.http = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        self.smtp = SmtpServer(("0.0.0.0", SMTP_PORT), SmtpHandler)
        for srv in (self.http, self.smtp):
            threading.Thread(target=srv.serve_forever, daemon=True).start()

    def shutdown(self):
        for srv in (self.http, self.smtp):
            srv.shutdown()
            srv.server_close()


def start() -> Servers:
    return Servers()
