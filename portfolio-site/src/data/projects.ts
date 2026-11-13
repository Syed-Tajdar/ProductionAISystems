import { inRegionOrder } from "./regions";
import { codeLink, demos } from "./site";

// Content of the /projects/<slug> pages, taken from each project's README. Figures are from the
// recorded eval runs; each page keeps the README's scope notes next to them.

export type Step = { title: string; body: string };
export type Plain = { steps: [string, string, string]; why: string }; // "In plain words", for a non-technical reader
export type Faq = { q: string; a: string; plain?: boolean }; // plain: inside the plain-language check

export type ProjectPage = {
  slug: string;
  title: string;
  subtitle: string;
  metaTitle: string;
  metaDescription: string;
  keywords: string[];
  languages: string[]; // programmingLanguage in JSON-LD
  plain: Plain;
  faq: Faq[]; // "Questions you might ask", also FAQPage JSON-LD
  facts: [string, string][];
  numbers: [string, string][];
  problem: string;
  flow: Step[]; // architecture, left to right
  guards: string[]; // what happens when something fails
  decisions: Step[];
  resultsIntro: string;
  results: [string, string][] | { head: string[]; rows: string[][] };
  scope: string[];
  code?: string; // undefined while the repository is private (see publicCode in site.ts)
  readme?: string;
  demo?: string; // live demo URL, from site.ts
};


export const projectPages: ProjectPage[] = [
  {
    slug: "call-qa-agent",
    title: "Call QA Agent",
    subtitle: "Scores every sales and support call against a rubric and quotes the line behind each verdict.",
    metaTitle: "Call QA Agent: AI call scoring with quoted evidence",
    metaDescription:
      "Call QA automation with Deepgram and Claude: every call scored against a rubric, each verdict backed by a checked quote. 67/67 items correct in the eval.",
    keywords: ["call QA automation", "call center quality assurance", "Claude", "Deepgram", "LLM evals", "FastAPI"],
    languages: ["Python"],
    plain: {
      steps: [
        "A sales or support call recording comes in.",
        "It is turned into text, with who said what, and checked against your quality checklist.",
        "Each check shows pass or fail with the exact sentence behind it; anything uncertain goes to a person.",
      ],
      why: "You see problems in every call, not only in the few calls someone has time to listen to, and every result can be checked in seconds.",
    },
    faq: [
      {
        q: "How do you stop the AI from making up a verdict?",
        a: "Every pass or fail has to quote the transcript, and I check that the quote is really there, ignoring case and punctuation. If it isn't, the item is marked for review with the model's verdict kept, and a critical item in review makes the call Undecided, never Pass.",
      },
      {
        q: "What does it cost per call?",
        a: "Scoring averaged $0.0102 per call in the eval, at list price ($0.0092 to $0.0108). Speech-to-text is billed separately by Deepgram per audio minute and is not in that figure. Every score row stores the model, tokens, latency and cost, so spend is visible per call.",
      },
      {
        q: "Where does call data go, and who can see it?",
        a: "Audio goes to Deepgram for transcription and the transcript to the scoring model; everything is stored in the app's own Supabase project. Recordings sit in a private Supabase Storage bucket and play through signed links that expire after 5 minutes. The dashboard needs a passcode; the public demo shows only synthetic calls.",
      },
      {
        q: "What happens when Deepgram or the model fails?",
        a: "Calls are retried with backoff. A call that still fails is marked failed with the reason, and no partial score is stored, so nothing half-scored shows up as a result.",
      },
      {
        q: "Would it handle thousands of calls a day?",
        a: "On Vercel each upload is transcribed and scored inside its request. On an always-on container it replies at once and works in the background (PROCESSING_MODE=background). For thousands of calls a day I would put a queue in front so provider rate limits are respected.",
      },
      {
        q: "What would you do differently?",
        a: "Test on real recorded calls with human-labelled verdicts. The 67 of 67 result comes from 10 scripted calls written with the rubrics, so it shows the pipeline and the evidence checks work, not accuracy on real calls.",
      },
    ],
    facts: [
      ["Stack", "Python 3.12, FastAPI, Deepgram nova-3, Claude (Gemini and OpenAI as options), Supabase Postgres and Storage, Vercel Functions."],
      ["Status", "Live on Vercel with a public, read-only demo on synthetic calls."],
    ],
    numbers: [
      ["67/67", "rubric items matched the expected verdict on 10 test calls"],
      ["10/10", "calls got the expected pass or fail result"],
      ["91%", "of speech time attributed to the right speaker (8 audio calls)"],
      ["$0.0102", "average LLM cost per scored call, at list price"],
    ],
    problem:
      "Call centers review a small sample of calls by hand, so a missed disclosure or a misleading claim can go unnoticed. Reviewers also need to see why a call failed, not only a score.",
    flow: [
      { title: "Upload", body: "Audio file or URL to POST /calls, behind a passcode." },
      { title: "Transcribe", body: "Deepgram nova-3 with diarization and brand keyterms; utterances stored in Supabase." },
      { title: "Score", body: "Claude Sonnet with a versioned YAML rubric. JSON schema output: verdict, utterance and quote per item." },
      { title: "Verify", body: "Every quote must exist in the stored transcript, or the item becomes needs_review." },
      { title: "Review", body: "Dashboard with each quote highlighted in its line and review flags in amber." },
    ],
    guards: [
      "A critical item that needs review makes the result Undecided, never Pass.",
      "Deepgram and LLM errors are retried with backoff; a call that still fails is marked failed with the reason, and no partial score is stored.",
      "Each score row stores the model, tokens, latency and cost, and points at the exact rubric version.",
    ],
    decisions: [
      {
        title: "Evidence or it does not count",
        body: "The model must quote the transcript for every pass or fail. Quotes are matched ignoring case and punctuation; an unmatched quote is stored as needs_review with the model's verdict kept, so a reviewer sees it instead of a confident wrong answer.",
      },
      {
        title: "Versioned rubrics",
        body: "Rubrics are YAML with weights, critical flags and pass, fail and n/a rules. They are validated and stored once per version, so every score can be traced to the rubric it used.",
      },
      {
        title: "One interface, three providers",
        body: "Claude, Gemini and OpenAI sit behind one client, switched with LLM_PROVIDER. Production uses Claude Sonnet, because the Gemini free tier allowed 20 requests a day and returned 503s at busy hours.",
      },
      {
        title: "Serverless without lost work",
        body: "Vercel Functions with Fluid compute on free tiers. A function's work ends with its response, so uploads are transcribed and scored inside the request. Large recordings go from the browser straight to Supabase Storage with a signed URL, so the 4.5 MB request limit never applies, and the login limit lives in Postgres so it holds across instances.",
      },
      {
        title: "Closed by default",
        body: "The passcode is compared in constant time, sessions are signed cookies, failed logins are rate limited, and the public demo mode only shows synthetic demo calls. Without a configured passcode every protected route answers 503.",
      },
    ],
    resultsIntro: "Eval run on 11 November 2026 on the audio files the demo plays: claude-sonnet-5-5 (effort medium) and Deepgram nova-3. The run on the current scoring prompt, with the rows kept in memory, so the latencies leave out database writes.",
    results: [
      ["Item accuracy", "67/67 (100%)"],
      ["Critical-item accuracy", "25/25 (100%)"],
      ["Pass/fail accuracy", "10/10 (100%)"],
      ["needs_review rate", "0/67 (0%)"],
      ["Diarization accuracy (8 audio calls)", "91%"],
      ["Evidence on the right line", "48/56 (86%)"],
      ["Average transcription latency (8 audio calls)", "0.8 s"],
      ["Average scoring latency", "4.5 s"],
      ["Average LLM cost per call ($2 / $10 per million tokens)", "$0.0102 ($0.0092 to $0.0108)"],
    ],
    scope: [
      "These are 10 scripted calls written alongside the rubrics and expected verdicts. The score shows that the pipeline, prompts and evidence checks work end to end; it does not measure accuracy on real calls. A run earlier the same day scored 66 of 67: the item that differed was a contact check on a call where speech recognition misheard a spelled-out email address, so that item can go either way.",
      "The two mixed Arabic and English calls were scored from the script text with real line timings, so transcribing mixed Arabic and English audio is not measured here. Real recordings were not part of this test.",
      "For 4 of 56 agreed verdicts the quote came from a different line than the expected one; the quotes were in the transcript and the verdicts were right.",
    ],
    code: codeLink("call-qa-agent"),
    readme: codeLink("call-qa-agent/README.md"),
    demo: demos["call-qa-agent"],
  },
  {
    slug: "n8n-leads",
    title: "n8n Lead Automation",
    subtitle: "Classifies every web lead with an LLM, alerts sales on hot leads and follows up on its own, in English and Arabic.",
    metaTitle: "n8n Lead Automation with Claude and Supabase",
    metaDescription:
      "n8n workflows that classify web leads with Claude, deduplicate them in Supabase, alert sales on Telegram and follow up in English and Arabic.",
    keywords: ["n8n", "lead automation", "AI lead qualification", "Claude", "Supabase", "Telegram", "Arabic"],
    languages: ["JavaScript", "SQL", "Python"],
    plain: {
      steps: [
        "Someone fills in the enquiry form on your website.",
        "Within about two seconds the enquiry is sorted by urgency, saved once even if the person writes twice, and hot ones go straight to sales.",
        "If nobody has replied after a day, a polite follow-up goes out in the person's language.",
      ],
      why: "Fast replies win enquiries. Nothing gets lost, spam is kept away from sales, and no one is emailed twice.",
    },
    faq: [
      {
        q: "What if the AI sorts a lead wrongly or doesn't answer?",
        a: "It must answer in a fixed JSON format. If it errors, refuses or returns invalid JSON, the lead is saved as unclassified and sales still get the alert, so a model problem never loses a lead. An existing lead keeps its earlier classification.",
      },
      {
        q: "Can someone trick it through the form?",
        a: "The form text is treated as data, not instructions. In the live test, a message telling the model to rate it hot came back as info and cold. The auto-reply never repeats the person's message, so the form can't be used to send arbitrary text to someone else.",
      },
      {
        q: "How do you avoid duplicate leads?",
        a: "One database function looks the lead up by phone, then email, and updates or inserts it in a single transaction. Unique indexes catch two submissions arriving at the same moment, and the second one retries as an update.",
      },
      {
        q: "What happens if Telegram, email or the database is down?",
        a: "If Telegram or email fails, the lead stays saved and the failure is logged. If the database fails, ops get the contact details on Telegram and the form shows an error, so the enquiry isn't lost. A separate error workflow posts any failed run to ops.",
      },
      {
        q: "How are the follow-ups scheduled?",
        a: "A GitHub Actions schedule calls a protected webhook every 6 hours. The demo host sleeps between calls, so each call wakes it; an always-on host could run it hourly. It claims up to 20 leads older than a day under a row lock, so a late or repeated call emails nobody twice; a failed email is retried on the next run, at most 3 times.",
      },
      {
        q: "What would you do differently?",
        a: "Measure on real enquiries. The 5 of 5 result is a small live check of the prompt and format on synthetic leads, not an accuracy benchmark. I would also add email intake through Gmail.",
      },
      {
        q: "Why doesn't the public demo send real emails?",
        a: "Because an open form could be used to send emails to anyone's address: type a stranger's email and my system would write to them. So the demo shows the exact email it would send, on the page, instead of sending it. In production the same workflow sends it through SMTP, and that step is tested on the test bench against a test mail server, including an address the server rejects.",
        plain: true,
      },
    ],
    facts: [
      ["Stack", "n8n 2.45, Claude Haiku (Gemini as an option), Supabase Postgres, Telegram, SMTP email, GitHub Actions for the schedules, Docker."],
      ["Status", demos["n8n-leads"]
        ? "Live: a public demo form on Vercel, with n8n on Render's free plan. Four workflows: intake, follow-up, daily summary and errors."
        : "Four workflows (intake, follow-up, daily summary and errors), tested end to end on a test bench."],
    ],
    numbers: [
      ["1.8 to 3.0 s", "per submission end to end, with the real Claude API"],
      ["5/5", "synthetic test leads classified as expected, including Arabic and spam"],
      ["1 transaction", "to deduplicate by phone, then email, safe against two racing submissions"],
    ],
    problem:
      "Leads from web forms go cold when nobody replies fast, and sales teams lose time on spam and on people who already enquired.",
    flow: [
      { title: "Form", body: "The landing page posts to an n8n webhook; a honeypot field catches bots." },
      { title: "Normalize", body: "Phones to E.164 (including Arabic-Indic digits), lowercase email, UTM tags kept; no phone or email answers 422." },
      { title: "Classify", body: "Claude Haiku with a JSON schema: intent, urgency, language, a one-line summary and the reason for the urgency." },
      { title: "Save", body: "upsert_lead() in Supabase: dedupe by phone, then email, and log the event in one transaction." },
      { title: "Act", body: "Telegram alert for hot leads, auto-reply email in English or Arabic, then the form gets its answer." },
    ],
    guards: [
      "LLM error, refusal or invalid JSON: the lead is saved as unclassified and still alerted.",
      "Telegram or email fails: the lead stays saved and the failure is logged; Supabase fails: ops get the contact details on Telegram and the form shows an error.",
      "Follow-ups and the daily summary start from a scheduled GitHub Actions workflow with a secret header; a row lock means no lead is emailed twice.",
    ],
    decisions: [
      {
        title: "Webhook in, answer last",
        body: "Every workflow starts from a webhook and finishes its work before it responds, so n8n can run on a host that scales to zero. Scheduled work is a webhook that a GitHub Actions schedule calls with a shared secret header.",
      },
      {
        title: "Dedupe in the database",
        body: "One SQL function looks the lead up by phone, then by email, updates or inserts it and logs the event in one transaction. Partial unique indexes catch two racing submissions, and the second one retries as an update.",
      },
      {
        title: "A failed LLM never loses a lead",
        body: "Any LLM failure stores the lead as unclassified with the error in its event log, and sales still get the alert. An existing lead keeps its previous classification.",
      },
      {
        title: "Prompt injection tested",
        body: "The prompt treats the form text as data, not instructions. In the live test, a message telling the model to rate it hot came back as info and cold.",
      },
      {
        title: "Auto-replies that cannot be abused",
        body: "The reply never echoes the lead's message and uses the first name only when it looks like a name, so the public form cannot send arbitrary text to someone else's address.",
      },
    ],
    resultsIntro: "Measured on 11 November 2026 on the test bench with the real Claude API (claude-haiku-5-5); Supabase and Telegram were mocked. One run of five synthetic leads.",
    results: [
      ["Leads classified", "5 of 5"],
      ["Time per submission, end to end", "1.8 to 3.0 s"],
      ["English rental lead", "hot, and the only one alerted"],
      ["Arabic lead", "language ar, intent buy"],
      ["SEO sales pitch", "spam"],
      ["Message telling the model to rate it hot", "info, cold"],
    ],
    scope: [
      "Five synthetic leads; a small live check of the prompt and schema, not an accuracy benchmark.",
      "The workflow tests run against mocks of Supabase, Claude, Gemini, Telegram and SMTP, including LLM errors, rate limits, refusals, invalid or cut-off JSON and failed alerts and emails. The SQL tests run on Postgres 17, including a forced race between two sessions.",
      "Email intake through Gmail push notifications is not part of this build.",
      "Public demo: 5 enquiries per 10 minutes per visitor and 100 a day; emails are shown instead of sent, demo alerts are marked and capped at 20 a day, and demo leads are deleted after 7 days.",
    ],
    code: codeLink("n8n-leads"),
    readme: codeLink("n8n-leads/README.md"),
    demo: demos["n8n-leads"],
  },
  {
    slug: "rag-mcp",
    title: "Bilingual Document Assistant",
    subtitle: "Answers questions from company documents in English and Arabic, with a checked quote behind every statement. RAG on a web page and over MCP.",
    metaTitle: "Bilingual Arabic and English RAG + MCP server",
    metaDescription:
      "Arabic and English RAG with pgvector hybrid search and Claude: every statement cites a checked quote, also served as a read-only MCP server.",
    keywords: ["Arabic RAG", "bilingual RAG", "MCP server", "pgvector", "hybrid search", "Claude", "citations"],
    languages: ["Python", "SQL"],
    plain: {
      steps: [
        "Your policies, guides and FAQs are loaded once, in English and Arabic.",
        "Someone asks a question in either language.",
        "They get a short answer where every sentence shows the passage it came from, or a clear \"not in the documents\".",
      ],
      why: "Staff stop searching through PDFs, and every answer can be checked against the source before anyone acts on it.",
    },
    faq: [
      {
        q: "How do you stop it from making things up?",
        a: "Each statement must cite a passage with an exact quote of 8 or more characters, and I check the quote is in that passage, with Arabic spelling variants folded. Statements without a valid quote are dropped; if nothing is left, the answer is \"not in the documents\". In the eval, 67 of 67 citations were valid and 5 of 5 out-of-scope questions were refused.",
      },
      {
        q: "What does a question cost?",
        a: "$0.0064 on average at list price in the eval, including about $0.00005 for translating the question for the second keyword search. Repeated questions come from a cache, and the public demo has a daily cap of 150 questions, which bounds spend at about $1 a day.",
      },
      {
        q: "How does it find Arabic answers to an English question?",
        a: "Search by meaning works across languages, and keyword search runs on the question and on its translation, so a clause that exists only in Arabic is still found. That translation step fixed both cross-language misses of the first eval run.",
      },
      {
        q: "Where do questions and documents go?",
        a: "Questions go to Anthropic for the answer and to Google for the search vector; the page says so. Documents and vectors live in Postgres with row level security on and no public access. The public demo uses synthetic documents of a fictional company, and private deployments put the MCP endpoint behind a bearer token.",
      },
      {
        q: "What happens when something fails?",
        a: "If the translation fails, search continues without it. If the answer service is down, the page says so in plain words. If the demo limits can't be checked, the question is refused rather than answered uncounted.",
      },
      {
        q: "What would you do differently?",
        a: "Test on a held-out question set and real documents. The current numbers come after fixing two weaknesses on the same 32 questions, written by the author of the documents, so they are optimistic.",
      },
    ],
    facts: [
      ["Stack", "Python 3.12, FastAPI, Postgres with pgvector and full-text search, Gemini embeddings, Claude Sonnet for answers and Haiku for query translation, MCP Python SDK, Vercel Functions."],
      ["Status", "Live on Vercel with a public demo page."],
    ],
    numbers: [
      ["26/27", "answerable questions with every expected section in the top 5 (96%)"],
      ["67/67", "citations whose quote was found in the cited passage"],
      ["5/5", "out-of-scope questions answered with \"not in the documents\""],
      ["$0.0064", "average cost per question, at list price"],
    ],
    problem:
      "Staff and customers need answers from policies, manuals and FAQs in Arabic and English, with sources they can check. AI agents need the same answers through a tool interface, without write access.",
    flow: [
      { title: "Ingest", body: "PDF, DOCX and Markdown parsed with headings, pages and tables; chunked by section; embeddings cached by content hash." },
      { title: "Retrieve", body: "One SQL function: vector search, full-text search and a translated full-text search, merged by reciprocal rank fusion." },
      { title: "Answer", body: "Claude Sonnet with a JSON schema: statements, each with a source number and an exact quote." },
      { title: "Verify", body: "Each quote must appear in the cited chunk, with Arabic spelling variants folded; unsupported statements are dropped." },
      { title: "Serve", body: "A web page with right-to-left Arabic answers, and a read-only MCP server over stdio or HTTP." },
    ],
    guards: [
      "Nothing left after the check: the answer is \"The documents do not contain an answer to this question.\", in the question's language.",
      "Public demo: 5 questions per 10 minutes per address and 150 a day; repeated questions come from a cache.",
      "If the query translation fails, search continues without it.",
    ],
    decisions: [
      {
        title: "Hybrid retrieval",
        body: "Exact terms such as \"returned cheque\" or \"AED 500\" come from keyword search, paraphrases from vectors. One SQL function merges both lists with reciprocal rank fusion and caps results at 3 chunks per document.",
      },
      {
        title: "Retrieval across languages",
        body: "Keyword search only matches chunks in the question's language, so each question is also translated by Claude Haiku (about $0.00005) and searched as a third list. This fixed both cross-language misses of the first eval run.",
      },
      {
        title: "Citations or nothing",
        body: "A citation counts only if its quote of 8 or more characters appears in the retrieved chunk it names. Statements without one are dropped, and a question with nothing left gets a plain \"not in the documents\".",
      },
      {
        title: "Arabic handled properly",
        body: "Quote checks fold alef forms, taa marbuta, alef maqsura, hamza seats, diacritics, tatweel and Arabic-Indic digits. Answers come back with a direction, and the page sets right to left per block.",
      },
      {
        title: "Read-only MCP server",
        body: "Three tools (search_documents, ask, list_documents) with typed input and output schemas and read-only hints. On the web deployment it is a public demo endpoint under the same per-visitor limits and daily cap as the page, kept in Postgres so they hold across serverless instances; private deployments require a bearer token instead.",

      },
    ],
    resultsIntro:
      "Eval on 6 November 2026: answers by claude-sonnet-5-5, judge claude-opus-5-5, embeddings gemini-embedding-2. The first run is before the cross-language and per-document fixes; the second is the current code.",
    results: {
      head: ["Metric", "First run", "Current"],
      rows: [
        ["Retrieval hit rate (every expected section in the top 5)", "23/27 (85%)", "26/27 (96%)"],
        ["Top result from an expected section", "20/27 (74%)", "25/27 (93%)"],
        ["Statements fully supported by their sources (judge)", "60/60 (100%)", "67/67 (100%)"],
        ["Key facts covered", "44/50 (88%)", "49/50 (98%)"],
        ["Citation validity", "60/63 (95%)", "67/67 (100%)"],
        ["Out-of-scope questions refused", "5/5", "5/5"],
        ["In-scope questions wrongly refused", "2/27", "0/27"],
        ["Answer in the question's language", "32/32", "32/32"],
        ["Average latency per question", "2.9 s", "3.6 s"],
        ["Average cost per question", "$0.0060", "$0.0064"],
      ],
    },
    scope: [
      "32 questions over 8 short synthetic documents, written by the same author as the documents, so their wording is closer to the source text than real users' would be.",
      "The first run was used to find two retrieval weaknesses and the current numbers are after fixing them on the same questions, so they are optimistic; a held-out question set and real documents would give lower numbers.",
      "Faithfulness comes from one LLM judge that checks statements against the sources they cite. Every number is from a single run.",
      "The remaining miss is a two-document question that gets only the reporting part: the English and Arabic copies of the tenant handbook take five of the top places, and the English stemmer keeps \"complain\" and \"complaint\" apart.",
    ],
    code: codeLink("rag-mcp"),
    readme: codeLink("rag-mcp/README.md"),
    demo: demos["rag-mcp"],
  },
];

export const projectPage = (slug: string) => projectPages.find((p) => p.slug === slug);

// The same order as the project cards for the selected region.
export const orderedProjectPages = inRegionOrder(projectPages, (p) => p.slug);
