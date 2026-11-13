// Calyx AI case study content: the /calyx page and the PDF are rendered from this.
// No client names or code. The screens are from the running system with financial figures blurred.

export const calyx = {
  title: "Calyx AI",
  subtitle: "Multi-tenant call analytics for pay-per-call networks",
  plain: {
    steps: [
      "Calls are pulled in automatically from each call-tracking platform the business uses.",
      "Duplicates are removed and everything lands in one dashboard: calls, payouts and conversions.",
      "The team can mark a sale on the platform from the dashboard and ask questions about the numbers in plain English.",
    ] as [string, string, string],
    why: "One reliable view of the business instead of exporting reports from several platforms by hand.",
  },
  faq: [
    {
      q: "How do you avoid counting the same call twice?",
      a: "Each call's content is hashed (SHA-256) and the hash is kept in Redis per tenant, platform and call; unchanged calls are suppressed, changed ones publish an update. Moving the analytics table to ClickHouse ReplacingMergeTree removed about 1.4 million duplicate rows (31%).",
    },
    {
      q: "How do you stay within each platform's API limits?",
      a: "Sync runs per account with a two-window rate limit, a deep-page backfill and incremental refreshes after that, so a large account can't starve the others.",
    },
    {
      q: "How do you make write-back to a platform safe?",
      a: "It always uses the calling tenant's own credentials, is idempotent, keeps an audit trail and reads the result back to confirm it. It sits behind a two-level enable flag, a campaign allowlist and a cap on the amount; the convert loop is proven live on Ringba.",
    },
    {
      q: "How do you keep AI costs predictable?",
      a: "Transcription and scoring run only on converted calls, never on the backfill, with a daily cap on transcribed minutes per company, and both sit behind feature flags.",
    },
    {
      q: "How did you know each part really worked?",
      a: "Each phase had written acceptance criteria and was only marked done with live evidence: API probes, data movement confirmed, screenshots. Anything not verified stayed labelled unknown.",
    },
    {
      q: "What would you do differently?",
      a: "Write the canonical hash rules into a shared spec from day one. The webhook and API-sync paths first hashed the same call differently, which kept republishing it until path-specific fields were excluded.",
    },
  ],
  facts: [
    ["Role", "Sole engineer, end to end: research, architecture, specs, build, live verification and deployment, while working at Devdabs."],
    ["Stack", "Go services, a TypeScript API, a Next.js dashboard, ClickHouse, PostgreSQL, Redis, Kafka, Docker Compose on a Linux server; Deepgram and OpenAI for the AI layer."],
    ["Status", "Deployed to production, running on real call data."],
  ] as [string, string][],
  numbers: [
    ["2", "call-tracking platforms certified live, with adapters for two more"],
    ["2,000+", "calls a day across two live accounts, after a 30-day backfill"],
    ["31%", "duplicate rows removed from an analytics table (about 1.4 million)"],
    ["~19 s", "to copy and OPTIMIZE 4.6 million rows into the deduplicating table"],
  ] as [string, string][],
  problem:
    "Pay-per-call networks run their campaigns on several call-tracking platforms. Each platform has its own API, data model and limits. Teams were exporting reports by hand and still could not see one reliable view of calls, payouts and conversions, or act on a call without logging into each platform.",
  built: [
    {
      title: "Platform integrations",
      body: "Ringba and Retreaver are certified live; TrackDrive and CallGrid have adapters. API sync runs per account with a two-window rate limit, deep-page backfill and incremental refresh.",
    },
    {
      title: "Clean data at volume",
      body: "A shared deduplication module keeps a SHA-256 hash of each call's content in Redis, keyed by tenant, platform and call. Unchanged calls are suppressed and changed calls publish an update. If Redis fails, it fails open: a possible duplicate is better than silently dropping data. Redis runs with bounded memory (1 GB, volatile-ttl eviction), so the dedup keys stay flat when accounts re-sync.",
    },
    {
      title: "Analytics store",
      body: "ClickHouse, with the main analytics table moved to ReplacingMergeTree to remove duplicate rows produced by enrichment. The table went from about 4.6 million to 3.2 million rows: about 1.4 million duplicates (31%) gone. Copying the 4.6 million rows and running OPTIMIZE took about 19 seconds.",
    },
    {
      title: "Write-back",
      body: "Convert and void calls on the source platform directly from Calyx AI. Requests come in on the TypeScript API, which forwards them to the Go platform-sync service, and platform sync calls the platform, with tenant isolation (each tenant's own credentials), idempotency, an audit trail and read-back confirmation. The convert loop is proven live on Ringba: a call converted from Calyx read back with hasConverted=true and the amount. It sits behind a two-level enable flag and a campaign allowlist, with a cap on the amount.",
    },
    {
      title: "AI layer",
      body: "Call transcription with Deepgram, which runs inside the platform-sync service, and call scoring and Ask AI with OpenAI, called from the scoring and ask-ai services. Both are built and deployed behind feature flags. Cost rules keep spend predictable: only converted calls are processed, never the backfill, and a daily cap on transcribed minutes.",
    },
    {
      title: "Coverage map",
      body: "A North America projection with US state and Canadian province normalization.",
    },
  ],
  howIWorked:
    "I ran the build as a phase-gate process. I wrote the plan and acceptance criteria for each phase, and nothing was marked done without live evidence: API probes, data movement confirmed, screenshots. Items that were not verified stayed labelled unknown.",
  hardProblems: [
    {
      title: "Ping-pong republishing",
      body: "The webhook and API-sync paths produced different hashes for the same call, so the same call kept being republished. Fixed by excluding path-specific fields from the canonical hash.",
    },
    {
      title: "Sticky conversion flag",
      body: "The platform's converted flag stays true after a void. A reliable conversion state needs the flag and an amount greater than zero.",
    },
    {
      title: "Credential leak surface",
      body: "Caught a metric that could expose tokens inside error strings, and reverted it before release.",
    },
    {
      title: "Multi-tenant safety",
      body: "Write-back always uses the calling tenant's own credentials, never a shared integration row.",
    },
  ],
  results: [
    "Ringba and Retreaver certified live; TrackDrive and CallGrid adapters built.",
    "2,000+ calls a day across two live accounts, after a 30-day backfill.",
    "About 1.4 million duplicate rows (31%) removed from an analytics table; the 4.6 million row copy and OPTIMIZE took about 19 seconds.",
    "Convert write-back proven live on Ringba, behind enable flags, an allowlist and an amount cap.",
    "Deployed and operated by me with Docker Compose on a Linux server.",
  ],
  note: "Client names and code are left out, and financial and account figures in the screens are blurred. Figures come from the running system.",
  // The PDF has no screens, so its note says where they are.
  printNote: "Client names and code are left out. Screens from the running system, with financial and account figures blurred, are on the web version of this case study. Figures come from the running system.",
  pdf: "/calyx-ai-case-study.pdf",
  // Screens from the running system; revenue and account figures are blurred in the images themselves.
  screens: [
    {
      large: "/calyx/dashboard-1440.webp", small: "/calyx/dashboard-720.webp", width: 1440, height: 760,
      caption: "Operations overview",
      alt: "Calyx AI operations overview in dark mode: cards for total revenue, total pings, average call score and compliance exposure (figures blurred), an inbound ping chart for the last 7 days and a platform mix panel.",
    },
    {
      large: "/calyx/ask-ai-800.webp", small: "/calyx/ask-ai-720.webp", width: 800, height: 562,
      caption: "Ask AI",
      alt: "Calyx AI Ask AI screen: suggested questions such as revenue by platform and lowest publisher conversion, answers marked From Calyx data with the date range and the query behind each number (figures blurred), and a history panel.",
    },
    {
      large: "/calyx/coverage-1440.webp", small: "/calyx/coverage-720.webp", width: 1440, height: 900,
      caption: "Coverage map",
      alt: "Calyx AI coverage map of the United States and Canada shaded by call volume across 56 regions, with California selected and its figures blurred.",
    },
  ],
};
