# Hosting decision: Vercel and Supabase free tiers

Status: accepted, 7 November 2026 (Option B). Applies to `call-qa-agent` and `rag-mcp`. It replaces the Cloud Run decision of 6 November for these two apps; the Cloud Run steps stay available in [docs/cloud-run/](cloud-run/). `n8n-leads` was decided on 9 November 2026: Render's free plan plus a static page on Vercel (see [its section](#n8n-leads-render-free-plan-and-a-static-page)).

## Context

- Two FastAPI apps. Call QA: API, dashboard and upload page; an upload waits on Deepgram and an LLM, usually 5 to 60 seconds. RAG: question page, JSON API and an MCP server; an answer takes 2 to 8 seconds.
- Low traffic: portfolio visitors and occasional uploads by the owner. Both public demos are limited per visitor and per day.
- Audio files can be tens of MB. Data lives in Supabase, Frankfurt.
- Goal: $0 hosting with no card on file, so a mistake or abuse cannot run up a hosting bill, and nothing to operate.

## Options

Limits as listed by each provider in November 2026.

| | Option A: Cloud Run (previous decision) | Option B: Vercel Hobby + Supabase free |
| --- | --- | --- |
| Cost | $0 within the free tier, usage-billed above it | $0; Hobby and the Supabase free plan need no card |
| Billing risk | Needs a billing account; budgets alert but do not cap | None for hosting: plans stop at their limits instead of billing |
| Request body | 32 MiB (HTTP/1) | 4.5 MB per request to a function |
| Duration | Up to 60 minutes per request | 300 s per request (Hobby with Fluid compute) |
| Instances | Pinned to 1, so in-memory limits worked | Many, as traffic needs; state must be shared |
| Region | `europe-west1`, next to Frankfurt | `fra1` (Frankfurt), set per project |
| Operations | Secret Manager, revisions, a Dockerfile | Environment variables per project, deploys on every push, instant rollback |
| Use | Any | Hobby is for non-commercial use, which fits a portfolio |

Render Free (spins down and kills background work), Render Starter ($7 a month per service), Railway (usage credit) and Oracle Always Free (we would run the VM) were compared in the first version of this record; nothing about them changed.

## Decision

Option B. Each app is its own Vercel project (Root Directory `call-qa-agent` or `rag-mcp`): FastAPI on Vercel Functions with Fluid compute, region `fra1`, Python 3.12. Postgres, pgvector and audio storage on the Supabase free plan. Steps: [call-qa-agent/DEPLOY.md](../call-qa-agent/DEPLOY.md) and [rag-mcp/DEPLOY.md](../rag-mcp/DEPLOY.md).

What changed in the apps to fit the platform:

1. **Request body:** the Call QA upload page sends audio straight from the browser to a private Supabase Storage bucket with a signed upload URL (passcode-protected), and Deepgram fetches it with a short-lived signed download URL. Files up to 50 MB (the Supabase free plan limit per file) never pass through the function; bigger ones go by public URL as before.
2. **Duration:** `PROCESSING_MODE=inline` stays: transcription and scoring run inside the request, well under 300 s.
3. **Many instances:** the Call QA login rate limit (`login_failures`, `login_*` functions called through PostgREST) and the RAG demo limits and answer cache (`demo_requests`, `demo_answers`, `demo_take`) moved from memory to Postgres. `demo_take` checks and counts in one step under a transaction-scoped advisory lock, so concurrent instances cannot exceed the daily cap.
4. **Connections:** Call QA talks to Supabase over HTTP only. RAG uses the transaction pooler (port 6543), opens connections on demand (at most 3 per instance) and closes them after 30 idle seconds; prepared statements are off for the pooler.
5. **MCP:** the RAG project also serves the MCP server at `/mcp` over streamable HTTP: a public read-only demo without a token under the same per-visitor limits and daily cap, or private with a bearer token.

Trade-offs we accept:

- Hobby is non-commercial and has fixed limits; a paying client would move to Vercel Pro or back to a container.
- An inline upload holds its request until scoring finishes; a call that needs more than 300 s would fail (unlikely below 50 MB of audio).
- Supabase free projects pause after a week without activity and must be resumed from the dashboard.
- Per-visitor limits rely on Vercel's `x-real-ip`; hosted MCP clients share their servers' addresses. The daily cap is what bounds spending.
- Deepgram and Anthropic stay usage-billed. The RAG daily cap (about $1 a day) and a monthly spend limit in the Claude Console bound the LLM cost; Call QA uploads need the passcode.

## LLM provider

Production uses `LLM_PROVIDER=anthropic` with `claude-sonnet-5-5` ($2 per million input tokens, $10 per million output tokens, billed per call). Gemini stays an optional provider (`LLM_PROVIDER=gemini`), not the default, because:

- The AI Studio free tier allows 20 requests a day for `gemini-3.8-flash`. One full eval needs at least 10, so a single run uses half a day's quota, and a busy demo day would run out.
- At busy hours it returned HTTP 503 ("high demand") even after retries. Two scheduled eval runs on 6 November 2026 failed: the first on 503s, the second on the exhausted daily quota.
- On the free tier Google may use the content to improve its products, so a free Gemini key only processes synthetic demo data.

A paid Gemini tier would remove the first and third points; we would compare it on cost and accuracy before switching.

## n8n-leads: Render free plan and a static page

Status: accepted, 9 November 2026. Steps: [n8n-leads/DEPLOY.md](../n8n-leads/DEPLOY.md).

n8n needs a long-running process for its editor and its webhooks, which Vercel Functions are not. The workflows already start from webhooks only, with the schedules called from outside (GitHub Actions), so a host that sleeps when idle works.

Decision: n8n on a Render free web service (`n8n-leads/render.yaml`), with its own tables in a separate `n8n` schema of the existing Supabase database and a fixed `N8N_ENCRYPTION_KEY`, so a sleep, restart or redeploy loses nothing. The demo page is static on Vercel (`n8n-leads/landing`) and wakes n8n as soon as it opens. The workflow runs in demo mode: emails are shown on the page instead of sent, submissions are capped per visitor and per day in Postgres, and demo Telegram alerts are marked and capped at 20 a day.

| | Render free (chosen) | Render Starter | n8n Cloud | Cloud Run, min 0 |
| --- | --- | --- | --- | --- |
| Cost | $0, no card | $7 a month | About EUR 24 a month after the trial | $0 within the free tier, needs a billing account |
| Idle | Sleeps after 15 minutes | Always on | Always on | Scales to zero |
| First request after idle | About 2 to 3 minutes (measured below) | Immediate | Immediate | Tens of seconds |
| Limits | 512 MB, 0.1 CPU, 750 free instance hours a month | 512 MB, 0.5 CPU | 2,500 executions a month | Usage billed above the free tier |

Measured in a container with the free plan's limits (512 MB, hard 0.1 CPU) on Postgres 17 with the `n8n` schema: `/healthz` answers after about 80 s, the webhooks are registered after about 2 minutes, the first lead is answered after about 3 minutes, then about 4 s per lead (1 to 2 s on a normal CPU). Two settings were needed to make it work at all: heap limits for n8n and its Code runner (without them n8n was killed at 512 MB), and a longer token lifetime for the Code runner (on 0.1 CPU it started after its 30 s token had expired, so Code nodes could not run). Render may give more CPU in bursts, so real starts can be faster.

Trade-offs we accept:

- **Cold start.** A visitor who arrives after a quiet spell waits 2 to 3 minutes for the first answer. The page wakes n8n on load, retries while n8n registers its webhooks, waits up to 5 minutes and says plainly what is happening. Every answer after that takes seconds.
- **750 free hours a month.** One service running all month needs 744, so the hours never run out; a sleeping service uses fewer. A second free service in the same Render workspace would share them.
- **Schedules wake it.** Each scheduled call usually finds n8n asleep and waits for it, about 3 Actions minutes per call on a private repository. Hourly that would be roughly 2,200 minutes a month, more than the 2,000 free. In demo mode the follow-up never emails anyone (demo leads are excluded), so the follow-up runs every 6 hours (`7 */6 * * *`): with the daily summary about 450 minutes a month. A paying client on an always-on host would go back to hourly.
- **Uptime.** The uptime check loads the static page only. A sleeping n8n is expected and must not raise an alert.
- **Database certificate.** n8n connects to the Supabase pooler with TLS but does not verify Supabase's own certificate authority (`DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=false`). The CA is committed and the steps to turn the check on, with a pre-check and a one-field rollback, are in [n8n-leads/DEPLOY.md](../n8n-leads/DEPLOY.md#check-the-database-certificate).
- **Shared database.** n8n's tables count toward the Supabase free plan's 500 MB; execution history is kept for a week and successful runs are not stored.

## What would make us change

- **Commercial use:** Hobby no longer applies. Then Vercel Pro, or the container on Cloud Run.
- **Long calls:** recordings that need more than 300 s to transcribe and score, or files over 50 MB becoming common. Then background processing with a queue, or Cloud Run.
- **Free plan limits:** Supabase storage (1 GB) or database (500 MB) filling up, or the inactivity pause becoming a problem. Then the Supabase Pro plan.
- **Provider changes:** Vercel, Supabase or Render change the free plans or limits.
- **n8n for a real client:** a sleeping host is fine for a demo, not for leads that must be answered in seconds. Then Render Starter, Railway or n8n Cloud.

## Sources

- Vercel FastAPI: https://vercel.com/docs/frameworks/backend/fastapi
- Vercel Python runtime (versions, dependencies): https://vercel.com/docs/functions/runtimes/python
- Vercel Functions limits (4.5 MB body, durations): https://vercel.com/docs/functions/limitations
- FastAPI lifespan events on Vercel: https://vercel.com/changelog/fastapi-lifespan-events-are-now-supported-on-vercel
- Vercel Hobby plan: https://vercel.com/docs/plans/hobby
- Supabase Storage file limits: https://supabase.com/docs/guides/storage/uploads/file-limits
- Supabase connection pooling: https://supabase.com/docs/guides/database/connecting-to-postgres
- Cloud Run pricing and limits (Option A): https://cloud.google.com/run/pricing, https://cloud.google.com/run/quotas
- Render free instances (sleep after 15 minutes, 750 hours a month): https://render.com/docs/free
- n8n database settings (Postgres schema, SSL): https://docs.n8n.io/hosting/configuration/environment-variables/database/
