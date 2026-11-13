// All page content lives here. Values in [brackets] are placeholders:
// they render as plain text (never as links) until replaced.
// Region-specific parts (second-language line, relocation, SEO wording, card order) are in regions.ts.

import { inRegionOrder, regions } from "./regions";

export type Link = { label: string; href?: string };

export type Badge = { plain: string; technical: string }; // plain words on the card, the measured wording on hover

export type Project = {
  id: string;
  title: string;
  plain: string; // "In plain words" line for a non-technical reader
  problem: string;
  stack: string[];
  metrics: Badge[]; // real numbers only
  page: string; // project page or case study
  code?: string;
  demo?: string; // live demo URL; the card shows "Try it live" only when this is set
  extra?: Link[];
  featured?: boolean;
};

export type MiniAutomation = {
  id: string; // anchor on the home page: #mini-<id>
  title: string;
  problem: string;
  stack: string[];
  github?: string;
};

export type Job = {
  role: string;
  connector?: string; // word before the company, "at" by default
  company?: string;
  place?: string;
  dates: string;
  summary: string;
  rebuilt?: { before: string; after: string; href: string }[]; // earlier rule-based work and its AI rebuild on this site
};

export const site = {
  name: "Syed Tajdar Nazar Gillani",
  shortName: "Syed Tajdar",
  title: "AI Automation Engineer",
  // Two-part pitch: the main line, then a smaller proof line (its link text points to the case study).
  pitch: "I turn manual business processes into production AI systems: agents, workflows, and the integrations and data behind them.",
  proofLine: {
    before: "End to end: design, build, deploy and operate. I built and run ",
    link: { label: "Calyx AI", href: "/calyx" },
    after: " as its sole engineer, processing 2,000+ calls a day.",
  },
  availability: "Available now",
  experience: "Building automations since 2019, AI in production since 2024.",
  languages: "English and Urdu. I build systems in Arabic and English.",
  // Hero chips: the same facts, short enough to keep the hero within the first phone screen.
  chips: { languages: "English and Urdu; builds in Arabic and English", experience: "Automations since 2019, AI in production since 2024" },
  roles: ["AI Automation Engineer", "AI Engineer", "Automation Specialist", "n8n Developer"],
  timeZone: "Based in Islamabad (GMT+5), one hour ahead of the UAE, so my working day overlaps the Gulf's.",
  location: "Islamabad, Pakistan",
  email: "syedtajdar812@gmail.com",
  linkedin: "https://www.linkedin.com/in/syed-tajdar-nazar-a2b431389",
  github: "https://github.com/Syed-Tajdar",
  cv: "/cv.pdf",
  cvFileName: "Syed-Tajdar-Nazar-Gillani-CV.pdf", // the name it downloads under
  photo: {
    webp: { "320": "/photo/syed-tajdar-320.webp", "640": "/photo/syed-tajdar-640.webp" },
    jpg: { "320": "/photo/syed-tajdar-320.jpg", "640": "/photo/syed-tajdar-640.jpg" },
    alt: "Syed Tajdar, head and shoulders, in a navy suit and white shirt against a grey background",
  },
};

// The repository is private until the public release. Code links, the GitHub button and README links appear only
// when the site is built with one of these, so until then nothing points at a page that would answer 404:
// - PUBLIC_CODE_URL: the address of the public repository, for example https://github.com/<owner>/<name>.
// - PUBLIC_CODE=true: the links point at this repository's own address (for the day it is made public as it is).
const codeUrl = (process.env.PUBLIC_CODE_URL ?? "").trim().replace(/\/+$/, "");
if (codeUrl && !/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+$/.test(codeUrl)) {
  throw new Error(`PUBLIC_CODE_URL must be a GitHub repository address, not "${codeUrl}"`);
}
export const publicCode = Boolean(codeUrl) || process.env.PUBLIC_CODE === "true";
const code = `${codeUrl || "https://github.com/Syed-Tajdar/ProductionAISystems"}/tree/main`;
export const codeLink = (path: string) => (publicCode ? `${code}/${path}` : undefined);

// Live demos (Vercel), by project id. Used by the cards, the project pages and the JSON-LD.
// The lead demo wakes a sleeping n8n server, so it is listed only once LEADS_DEMO_URL is set at build time.
const leadsDemo = (process.env.LEADS_DEMO_URL ?? "").trim().replace(/\/+$/, "");
if (leadsDemo && !/^https:\/\/[a-z0-9.-]+$/i.test(leadsDemo)) throw new Error(`LEADS_DEMO_URL must be an https origin, not "${leadsDemo}"`);
export const demos: Record<string, string> = {
  "call-qa-agent": "https://syed-call-qa.vercel.app",
  "rag-mcp": "https://syed-rag.vercel.app",
  ...(leadsDemo ? { "n8n-leads": leadsDemo } : {}),
};

export const projects: Project[] = [
  {
    id: "calyx",
    title: "Calyx AI",
    plain: "One dashboard for every call a pay-per-call business buys and sells, pulled together from the platforms it uses, with AI that answers questions about the numbers.",
    problem:
      "Pay-per-call teams run campaigns across several call-tracking platforms with no single reliable view. Calyx AI syncs them into one multi-tenant analytics store with write-back to the platforms and an AI layer. Built end to end as the sole engineer and running in production.",
    stack: ["Go", "TypeScript", "ClickHouse", "PostgreSQL", "Redis", "Kafka", "Docker"],
    metrics: [
      { plain: "2,000+ calls a day", technical: "2,000+ calls a day across two live accounts, after a 30-day backfill" },
      { plain: "2 call platforms connected live", technical: "Ringba and Retreaver certified live; TrackDrive and CallGrid adapters built" },
      { plain: "31% duplicate records removed", technical: "About 1.4 million duplicate rows (31%) removed with ClickHouse ReplacingMergeTree" },
    ],
    page: "/calyx",
    extra: [{ label: "PDF", href: "/calyx-ai-case-study.pdf" }],
    featured: true,
  },
  {
    id: "call-qa-agent",
    title: "Call QA Agent",
    plain: "Listens to every sales or support call, checks it against your quality checklist and shows the exact sentence behind each pass or fail.",
    problem:
      "Call centers review only a small sample of calls by hand, so missed disclosures slip through. This agent scores every call against a rubric and quotes the exact line that passed or failed.",
    stack: ["Python", "FastAPI", "Deepgram", "Claude", "Supabase"],
    metrics: [
      { plain: "67 of 67 checks right in tests", technical: "Item accuracy 67/67 on 10 scripted test calls" },
      { plain: "about 1 cent per call", technical: "Average LLM cost per scored call: $0.0102 at list price" },
      { plain: "91% of speech matched to the right speaker", technical: "Diarization accuracy 91% on 8 audio calls" },
    ],
    page: "/projects/call-qa-agent",
    code: codeLink("call-qa-agent"),
    demo: demos["call-qa-agent"],
  },
  {
    id: "n8n-leads",
    title: "n8n Lead Automation",
    plain: "Every website enquiry is sorted by urgency, saved once, sent to sales if it's hot and followed up automatically, in English or Arabic.",
    problem:
      "Leads from forms go cold when nobody replies fast. These workflows classify each lead with an LLM, deduplicate it, alert sales on hot leads and follow up on their own.",
    stack: ["n8n", "Claude", "Supabase", "Telegram", "GitHub Actions"],
    metrics: [
      { plain: "each enquiry handled in 2 to 3 seconds", technical: "1.8 to 3.0 s per submission end to end, with the real Claude API" },
      { plain: "5 of 5 test enquiries sorted correctly", technical: "5/5 synthetic test leads classified as expected, including Arabic and spam" },
      { plain: "replies in English or Arabic", technical: "Auto-reply email in the lead's language (en or ar)" },
    ],
    page: "/projects/n8n-leads",
    code: codeLink("n8n-leads"),
    demo: demos["n8n-leads"],
  },
  {
    id: "rag-mcp",
    title: "Bilingual Document Assistant",
    plain: "Ask a question about your company's policies in English or Arabic and get a short answer, with the exact passage it came from.",
    problem:
      "Teams need answers from policies and manuals in Arabic and English, with sources they can check. This assistant answers with checked citations and gives agents the same tools over MCP.",
    stack: ["Python", "pgvector", "Claude", "Gemini embeddings", "MCP"],
    metrics: [
      { plain: "right passage found for 26 of 27 test questions", technical: "Retrieval hit rate 26/27 (96%): every expected section in the top 5" },
      { plain: "every quote checked: 67 of 67 valid", technical: "Citation validity 67/67: each quote found in the cited passage" },
      { plain: "says so when the answer isn't there (5 of 5)", technical: "5/5 out-of-scope questions answered with \"not in the documents\"" },
    ],
    page: "/projects/rag-mcp",
    code: codeLink("rag-mcp"),
    demo: demos["rag-mcp"],
  },
];

// Every region profile must order exactly these projects, so a typo fails the build.
for (const [name, r] of Object.entries(regions)) {
  const ids = projects.map((p) => p.id);
  const order = r.projectOrder as string[];
  if (order.length !== ids.length || new Set(order).size !== order.length || !ids.every((id) => order.includes(id))) {
    throw new Error(`regions.ts: projectOrder of "${name}" must list each of ${ids.join(", ")} once.`);
  }
}

export const orderedProjects = inRegionOrder(projects, (p) => p.id);

// Hero proof bar: every figure comes from an eval run or the running system (see the project pages).
// Each label in plain words; the measured, technical wording shows on hover and in full on the linked page.
export const proof: { value: string; label: string; technical: string; source: string; href: string }[] = [
  { value: "100%", label: "of quality checks judged right on 10 test calls (67 of 67)", technical: "Item accuracy 67/67 on 10 scripted test calls (eval run, 11 November 2026; an earlier run that day scored 66/67)", source: "Call QA Agent", href: "/projects/call-qa-agent#results" },
  { value: "96%", label: "finds the right passage for 26 of 27 answerable test questions", technical: "Retrieval hit rate in a 32-question English and Arabic eval: every expected section in the top 5 for 26 of 27 answerable questions", source: "Document Assistant", href: "/projects/rag-mcp#results" },
  { value: "2,000+", label: "calls a day handled by a system I built and run", technical: "Calls a day processed in production by Calyx AI, across two live accounts", source: "Calyx AI", href: "/calyx#results" },
  { value: "~$0.01", label: "about 1 cent to review one call", technical: "Average LLM cost per scored call, $0.0102 at list price ($2 / $10 per million tokens)", source: "Call QA Agent", href: "/projects/call-qa-agent#results" },
];

// Each card: a plain title and first sentence for any reader, then the technical detail.
export const approach: { title: string; plain: string; body: string }[] = [
  {
    title: "Measured, not claimed",
    plain: "Every number on this site was measured on a test or on the running system, and the page says exactly what it was measured on.",
    body: "Every figure on this site comes from an eval run or the running system, with its scope written next to it. Call QA is scored against expected verdicts on 10 test calls; the RAG system against 32 questions and an LLM judge.",
  },
  {
    title: "Proof behind every answer",
    plain: "An answer or verdict only counts if it can point to the source it came from, and when something breaks it stops safely instead of guessing.",
    body: "A verdict or an answer counts only if its quote is found in the source. LLM failures fall back to a safe state: needs review, unclassified or \"not in the documents\". Form text is treated as data and tested with a prompt-injection attempt.",
  },
  {
    title: "Predictable costs",
    plain: "I measure what each call or question costs and set caps, so the bill never surprises anyone.",
    body: "Cost per call or question is measured at list price. Public demos have per-visitor limits and a daily cap; Calyx AI transcribes only converted calls, never the backfill, with a daily cap on minutes.",
  },
  {
    title: "Arabic and English",
    plain: "Customers get replies, alerts and answers in their own language, Arabic or English, laid out right to left where needed.",
    body: "Answers, replies and alerts in the user's language, right to left where needed. Arabic spelling variants and Arabic-Indic digits are normalized, and retrieval works across languages when a document exists in only one.",
  },
];

const mini = (dir: string) => codeLink(`mini-automations/${dir}`);

export const miniAutomations: MiniAutomation[] = [
  {
    id: "whatsapp-lead-qualifier",
    title: "WhatsApp Lead Qualifier",
    problem: "Asks property leads the qualifying questions on WhatsApp in English or Arabic and alerts sales on hot leads.",
    stack: ["n8n", "WhatsApp API", "Claude"],
    github: mini("whatsapp-lead-qualifier"),
  },
  {
    id: "invoice-to-sheet",
    title: "Invoice to Sheet",
    problem: "Reads supplier invoices (PDF or text) into Google Sheets and flags wrong totals, VAT and duplicates.",
    stack: ["Python", "Claude", "Google Sheets"],
    github: mini("invoice-to-sheet"),
  },
  {
    id: "bilingual-email-triage",
    title: "Bilingual Email Triage",
    problem: "Labels Arabic and English emails, drafts replies in the sender's language and flags urgent ones on Telegram.",
    stack: ["n8n", "Gmail", "Claude"],
    github: mini("bilingual-email-triage"),
  },
  {
    id: "appointment-reminders",
    title: "Appointment Reminders",
    problem: "Sends clinic patients WhatsApp reminders the day before and 2 hours before, in English or Arabic.",
    stack: ["n8n", "Supabase", "WhatsApp API"],
    github: mini("appointment-reminders"),
  },
  {
    id: "price-monitor",
    title: "Price Monitor",
    problem: "Watches competitor product pages and sends a Telegram alert on price drops and stock changes.",
    stack: ["Python", "Claude", "Telegram"],
    github: mini("price-monitor"),
  },
  {
    id: "cv-screener",
    title: "CV Screener",
    problem: "Checks CVs against a job's requirements with quoted evidence, redacts personal data and sorts candidates for review.",
    stack: ["Python", "Claude", "pypdf"],
    github: mini("cv-screener"),
  },
  {
    id: "daily-report-bot",
    title: "Daily Report Bot",
    problem: "Posts yesterday's sales against the last week to Telegram each morning, with commentary checked against the numbers.",
    stack: ["n8n", "Supabase", "Claude"],
    github: mini("daily-report-bot"),
  },
  {
    id: "sheet-to-crm-sync",
    title: "Sheet to CRM Sync",
    problem: "Upserts leads from Google Sheets into HubSpot by email and writes each row's sync status back to the sheet.",
    stack: ["n8n", "Google Sheets", "HubSpot"],
    github: mini("sheet-to-crm-sync"),
  },
];

// The few tools a recruiter is most likely to search for, shown once in the hero; the full list is in Skills.
export const coreStack = ["n8n", "Python", "Claude API", "AI agents", "FastAPI", "Supabase", "Postgres"];

export const skills: { group: string; items: string[] }[] = [
  {
    group: "AI and agents",
    items: ["Claude API", "OpenAI API", "Gemini API", "Tool use", "Structured JSON output", "Prompt and context design", "RAG", "MCP servers", "LLM evals", "Deepgram STT"],
  },
  {
    group: "Automation",
    items: ["n8n", "Webhooks", "Scheduled jobs", "Error workflows", "Supabase Edge Functions"],
  },
  {
    group: "Integrations",
    items: ["Python", "FastAPI", "Go", "TypeScript", "Next.js", "REST APIs", "Telegram Bot API", "WhatsApp Cloud API", "Gmail", "Google Sheets", "HubSpot"],
  },
  {
    group: "Data",
    items: ["Postgres", "Supabase", "pgvector", "ClickHouse", "Redis", "Kafka"],
  },
  {
    group: "Deploy",
    items: ["Docker", "Linux servers", "Vercel", "Render", "GitHub Actions"],
  },
];

export const experience: Job[] = [
  {
    role: "AI Automation Engineer",
    company: "Devdabs (Hybrid)",
    dates: "February 2024 to Present",
    summary: "Built Calyx AI, a multi-tenant call-tracking analytics platform, end to end as its sole engineer, plus automation for client campaigns.",
  },
  {
    role: "Client projects",
    connector: "for",
    company: "US pay-per-call marketing agencies (through Devdabs)",
    dates: "March 2026 to October 2026",
    summary: "A reporting dashboard over 12 lead-capture tables and a Gmail-to-CRM lead pipeline; the final QA gate for landing pages going live (consent, tracking and claims checks).",
  },
  {
    role: "Partner project",
    connector: "with",
    company: "a software company",
    dates: "August 2026 to September 2026",
    summary: "Reviewed and improved a call tracking and routing platform, validating its routing model and caps.",
  },
  {
    role: "Freelance Automation Developer (part-time)",
    place: "Remote",
    dates: "January 2022 to January 2024",
    summary: "Small automations for small businesses: email and lead-capture workflows, WhatsApp auto-replies and CRM updates.",
    rebuilt: [{ before: "WhatsApp auto-replies (2022)", after: "WhatsApp Lead Qualifier", href: "#mini-whatsapp-lead-qualifier" }],
  },
  {
    role: "Field Operations Manager",
    company: "Ammarco Engineering Associates",
    place: "Islamabad",
    dates: "February 2019 to September 2022",
    summary: "Field operations and site supervision; built internal automations for administration (Gmail lead tracking and follow-up, a simple chatbot).",
    rebuilt: [{ before: "Gmail lead tracking (2019)", after: "Bilingual Email Triage", href: "#mini-bilingual-email-triage" }],
  },
];

// "What I can automate for your team": business wording, each backed by a project or mini automation on this site.
// Skills must be names from `skills`, so the Skills section can link back; industries only where a build shows them.
export type Service = { id: string; title: string; body: string; proof: Link[]; skills: string[]; industries: string[] };

export const services: Service[] = [
  {
    id: "lead-follow-up",
    title: "Lead follow-up",
    body: "Every enquiry sorted, saved once and followed up on time.",
    proof: [{ label: "n8n Lead Automation", href: "/projects/n8n-leads" }, { label: "WhatsApp Lead Qualifier", href: "#mini-whatsapp-lead-qualifier" }],
    skills: ["n8n", "Claude API", "Supabase", "Telegram Bot API", "Webhooks", "Scheduled jobs"],
    industries: ["Real estate"],
  },
  {
    id: "replies",
    title: "WhatsApp and email replies",
    body: "A first reply in the customer's language within seconds.",
    proof: [
      { label: "Bilingual Email Triage", href: "#mini-bilingual-email-triage" },
      { label: "WhatsApp Lead Qualifier", href: "#mini-whatsapp-lead-qualifier" },
      { label: "Appointment Reminders", href: "#mini-appointment-reminders" },
    ],
    skills: ["n8n", "WhatsApp Cloud API", "Gmail", "Claude API", "Telegram Bot API"],
    industries: ["Clinics", "Customer support"],
  },
  {
    id: "call-review",
    title: "Call quality review",
    body: "Every call checked against your checklist, with the proof.",
    proof: [{ label: "Call QA Agent", href: "/projects/call-qa-agent" }, { label: "Calyx AI", href: "/calyx" }],
    skills: ["Deepgram STT", "Claude API", "Structured JSON output", "LLM evals", "Python", "FastAPI", "Supabase"],
    industries: ["Call centers", "Insurance"],
  },
  {
    id: "document-answers",
    title: "Answers from company documents",
    body: "Policy answers in English or Arabic, with the source shown.",
    proof: [{ label: "Bilingual Document Assistant", href: "/projects/rag-mcp" }],
    skills: ["RAG", "pgvector", "MCP servers", "Claude API", "Python", "Postgres"],
    industries: ["HR", "Operations"],
  },
  {
    id: "reports",
    title: "Reports and alerts",
    body: "Yesterday's numbers and key changes in your chat each morning.",
    proof: [
      { label: "Daily Report Bot", href: "#mini-daily-report-bot" },
      { label: "Price Monitor", href: "#mini-price-monitor" },
      { label: "Calyx AI", href: "/calyx" },
    ],
    skills: ["n8n", "Supabase", "Telegram Bot API", "Python", "Scheduled jobs", "ClickHouse"],
    industries: ["Retail", "E-commerce"],
  },
  {
    id: "sync",
    title: "CRM and sheet syncing",
    body: "Leads and invoices kept in step, with problems flagged.",
    proof: [{ label: "Sheet to CRM Sync", href: "#mini-sheet-to-crm-sync" }, { label: "Invoice to Sheet", href: "#mini-invoice-to-sheet" }],
    skills: ["n8n", "Google Sheets", "HubSpot", "Python", "REST APIs"],
    industries: ["Finance", "Sales"],
  },
];

// Short explanations shown next to technical terms wherever a plain-language block has to name them.
export const glossary: Record<string, string> = {
  RAG: "answers from documents",
  pgvector: "search by meaning",
  "MCP servers": "tools for AI agents",
  "LLM evals": "measured tests of AI output",
  "Deepgram STT": "speech to text",
};

// Every service skill must be a listed skill (so the Skills section can link back); a typo fails the build.
for (const sv of services) {
  const known = new Set(skills.flatMap((g) => g.items));
  const unknown = sv.skills.filter((k) => !known.has(k));
  if (unknown.length) throw new Error(`site.ts: service "${sv.id}" uses unknown skills: ${unknown.join(", ")}`);
}

// The services that use at least one skill of a group (for the links in the Skills section).
export const servicesForGroup = (group: string) => {
  const items = new Set(skills.find((g) => g.group === group)?.items ?? []);
  return services.filter((sv) => sv.skills.some((k) => items.has(k)));
};

export const isSet = (value?: string): value is string => !!value && !value.startsWith("[");

export const emailHref = isSet(site.email) ? `mailto:${site.email}` : undefined;
export const linkedinHref = isSet(site.linkedin) ? site.linkedin : undefined;
export const githubHref = publicCode && isSet(site.github) ? site.github : undefined;
