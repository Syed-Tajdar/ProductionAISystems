// Demo form for Workflow A. Wakes the n8n server when the page loads (the free hosting plan sleeps when idle),
// posts the form, waits long enough for a cold start, then shows what the workflow did with the lead.
// While n8n starts, its port answers before the webhooks are registered (404), and the host can answer 502
// or 503 with its own page: those are retried until the deadline. A JSON answer from the workflow is final.
// Everything from the server is written with textContent, never as HTML.
(function () {
  "use strict";
  const COLD_HINT_MS = 6000;    // after this, say the server is starting
  const LONG_HINT_MS = 100000;  // after this, say it is nearly there
  const TIMEOUT_MS = 300000;    // give up: a cold start takes 2 to 3 minutes on the free plan
  const RETRY_MS = 4000;

  const $ = (id) => document.getElementById(id);
  const form = $("lead-form");
  const button = form.querySelector("button[type=submit]");
  const url = String(window.LEAD_WEBHOOK_URL || "");
  const configured = /^https?:\/\/[^/]+\/webhook\/[\w-]+$/.test(url);
  form.noValidate = true; // the script checks the fields itself; without it the browser does

  function text(id, value) { $(id).textContent = value; }
  function setState(kind, body) {
    const slot = $("state-slot");
    slot.replaceChildren();
    if (!kind) return;
    const node = $("state-" + kind).content.cloneNode(true);
    const p = node.querySelector(".du-state-body");
    if (p && body) p.textContent = body;
    slot.append(node);
    slot.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function formError(message) {
    const el = $("form-error");
    el.textContent = message || "";
    el.hidden = !message;
  }
  function wait(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    if (s <= 90) return "in a minute";
    if (s < 5400) return "in about " + Math.round(s / 60) + " minutes";
    return "in about " + Math.round(s / 3600) + " hours";
  }

  if (!configured) {
    button.disabled = true;
    setState("setup");
    return;
  }

  // Warm-up: any request wakes the server. no-cors because only the wake-up matters, not the answer.
  const server = $("server");
  let awake = false;
  const slow = setTimeout(() => {
    if (awake) return;
    server.hidden = false;
    server.textContent = "Waking the server, it sleeps when nobody uses it. This takes 2 to 3 minutes.";
  }, 1500);
  fetch(new URL("/healthz", url), { mode: "no-cors", cache: "no-store" })
    .then(() => {
      awake = true;
      clearTimeout(slow);
      if (!server.hidden) server.textContent = "Server is up and loading the workflow.";
    })
    .catch(() => { clearTimeout(slow); server.hidden = true; });

  // Examples: each fill gets a new number, so the first send is a new lead and a second send is a repeat.
  const examples = JSON.parse($("examples").textContent);
  form.querySelectorAll("[data-example]").forEach((b) => {
    b.addEventListener("click", () => {
      const ex = examples[Number(b.dataset.example)];
      const n = String(Math.floor(1000 + Math.random() * 9000));
      for (const k of ["name", "phone", "email", "message"]) form.elements[k].value = (ex[k] || "").replaceAll("{n}", n);
      form.consent.checked = true;
      formError("");
    });
  });

  const CATEGORY = { hot: "Hot", warm: "Warm", cold: "Cold", unclassified: "Unclassified" };
  const INTENT = { buy: "wants to buy", rent: "wants to rent", info: "has a question", spam: "spam" };
  const ALERT = {
    sent: "Sent to the sales team on Telegram, marked as a demo.",
    not_needed: "Not needed. Only hot leads, and leads the AI model could not rate, alert sales.",
    capped: "Skipped: today's demo alerts are used up, so the owner's phone isn't flooded. A real setup alerts on every hot lead.",
    failed: "Telegram did not take the alert. The lead is saved and the failure is logged on it.",
  };
  const NO_EMAIL = {
    returning: "None: this lead already got one the first time.",
    no_email: "None: no email address was given. Sales can call or message the number.",
    spam: "None: spam gets no reply.",
  };

  function stepState(id, state) { $(id).dataset.state = state; }

  // Telegram HTML as the workflow sends it: <b> for bold, everything else escaped text. Built as DOM nodes,
  // never as HTML, so nothing from the response can run.
  function telegramText(html) {
    const out = document.createDocumentFragment();
    const plain = (t) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
    let bold = false;
    for (const part of String(html).split(/(<\/?b>)/)) {
      if (part === "<b>" || part === "</b>") { bold = part === "<b>"; continue; }
      if (!part) continue;
      const node = document.createTextNode(plain(part));
      if (bold) { const strong = document.createElement("strong"); strong.append(node); out.append(strong); } else out.append(node);
    }
    return out;
  }

  function showResult(d) {
    const cat = CATEGORY[d.category] ? d.category : "unclassified";
    const badge = $("r-category");
    badge.textContent = CATEGORY[cat];
    badge.className = "badge cat-" + cat;
    text("r-intent", d.intent ? INTENT[d.intent] || d.intent : "");
    text("r-reason", d.reason || "");
    $("r-reason").hidden = !d.reason;
    text("r-summary", d.summary || "");
    $("r-summary").hidden = !d.summary;
    text("r-duplicate", d.duplicate
      ? "Repeat: an earlier enquiry has the same phone or email, so that lead was updated instead of saved twice."
      : "New lead, saved once.");
    text("r-alert", ALERT[d.alert] || ALERT.not_needed);
    // The Telegram message as sales received it: the workflow returns the exact text it sent.
    const tg = $("r-tg");
    tg.hidden = !(d.alert === "sent" && d.alert_message);
    if (!tg.hidden) $("r-tg-text").replaceChildren(telegramText(d.alert_message));
    const email = d.email;
    text("r-email-status", email ? "Written for this lead and shown below instead of sent." : NO_EMAIL[d.no_email] || NO_EMAIL.no_email);
    $("r-email").hidden = !email;
    if (email) {
      text("r-to", email.to);
      text("r-subject", email.subject);
      const body = $("r-body");
      body.textContent = email.text;
      body.dir = email.dir === "rtl" ? "rtl" : "ltr";
      body.lang = email.language === "ar" ? "ar" : "en";
      $("r-subject").dir = "auto";
    }
    // Each step's marker: done, skipped (nothing to do) or needs a look.
    stepState("step-sorted", cat === "unclassified" ? "warn" : "done");
    stepState("step-repeat", "done");
    stepState("step-alert", { sent: "done", failed: "warn" }[d.alert] || "skipped");
    stepState("step-email", email ? "done" : "skipped");
    const result = $("result");
    result.hidden = false;
    result.focus({ preventScroll: true });
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function validate(data) {
    const phone = data.get("phone").trim();
    const email = data.get("email").trim();
    if (!data.get("name").trim()) return "Please enter a name.";
    if (!phone && !email) return "Please add a phone number or an email.";
    if (phone && !/^\+?[0-9\s\-()]{7,20}$/.test(phone)) return "Please check the phone number.";
    if (email && !form.email.checkValidity()) return "Please check the email address.";
    if (!data.get("message").trim()) return "Please say what you are looking for.";
    if (!form.consent.checked) return "Please tick the box to confirm you used made-up details.";
    return "";
  }

  let busy = false;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (busy) return;
    const data = new FormData(form);
    const problem = validate(data);
    formError(problem);
    if (problem) return;

    const body = new URLSearchParams();
    for (const k of ["name", "phone", "email", "message", "company", "consent"]) body.append(k, String(data.get(k) || "").trim());
    body.append("source", "web_form");
    body.append("page", window.location.href.split("?")[0]);
    const params = new URLSearchParams(window.location.search);
    for (const k of ["utm_source", "utm_medium", "utm_campaign"]) if (params.get(k)) body.append(k, params.get(k));
    body.append("submitted_at", new Date().toISOString());

    busy = true;
    button.disabled = true;
    setState(null);
    $("result").hidden = true;
    const sending = $("sending");
    const sendingText = $("sending-text");
    sendingText.textContent = "Sending your enquiry to the workflow...";
    sending.hidden = false;
    const hints = [
      setTimeout(() => { sendingText.textContent = "The server was asleep and is starting up. On the free hosting plan this takes 2 to 3 minutes; after that it answers in a few seconds. Your enquiry is sent as soon as it is ready."; }, COLD_HINT_MS),
      setTimeout(() => { sendingText.textContent = "Still starting, nearly there..."; }, LONG_HINT_MS),
    ];
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), TIMEOUT_MS);
    const deadline = Date.now() + TIMEOUT_MS;

    try {
      let res, out;
      for (;;) {
        // Form encoding keeps this a simple cross-origin request, with no preflight.
        res = await fetch(url, { method: "POST", body, signal: abort.signal });
        out = await res.json().catch(() => null);
        const starting = res.status === 404 || ([502, 503, 504].includes(res.status) && !(out && "ok" in out));
        if (!starting || Date.now() + RETRY_MS > deadline) break;
        await new Promise((r) => setTimeout(r, RETRY_MS));
      }
      out = out || {};
      sending.hidden = true; // before the result shows, so the scroll to it lands where it should
      if (res.ok && out.ok) {
        if (out.demo) showResult(out.demo);
        else setState("received");
      } else if (res.status === 422) {
        formError("Please check the details: " + (out.error || "the phone number or email is not valid") + ".");
      } else if (res.status === 429) {
        setState("limit", out.reason === "daily"
          ? "Today's demo enquiries are used up. The form opens again " + wait(out.retry_after) + "."
          : "This demo takes " + form.dataset.perVisitor + " enquiries per visitor in " + form.dataset.windowMinutes
            + " minutes. You can send more " + wait(out.retry_after) + ".");
      } else {
        setState("error", out.error || "Something went wrong on the server. Please try again in a minute.");
      }
    } catch (err) {
      setState("error", err && err.name === "AbortError"
        ? "The server took longer than five minutes to start. Please try again: it is usually awake by now."
        : "The request did not get through. Check the connection and try again.");
    } finally {
      clearTimeout(timer);
      hints.forEach(clearTimeout);
      sending.hidden = true;
      busy = false;
      button.disabled = false;
    }
  });

  $("again").addEventListener("click", () => {
    form.reset();
    formError("");
    setState(null);
    $("result").hidden = true;
    form.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    form.querySelector("[data-example]").focus({ preventScroll: true });
  });
})();
