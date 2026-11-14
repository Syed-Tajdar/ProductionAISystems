// "What does this cost to run": measured AI cost per item, today's free hosting, and a labelled estimate for
// production. Measured figures are copied from the committed result files and only ever cut, never rounded up;
// scripts/check-costs.mjs recomputes them from those files after the build and fails on any difference.

/** Cuts a number to `digits` decimals without rounding up. */
export const cut = (n: number, digits: number) => Math.floor(n * 10 ** digits + 1e-9) / 10 ** digits;
export const usd = (n: number, digits: number) => `$${cut(n, digits).toFixed(digits)}`;
export const money = (n: number) => `$${n.toFixed(2)}`;
const int = (n: number) => Math.floor(n + 1e-9).toLocaleString("en-US");

type Measured = {
  id: string;
  item: string; // one unit, in plain words
  plural: string;
  plain: string;
  mean: number;
  min: number;
  max: number;
  digits: number;
  scope: string;
  source: string;
  field: string;
};

export const measured: Measured[] = [
  {
    id: "call-qa-agent",
    item: "Reviewing one call",
    plural: "calls",
    plain: "about 1 cent",
    mean: 0.0101624,
    min: 0.009182,
    max: 0.01081,
    digits: 4,
    scope: "Claude Sonnet scoring at list price ($2 / $10 per million tokens), 10 test calls, 11 November 2026. Speech-to-text is billed per audio minute and was not measured.",
    source: "call-qa-agent/evals/results/latest.json",
    field: "summary.llm_cost_usd, calls[].llm_cost_usd",
  },
  {
    id: "rag-mcp",
    item: "Answering one question from documents",
    plural: "questions",
    plain: "under 1 cent",
    mean: 0.0063518,
    min: 0.0033949,
    max: 0.0113596,
    digits: 4,
    scope: "Claude Sonnet answer plus Haiku translation at list price, 32 test questions in English and Arabic, 6 November 2026. Indexing the documents is not included.",
    source: "rag-mcp/evals/results/latest.json",
    field: "summary.cost_usd, results[].cost_usd",
  },
  {
    id: "n8n-leads",
    item: "Sorting one website enquiry",
    plural: "enquiries",
    plain: "a small fraction of a cent",
    mean: 0.0001282,
    min: 0.0001215,
    max: 0.0001354,
    digits: 6,
    scope: "One Claude Haiku call per enquiry at list price ($0.10 / $0.50 per million tokens), 5 test enquiries, 14 November 2026.",
    source: "n8n-leads/tests/results/live_llm.json",
    field: "cost_usd_mean, cost_usd_min, cost_usd_max",
  },
];

export const measuredRows = measured.map((m) => ({
  ...m,
  average: usd(m.mean, m.digits),
  range: `${usd(m.min, m.digits)} to ${usd(m.max, m.digits)}`,
  perDollar: `${int(1 / m.mean)} ${m.plural}`,
}));

// Today: every part runs on a free plan, with these limits (as listed by each provider in November 2026).
export const freeTiers: { name: string; runs: string; limit: string }[] = [
  { name: "Vercel Hobby", runs: "this site and the call review and document demos", limit: "Free for personal, non-commercial use, which fits a portfolio." },
  { name: "Render free", runs: "n8n, which runs the lead automation", limit: "Sleeps after 15 minutes without visitors; the first enquiry after that waits about 2 to 3 minutes." },
  { name: "Supabase free", runs: "the database and audio storage", limit: "500 MB database and 1 GB of files. Pauses after 7 days without activity until it is resumed." },
  { name: "GitHub Actions", runs: "tests and the scheduled jobs", limit: "2,000 free minutes a month for a private repository; the schedules use about 450." },
];

// Production: an estimate, not a measurement. List prices checked 14 November 2026.
export const EXAMPLE_VOLUME = 1000;
export const hosting: { name: string; why: string; monthly: number }[] = [
  { name: "Render Starter", why: "n8n always on, so every enquiry is answered in seconds", monthly: 7 },
  { name: "Supabase Pro", why: "a database that never pauses and is not held to 500 MB", monthly: 25 },
  { name: "Vercel Pro, one seat", why: "work for a client is commercial use, which the free plan does not allow", monthly: 20 },
];

export const aiLines = measured.map((m) => ({
  id: m.id,
  label: `${int(EXAMPLE_VOLUME)} ${m.plural}`,
  sum: `${int(EXAMPLE_VOLUME)} × $${m.mean}`,
  total: cut(EXAMPLE_VOLUME * m.mean, 2),
}));
export const aiTotal = cut(aiLines.reduce((s, l) => s + l.total, 0), 2);
// The same sum from the large, cut figures on the cards, so the page can say why the two differ.
export const cardAiTotal = cut(measured.reduce((s, m) => s + EXAMPLE_VOLUME * cut(m.mean, m.digits), 0), 2);
export const cardGapCents = Math.round((aiTotal - cardAiTotal) * 100);
export const hostingTotal = hosting.reduce((s, h) => s + h.monthly, 0);
export const monthTotal = cut(aiTotal + hostingTotal, 2);

// Limits already built into the demos, and the cost rules of the production system.
export const caps = { questions: 150, enquiries: 100, perVisitor: 5, windowMinutes: 10, cacheAnswers: 200, cacheHours: 24 };
const [, rag, leads] = measured;
export const worstDay = {
  questions: money(cut(caps.questions * rag.max, 2)),
  enquiries: money(cut(caps.enquiries * leads.max, 2)),
};

export const controls: { title: string; body: string }[] = [
  {
    title: "Daily caps",
    body: `The document demo stops at ${caps.questions} questions a day and the lead demo at ${caps.enquiries} enquiries a day. Even if each one cost as much as the most expensive in the tests, a full day would cost ${worstDay.questions} and ${worstDay.enquiries}.`,
  },
  { title: "Limits per visitor", body: `${caps.perVisitor} questions or ${caps.perVisitor} enquiries per visitor in ${caps.windowMinutes} minutes, so one person cannot use up the day.` },
  { title: "Answers reused", body: `A question asked again within ${caps.cacheHours} hours is answered from a store of the last ${caps.cacheAnswers} answers, at no AI cost.` },
  { title: "Scoring needs a passcode", body: "Visitors to the call review demo see calls that are already scored. Uploading and scoring a new call needs the owner's passcode." },
  { title: "AI only where it pays", body: "In Calyx AI, calls are transcribed and scored only once they have converted, never the older backlog, with a daily cap on minutes per company." },
];
