"""A stand-in for the parts of the n8n public API that setup_n8n.py uses, with switches for the ways an instance
met on another host differed from a standard n8n, so the fallbacks for them can be tested. Runs in the test process.

    fake = FakeN8n(no_list=True, id_read_only=True, no_publish=True, activate_after_reads=2, require_gzip=True)
    ... setup_n8n.run({"N8N_URL": fake.url, ...})
    fake.stop()
"""

import gzip
import json
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "fake-api-key-0f3c"
WAF_PAGE = ("<!DOCTYPE html><html><head><title>Request blocked</title><style>body{color:red}</style></head>"
            "<body><h1>Request blocked</h1><p>The firewall blocked this request.</p></body></html>")


class FakeN8n:
    def __init__(self, *, no_list=False, id_read_only=False, no_publish=False, activate_after_reads=0,
                 require_gzip=False, api_key=API_KEY, no_api=False):
        self.no_list, self.id_read_only, self.no_publish = no_list, id_read_only, no_publish
        self.activate_after_reads, self.require_gzip = activate_after_reads, require_gzip  # -1: never active
        self.api_key, self.no_api = api_key, no_api
        self.lock = threading.Lock()
        self.credentials: dict[str, dict] = {}
        self.workflows: dict[str, dict] = {}
        self.pending: dict[str, int] = {}   # workflow id -> reads left before it shows active
        self.requests: list[dict] = []
        self.fail: dict[tuple[str, str], tuple[int, str]] = {}  # (method, path prefix) -> (status, body), one-off
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                gzipped = self.headers.get("content-encoding") == "gzip"
                body = None
                if raw:
                    text = gzip.decompress(raw) if gzipped else raw
                    try:
                        body = json.loads(text)
                    except ValueError:
                        body = urllib.parse.parse_qs(text.decode())
                headers = {k.lower(): v for k, v in self.headers.items()}
                status, out = fake.respond(self.command, self.path, headers, body, bool(raw), gzipped)
                data = (out if isinstance(out, str) else json.dumps(out)).encode()
                self.send_response(status)
                self.send_header("content-type", "text/html" if isinstance(out, str) else "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def writes(self) -> list[dict]:
        return [r for r in self.requests if r["method"] != "GET"]

    def respond(self, method, raw_path, headers, body, has_body, gzipped):
        parsed = urllib.parse.urlparse(raw_path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        with self.lock:
            self.requests.append({"method": method, "path": path, "body": body, "has_body": has_body, "gzip": gzipped})
            for (m, prefix), answer in list(self.fail.items()):
                if m == method and path.startswith(prefix):
                    del self.fail[(m, prefix)]
                    return answer
            if has_body and self.require_gzip and not gzipped and path.startswith("/api/"):
                return 403, WAF_PAGE
            if path == "/healthz":
                return 200, {"status": "ok"}
            if path.startswith("/webhook/"):
                return self.webhook(path)
            if not path.startswith("/api/v1/") or self.no_api:
                return 404, f"<html><head><title>Error</title></head><body><pre>Cannot {method} {path}</pre></body></html>"
            if headers.get("x-n8n-api-key") != self.api_key:
                return 401, {"message": "unauthorized"}
            parts = path[len("/api/v1/"):].strip("/").split("/")
            if parts[0] == "credentials":
                return self.credential_route(method, parts[1:], body)
            if parts[0] == "workflows":
                return self.workflow_route(method, parts[1:], query, body)
            return 404, {"message": "not found"}

    def webhook(self, path):
        active = {w["name"] for w in self.workflows.values() if w["active"]}
        if path == "/webhook/lead-intake" and "Lead intake (Workflow A)" in active:
            return 200, {"ok": True}
        if path == "/webhook/lead-follow-up" and "Lead follow-up (Workflow B)" in active:
            return 403, "Authorization data is wrong!"
        return 404, {"message": "The requested webhook is not registered."}

    def credential_route(self, method, rest, body):
        if not rest:
            if method == "GET":
                if self.no_list:
                    return 405, {"message": "GET method not allowed"}
                return 200, {"data": [{k: c[k] for k in ("id", "name", "type")} for c in self.credentials.values()], "nextCursor": None}
            if method == "POST":
                if "id" in body and self.id_read_only:
                    return 400, {"message": "request/body/id is read-only"}
                cid = body.get("id") or secrets.token_hex(8)
                if cid in self.credentials:
                    return 400, {"message": "A credential with this ID already exists"}
                self.credentials[cid] = {"id": cid, "name": body["name"], "type": body["type"], "data": body["data"]}
                return 200, {"id": cid, "name": body["name"], "type": body["type"]}
        cid = rest[0]
        if method in ("GET", "PATCH") and self.no_list:
            return 405, {"message": f"{method} method not allowed"}
        if cid not in self.credentials:
            return 404, {"message": "Credential not found"}
        if method == "PATCH":
            self.credentials[cid].update({k: body[k] for k in ("name", "type", "data") if k in body})
            return 200, {"id": cid}
        if method == "DELETE":
            return 200, self.credentials.pop(cid)
        return 405, {"message": "method not allowed"}

    def workflow_route(self, method, rest, query, body):
        if not rest:
            if method == "GET":
                items = [w for w in self.workflows.values() if not query.get("name") or w["name"] == query["name"][0]]
                return 200, {"data": items, "nextCursor": None}
            if method == "POST":
                if "id" in body:
                    return 400, {"message": "request/body/id is read-only"}
                wid = secrets.token_hex(8)
                self.workflows[wid] = {"id": wid, "active": False, **body}
                return 200, self.workflows[wid]
        wid = rest[0]
        if wid not in self.workflows:
            return 404, {"message": "Not Found"}
        wf = self.workflows[wid]
        if len(rest) == 1:
            if method == "GET":
                if wid in self.pending:
                    self.pending[wid] -= 1
                    if self.pending[wid] == 0:
                        del self.pending[wid]
                        wf["active"] = True
                return 200, wf
            if method == "PUT":
                wf.update({k: body[k] for k in ("name", "nodes", "connections", "settings")})
                return 200, wf
        if method == "POST" and rest[1] in ("publish", "activate"):
            if rest[1] == "publish" and self.no_publish:
                return 404, "<html><head><title>Error</title></head><body><pre>Cannot POST /api/v1/workflows/x/publish</pre></body></html>"
            if self.activate_after_reads:
                self.pending[wid] = self.activate_after_reads  # -1 never reaches 0
                return 200, wf
            wf["active"] = True
            return 200, wf
        return 405, {"message": "method not allowed"}
