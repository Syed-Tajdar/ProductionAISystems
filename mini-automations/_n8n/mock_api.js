// Minimal stand-in for external APIs. Records every request and returns
// canned responses. Tests override responses with POST /__mock/set.
const http = require("http");

let requests = [];
let overrides = []; // { method, prefix, status, body }

const defaults = [
  { prefix: "/anthropic/", body: { type: "message", stop_reason: "end_turn", content: [{ type: "text", text: "{}" }] } },
  { prefix: "/graph/", body: { messaging_product: "whatsapp", messages: [{ id: "wamid.MOCK" }] } },
  { prefix: "/telegram/", body: { ok: true, result: { message_id: 1 } } },
  { prefix: "/supabase/", body: [] },
  { prefix: "/gemini/", body: { candidates: [{ content: { role: "model", parts: [{ text: "{}" }] }, finishReason: "STOP" }] } },
  { prefix: "/hubspot/", body: { status: "COMPLETE", results: [] } },
  { prefix: "/sheets/", body: {} },
];

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

http
  .createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      let body = raw;
      try { body = raw ? JSON.parse(raw) : null; } catch {}

      if (req.url === "/__mock/requests") return send(res, 200, requests);
      if (req.url === "/__mock/reset") { requests = []; overrides = []; return send(res, 200, { ok: true }); }
      if (req.url === "/__mock/set") { overrides.unshift(body); return send(res, 200, { ok: true }); }

      requests.push({ method: req.method, path: req.url, headers: req.headers, body });
      const hit =
        overrides.find((o) => req.url.startsWith(o.prefix) && (!o.method || o.method === req.method)) ||
        defaults.find((d) => req.url.startsWith(d.prefix));
      if (!hit) return send(res, 404, { error: "no mock for " + req.url });
      send(res, hit.status || 200, hit.body);
    });
  })
  .listen(8090, () => console.log("mock api on :8090"));
