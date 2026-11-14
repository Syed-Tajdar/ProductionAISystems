// Checks the "What it costs to run" section of the built home page (out/index.html) against the committed result
// files and settings it quotes: every measured figure is recomputed from its source and must appear exactly, cut and
// never rounded up, along with its source path, the estimate's arithmetic and the demo limits. Exits 1 on any finding.
import { existsSync, readFileSync } from "node:fs";

const repo = new URL("../../", import.meta.url).pathname;
const read = (path) => readFileSync(repo + path, "utf8");
const json = (path) => JSON.parse(read(path));

export const cut = (n, digits) => Math.floor(n * 10 ** digits + 1e-9) / 10 ** digits;
const usd = (n, digits) => `$${cut(n, digits).toFixed(digits)}`;
const money = (n) => `$${n.toFixed(2)}`;
const int = (n) => Math.floor(n + 1e-9).toLocaleString("en-US");
const mean = (xs) => xs.reduce((s, x) => s + x, 0) / xs.length;

// Self-test, so a broken helper cannot pass silently: 0.01081 must not become 0.0109, 0.0063518 not 0.0064.
for (const [n, d, want] of [[0.01081, 4, "$0.0108"], [0.0063518, 4, "$0.0063"], [0.0001354, 6, "$0.000135"], [0.0101624, 4, "$0.0101"], [0.29, 2, "$0.29"]]) {
  if (usd(n, d) !== want) {
    console.error(`check-costs self-test failed: ${n} gave ${usd(n, d)}, want ${want}`);
    process.exit(1);
  }
}

const page = new URL("../out/index.html", import.meta.url).pathname;
if (!existsSync(page)) {
  console.error("out/index.html not found. Run `npm run build` first.");
  process.exit(1);
}
const html = readFileSync(page, "utf8");
const start = html.indexOf('<section id="running-cost"');
if (start < 0) {
  console.error('out/index.html: no <section id="running-cost">');
  process.exit(1);
}
const text = html
  .slice(start, html.indexOf("</section>", start))
  .replace(/<!--[\s\S]*?-->/g, "")
  .replace(/<[^>]+>/g, " ")
  .replaceAll("&#x27;", "'")
  .replaceAll("&amp;", "&")
  .replace(/\s+/g, " ");

// Each measured item: per-item costs from the result file, the average as the file states it.
const callQa = json("call-qa-agent/evals/results/latest.json");
const rag = json("rag-mcp/evals/results/latest.json");
const leads = json("n8n-leads/tests/results/live_llm.json");
const leadCosts = Object.values(leads.leads).map((l) => l.cost_usd);
const items = [
  { source: "call-qa-agent/evals/results/latest.json", plural: "calls", digits: 4, average: callQa.summary.llm_cost_usd, each: callQa.calls.map((c) => c.llm_cost_usd) },
  { source: "rag-mcp/evals/results/latest.json", plural: "questions", digits: 4, average: rag.summary.cost_usd, each: rag.results.map((r) => r.cost_usd) },
  { source: "n8n-leads/tests/results/live_llm.json", plural: "enquiries", digits: 6, average: leads.cost_usd_mean, each: leadCosts },
];

const errors = [];
const want = (s, why) => {
  if (!text.includes(s)) errors.push(`missing ${why}: "${s}"`);
};

// The lead file states its own mean, min and max; they must agree with the per-lead costs.
if (Math.abs(leads.cost_usd_mean - mean(leadCosts)) > 1e-7) errors.push(`${items[2].source}: cost_usd_mean does not match leads[].cost_usd`);
if (leads.cost_usd_min !== Math.min(...leadCosts) || leads.cost_usd_max !== Math.max(...leadCosts)) errors.push(`${items[2].source}: cost_usd_min/max do not match leads[].cost_usd`);

const VOLUME = 1000;
let aiTotal = 0;
let cardTotal = 0; // the same sum from the cut figures a reader sees on the cards
for (const it of items) {
  if (Math.abs(it.average - mean(it.each)) > 1e-7) errors.push(`${it.source}: the stated average does not match the per-item costs`);
  // The card reads "<average> <plain words>, or <n> <items> for one dollar".
  const card = `${usd(it.average, it.digits)} [a-z0-9 ]+, or ${int(1 / it.average)} ${it.plural} for one dollar`;
  if (!new RegExp(card.replaceAll("$", "\\$").replaceAll(".", "\\.")).test(text)) errors.push(`missing card from ${it.source}: /${card}/`);
  want(`Exact average $${cut(it.average, 7)}, from ${usd(Math.min(...it.each), it.digits)} to ${usd(Math.max(...it.each), it.digits)} per item`, `range from ${it.source}`);
  cardTotal += VOLUME * cut(it.average, it.digits);
  want(`Source: ${it.source}`, "source path");
  const m = cut(it.average, 7);
  const line = cut(VOLUME * m, 2);
  aiTotal += line;
  // The arithmetic shows twice (under the name on a phone, in its own column on a wider screen).
  want(`AI for ${int(VOLUME)} ${it.plural} ${int(VOLUME)} × $${m} measured average`, `estimate line for ${it.plural}`);
  want(`${int(VOLUME)} × $${m} measured average ${money(line)}`, `estimate amount for ${it.plural}`);
}
aiTotal = cut(aiTotal, 2);
cardTotal = cut(cardTotal, 2);
want(`1,000 times each of them gives ${money(cardTotal)}, ${Math.round((aiTotal - cardTotal) * 100)} cents less than ${money(aiTotal)}`, "note on the card figures");

// Hosting in the estimate: list prices, added up with the AI lines.
const hosting = [...text.matchAll(/(Render Starter|Supabase Pro|Vercel Pro, one seat) [^$]*?; list price \$(\d+\.\d\d)/g)].map((m) => Number(m[2]));
if (hosting.length !== 3) errors.push(`expected 3 hosting lines in the estimate, found ${hosting.length}`);
const hostingTotal = hosting.reduce((s, x) => s + x, 0);
want(`Total ${money(hostingTotal)} hosting + ${money(aiTotal)} AI`, "estimate total");
want(`${money(hostingTotal)} hosting + ${money(aiTotal)} AI ${money(cut(hostingTotal + aiTotal, 2))}`, "estimate sum");
want("Estimate, not measured", "estimate label");
want("not included, because it was not measured", "note on speech-to-text");

// Demo limits, read from the settings the demos run with.
const setting = (name) => Number(read("rag-mcp/rag/config.py").match(new RegExp(`${name}: int = (\\d+)`))[1]);
const limits = JSON.parse(read("n8n-leads/landing/build.py").match(/LIMITS = (\{[^}]+\})/)[1]);
const ragMax = Math.max(...items[1].each), leadMax = Math.max(...leadCosts);
want(`stops at ${setting("demo_daily_cap")} questions a day and the lead demo at ${limits.daily_cap} enquiries a day`, "daily caps");
want(`a full day would cost ${money(cut(setting("demo_daily_cap") * ragMax, 2))} and ${money(cut(limits.daily_cap * leadMax, 2))}`, "worst day");
if (setting("demo_questions_per_client") !== limits.per_visitor || setting("demo_window_seconds") !== limits.window_minutes * 60) {
  errors.push("the two demos no longer share one per-visitor limit; reword the section");
}
want(`${limits.per_visitor} questions or ${limits.per_visitor} enquiries per visitor in ${limits.window_minutes} minutes`, "per-visitor limit");
want(`within ${setting("answer_cache_seconds") / 3600} hours is answered from a store of the last ${setting("answer_cache_size")} answers`, "answer cache");

if (errors.length) {
  console.error(`${errors.length} problem(s) in the running cost section:\n  ${errors.join("\n  ")}`);
  process.exit(1);
}
console.log(`Running cost section matches ${items.length} result files and the demo settings.`);
