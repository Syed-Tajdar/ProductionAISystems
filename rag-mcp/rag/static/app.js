// Progressive enhancement; the page works without this script (asking posts the form and loads the answer page,
// a.cite jumps to its source card).
// - Tapping a citation [n] opens the exact passage in a sheet (a bottom sheet on phones), quotes highlighted.
// - Asking stays on the page: the question and its answer are added to the thread as a pair, earlier pairs stay
//   above it (in this page only), and the newest one scrolls into view. The answer comes from the same POST /
//   the form uses; nothing is shown that the server did not send.
// - Each answer has a Copy button.

function openSource(id) {
  const el = id && document.getElementById(id);
  if (el && el.tagName === "DETAILS") el.open = true;
  return el;
}

function showPassage(source) {
  // Each question and answer pair has its own sheet; the first one served keeps the plain id.
  const sheet = source.closest(".turn")?.querySelector("dialog.sheet") || document.getElementById("passage-sheet");
  if (!sheet || typeof sheet.showModal !== "function") return false;
  const where = source.querySelector(".where");
  const passage = source.querySelector(".passage");
  const doc = source.querySelector(".doc");
  sheet.querySelector(".sheet-title").textContent = (source.querySelector(".n")?.textContent || "") + "  " + (where?.textContent || "");
  const body = sheet.querySelector(".sheet-body");
  body.replaceChildren(passage.cloneNode(true), doc ? doc.cloneNode(true) : "");
  sheet.showModal();
  body.querySelector("mark")?.scrollIntoView({ block: "center" });
  return true;
}

async function copyText(button, text, label) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Copy failed";
  }
  setTimeout(() => (button.textContent = label), 1600);
}

// The answer as plain text: statements with their markers, the not-covered line, then the sources.
function answerText(answer) {
  const parts = [answer.querySelector(".answer-text, .banner")?.textContent.replace(/\s+/g, " ").trim()];
  const nc = answer.querySelector(".not-covered");
  if (nc) parts.push(nc.textContent.replace(/\s+/g, " ").trim());
  const sources = [...answer.querySelectorAll("details.source")].map((s) =>
    `[${s.querySelector(".n")?.textContent.trim()}] ${s.querySelector(".where")?.textContent.replace(/\s+/g, " ").trim()}`);
  if (sources.length) parts.push("Sources:\n" + sources.join("\n"));
  return parts.filter(Boolean).join("\n\n");
}

document.addEventListener("click", (e) => {
  const cite = e.target.closest("a.cite");
  if (cite) {
    const source = openSource(cite.getAttribute("href").slice(1));
    if (source && showPassage(source)) e.preventDefault();
    return;
  }
  const copy = e.target.closest("button[data-copy-answer]");
  if (copy) {
    copyText(copy, answerText(copy.closest(".answer")), "Copy");
    return;
  }
  const sheet = e.target.closest("dialog.sheet");
  if (sheet && e.target === sheet) sheet.close(); // a tap on the backdrop closes it
});
window.addEventListener("hashchange", () => openSource(location.hash.slice(1)));

function showCopyButtons(root) {
  root.querySelectorAll("button[data-copy-answer]").forEach((b) => (b.hidden = !navigator.clipboard));
}
showCopyButtons(document);

// ---- the thread ----

const thread = document.getElementById("thread");
const status = document.getElementById("thread-status");
const smooth = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");
let turns = 0;
let busy = false;

// A pair taken from a served page gets its ids (and the links and labels that point at them) prefixed,
// so its citations, sources, sheet and trace panel stay its own.
function prefixIds(root, prefix) {
  root.querySelectorAll("[id]").forEach((el) => (el.id = prefix + el.id));
  root.querySelectorAll('a[href^="#"]').forEach((a) => a.setAttribute("href", "#" + prefix + a.getAttribute("href").slice(1)));
  root.querySelectorAll("[aria-labelledby]").forEach((el) =>
    el.setAttribute("aria-labelledby", el.getAttribute("aria-labelledby").split(" ").map((id) => prefix + id).join(" ")));
}

function fromTemplate(id) {
  return document.getElementById(id).content.cloneNode(true);
}

function setBusy(on) {
  busy = on;
  document.querySelectorAll("form[data-loading] button").forEach((b) => (b.disabled = on));
}

// Phones: the composer is pinned to the bottom, so the thread gets bottom padding as tall as the composer,
// and the newest answer's last source card can always scroll fully above it.
const composer = document.querySelector(".composer");
function padThread() {
  if (!thread || !composer) return;
  const pinned = getComputedStyle(composer).position === "sticky" && thread.querySelector(".turn");
  thread.style.setProperty("--composer-h", pinned ? `${Math.ceil(composer.getBoundingClientRect().height)}px` : "0px");
}
if (window.ResizeObserver && composer) new ResizeObserver(padThread).observe(composer);
window.addEventListener("resize", padThread);
padThread();

async function ask(question, form) {
  setBusy(true);
  document.querySelector("form.examples")?.setAttribute("hidden", ""); // examples are for an empty thread
  window.duFoldCard?.("rag");
  const turn = document.createElement("article");
  turn.className = "turn";
  turn.setAttribute("aria-label", "Question and answer");
  const bubble = document.createElement("p");
  bubble.className = "ask-bubble";
  bubble.dir = "auto";
  bubble.textContent = question;
  turn.append(bubble, fromTemplate("turn-loading"));
  thread.querySelector(":scope > .banner")?.remove();
  thread.append(turn);
  padThread();
  turn.scrollIntoView({ block: "start", behavior: smooth() });
  status.textContent = "Searching the documents";

  try {
    const res = await fetch(form.action, { method: "POST", body: new URLSearchParams({ q: question }), headers: { accept: "text/html" } });
    const page = new DOMParser().parseFromString(await res.text(), "text/html");
    const served = page.querySelector("#thread > .turn");
    const limits = page.querySelector(".limits");
    if (limits) document.querySelector(".limits")?.replaceWith(document.importNode(limits, true));
    if (!served) throw new Error(`no answer in the page (HTTP ${res.status})`);
    turns += 1;
    const content = document.importNode(served, true);
    prefixIds(content, `t${turns}-`);
    turn.replaceChildren(...content.childNodes);
    showCopyButtons(turn);
    status.textContent = turn.querySelector(".du-state-title")?.textContent || "Answer ready";
    if (turn.querySelector(".answer")) form.reset(); // after a limit or error the question stays, to ask again
  } catch {
    turn.querySelector(".loading")?.replaceWith(fromTemplate("turn-offline"));
    status.textContent = "The question did not get through";
  } finally {
    setBusy(false);
    turn.scrollIntoView({ block: "start", behavior: smooth() });
  }
}

document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!form.matches("form[data-loading]") || !thread || !window.fetch || !window.DOMParser) return;
  e.preventDefault();
  if (busy) return;
  const question = (e.submitter?.name === "q" ? e.submitter.value : new FormData(form).get("q") || "").trim();
  if (question) ask(question, form);
});

// "Use with MCP": a copy button for the client config (shown only when the script runs).
document.querySelectorAll("button[data-copy]").forEach((button) => {
  const target = document.getElementById(button.dataset.copy);
  if (!target) return;
  button.hidden = false;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(target.textContent);
      button.textContent = "Copied";
    } catch {
      const range = document.createRange(); // no clipboard access: select it for a manual copy
      range.selectNodeContents(target);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      button.textContent = "Selected";
    }
    setTimeout(() => (button.textContent = "Copy"), 1600);
  });
});
