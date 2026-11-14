// Checks that the plain-language parts of the built site (out/) use no technical term without an explanation next
// to it. Plain parts are the elements marked data-plain: the hero, the results labels, the services, the first
// sentence of each "How I build" card, the "In plain words" lines and blocks, the recruiter block, the cost
// calculator and the project page questions marked plain. Also the lead demo page (n8n-leads/landing/public):
// its page and the text strings of its script. A term may appear inside an element marked
// data-explained, which must carry its explanation in brackets, e.g. "RAG (answers from documents)". Text in
// attributes (hover titles) is not page text and is ignored. Exits 1 on any finding.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

export const TERMS = [/\bRAG\b/, /\bembeddings?\b/i, /\bdiarization\b/i, /\bpgvector\b/i, /\bMCP\b/, /\bRRF\b/, /\bLLMs?\b/, /\bevals?\b/i];

// The inner HTML of every element carrying data-plain, with nested elements of the same tag balanced.
export function plainBlocks(html) {
  const blocks = [];
  for (const m of html.matchAll(/<([a-z0-9]+)\b[^>]*\bdata-plain="([^"]*)"[^>]*>/g)) {
    const tag = m[1];
    let depth = 1, i = m.index + m[0].length;
    const re = new RegExp(`<(/?)${tag}\\b[^>]*>`, "g");
    re.lastIndex = i;
    let t;
    while (depth && (t = re.exec(html))) depth += t[1] ? -1 : t[0].endsWith("/>") ? 0 : 1;
    blocks.push({ name: m[2], html: html.slice(i, t ? t.index : html.length) });
  }
  return blocks;
}

// Findings for one block: explained terms are removed first (each must show a bracketed explanation).
export function findings(blockHtml) {
  const out = [];
  let html = blockHtml;
  for (const m of blockHtml.matchAll(/<span\b[^>]*\bdata-explained\b[^>]*>([\s\S]*?)<\/span>\s*<\/span>|<span\b[^>]*\bdata-explained\b[^>]*>([\s\S]*?)<\/span>/g)) {
    const text = (m[1] ?? m[2]).replace(/<[^>]+>/g, "");
    if (!/\([^)]+\)/.test(text)) out.push(`explained term without an explanation: "${text.trim()}"`);
    html = html.replace(m[0], " ");
  }
  const text = html.replace(/<!--[\s\S]*?-->/g, " ").replace(/<[^>]+>/g, " ").replace(/&[a-z#0-9]+;/gi, " ");
  for (const term of TERMS) {
    const hit = text.match(term);
    if (hit) out.push(`"${hit[0]}" without an explanation: "...${text.slice(Math.max(0, hit.index - 40), hit.index + 40).replace(/\s+/g, " ").trim()}..."`);
  }
  return out;
}

// Self-test, so a broken detector cannot pass silently.
const selfTest = [
  [`<p>We use RAG here</p>`, 1],
  [`<p>Answers <span data-explained>RAG <span>(answers from documents)</span></span> work</p>`, 0],
  [`<p><span data-explained>RAG</span></p>`, 1],
  [`<li title="LLM cost per call">about 1 cent</li>`, 0],
  [`<p>evaluate the evals</p>`, 1],
  [`<p>Medical retrieval</p>`, 0],
];
for (const [html, expected] of selfTest) {
  const got = findings(html).length;
  if ((got > 0) !== (expected > 0)) {
    console.error(`check-plain self-test failed on ${html}: ${got} finding(s)`);
    process.exit(1);
  }
}
const nested = plainBlocks(`<div data-plain="a"><div>RAG</div><p>x</p></div><p>after</p>`);
if (nested.length !== 1 || !nested[0].html.includes("<p>x</p>") || nested[0].html.includes("after")) {
  console.error("check-plain self-test failed on nested blocks");
  process.exit(1);
}

const root = new URL("../out/", import.meta.url).pathname;
if (!existsSync(root)) {
  console.error("out/ not found. Run `npm run build` first.");
  process.exit(1);
}
const walk = (dir) => readdirSync(dir).flatMap((f) => (statSync(join(dir, f)).isDirectory() ? walk(join(dir, f)) : [join(dir, f)]));
const pages = walk(root).filter((f) => f.endsWith(".html"));

// Every plain part this check is meant to cover must exist, so a renamed marker cannot make it pass.
const expected = {
  "index.html": ["hero", "results", "services", "card", "approach", "running-cost", "recruiters"],
  "projects/call-qa-agent.html": ["plain-words", "calculator"],
  "projects/n8n-leads.html": ["plain-words", "faq"],
  "projects/rag-mcp.html": ["plain-words"],
  "calyx.html": ["plain-words"],
};

const errors = [];
let checked = 0;
for (const page of pages) {
  const name = relative(root, page);
  const blocks = plainBlocks(readFileSync(page, "utf8"));
  for (const want of expected[name] ?? []) if (!blocks.some((b) => b.name === want)) errors.push(`${name}: no data-plain="${want}" block`);
  for (const b of blocks) {
    checked++;
    for (const f of findings(b.html)) errors.push(`${name} [${b.name}]: ${f}`);
  }
}
// The lead demo page deploys on its own (n8n-leads/landing), so it is read from the repository.
const demo = new URL("../../n8n-leads/landing/public/", import.meta.url).pathname;
const demoPage = readFileSync(join(demo, "index.html"), "utf8");
const demoBlocks = plainBlocks(demoPage);
if (!demoBlocks.some((b) => b.name === "demo")) errors.push('n8n-leads demo page: no data-plain="demo" block');
for (const b of demoBlocks) {
  checked++;
  for (const f of findings(b.html)) errors.push(`n8n-leads demo page [${b.name}]: ${f}`);
}
// Text the script writes into the page: every double-quoted string literal.
const strings = [...readFileSync(join(demo, "landing.js"), "utf8").matchAll(/"((?:[^"\\\n]|\\.)*)"/g)].map((m) => m[1]).join(" | ");
checked++;
for (const f of findings(strings)) errors.push(`n8n-leads demo script: ${f}`);

console.log(`Checked ${checked} plain-language block(s) across ${pages.length + 1} page(s).`);
if (errors.length) {
  console.error(`\n${errors.length} problem(s):\n  ${errors.join("\n  ")}`);
  process.exit(1);
}
console.log("Plain-language blocks use no unexplained technical terms.");
