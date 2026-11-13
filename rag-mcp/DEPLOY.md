# Deploying to Vercel

One Vercel project serves the question page, the JSON API and the MCP server (streamable HTTP at `/mcp`): FastAPI on Vercel Functions with Fluid compute, on the free Hobby plan. Documents, chunks, embeddings and the demo limits live in Supabase Postgres with pgvector (free plan, Frankfurt); the function runs in Frankfurt too (`fra1`, set in `vercel.json`). Why this setup: [docs/hosting-decision.md](../docs/hosting-decision.md). The Cloud Run alternative: [docs/cloud-run/rag-mcp.md](../docs/cloud-run/rag-mcp.md).

How the app fits the platform:

- **Many instances:** the per-visitor limit, the daily cap and the answer cache are in Postgres (`demo_requests`, `demo_answers`, the `demo_take` function), so they hold however many instances Vercel runs. `demo_take` checks and counts a question in one locked step, so two instances cannot both take the last question of the day.
- **Short connections:** the app connects through Supabase's transaction pooler, opens connections only when a request needs one (at most 3 per instance) and closes them after 30 idle seconds.
- **Duration:** one answer takes 2 to 8 s; the Hobby limit with Fluid compute is 300 s.

## 1. Supabase

Use the same project as the Call QA Agent or a new one in Frankfurt.

### Setup from a phone: the setup-supabase workflow

> **Without the workflow.** setup-supabase is one of the operations workflows kept in the private working copy of this repository; the public repository has the scripts it runs, not the workflow. From a computer with `psql` and Python, the same three steps are:
>
> ```bash
> DATABASE_URL=<session pooler URI> python .github/scripts/apply_migrations.py
> (cd rag-mcp && pip install -e . && DATABASE_URL=<session pooler URI> GEMINI_API_KEY=<key> python -m rag.cli ingest)
> (cd call-qa-agent && pip install -e . && SUPABASE_URL=<project URL> SUPABASE_SERVICE_KEY=<secret key> ANTHROPIC_API_KEY=<key> python scripts/seed_demo.py --skip-existing)
> ```
>
> The rest of this section is the phone route, for a copy of the repository that has the workflow.

The one-time database setup runs on GitHub Actions (workflow **setup-supabase**), so it needs no computer: everything below works in a phone browser. It has one choice, the step:

| Step | What it does |
| --- | --- |
| `migrations` | Applies the Supabase migrations of call-qa-agent, n8n-leads and rag-mcp with `psql`, in timestamp order, and records each file in `ops.applied_migrations`, so a rerun skips it. A migration whose tables already exist (applied by hand earlier, such as call-qa-agent's init or n8n-leads' leads migration) is recorded as already applied instead of failing. Then creates the private `call-audio` Storage bucket if it is missing. |
| `ingest-rag` | `python -m rag.cli ingest`: the 8 RAG documents, 111 chunks, embedded with Gemini. Unchanged files are skipped on a rerun. |
| `seed-demo-calls` | `scripts/seed_demo.py --skip-existing` with Anthropic: the 10 Call QA demo calls, scored from their script text. Calls already there are skipped. |
| `all` | The three above, in that order (the first time). |

**1. Collect the values.** The Supabase dashboard and the provider consoles work in a phone browser.

| Secret name | Where to copy it from |
| --- | --- |
| `SUPABASE_DB_URL` | Supabase project > **Connect** (top of the page) > **Connection string**, method **Session pooler**: copy the URI and replace `[YOUR-PASSWORD]` with the database password. Lost it? **Project Settings > Database > Reset database password**; a password of letters and digits avoids URL-encoding. Use the session pooler: GitHub's runners have no IPv6, and the direct connection is IPv6-only on the free plan. |
| `SUPABASE_URL` | **Project Settings > Data API**: the project URL, `https://<ref>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | **Project Settings > API Keys**, section **Secret keys**: a key starting `sb_secret_` (create one if the list is empty) |
| `ANTHROPIC_API_KEY` | console.anthropic.com > **API Keys** |
| `GEMINI_API_KEY` | aistudio.google.com > **Get API key** |
| `DEEPGRAM_API_KEY` | console.deepgram.com > **API Keys**. The workflow does not use it (the demo calls are scored from text); Vercel does |

**2. Add them as repository secrets** (the GitHub app cannot edit secrets, so use the browser):

1. Open the repository's **Settings > Secrets and variables > Actions** in a browser and sign in. If the GitHub app opens instead, choose "Open in browser" from the share menu.
2. Tap **New repository secret**.
3. **Name:** type it exactly as in the table, for example `SUPABASE_DB_URL`. **Secret:** paste the value, with no space or line break at the end. Tap **Add secret**.
4. Repeat for each secret. The page then lists the names; values cannot be read back, only replaced with **Update**.

**3. Run it:**

1. Open the repository's **Actions** tab and choose **setup-supabase** in the list.
2. Tap **Run workflow**, keep the branch `main`, pick the step (`all` the first time) and tap the green **Run workflow**.
3. Pull to refresh and open the new run. It takes about 3 to 5 minutes for `all`. When it is done, the run's **Summary** shows a table: migrations applied, chunks ingested, demo calls seeded. A red step names the missing secret or the failing migration in its log.

Every step is safe to run again. Secrets are passed only to the steps that need them and never printed; the database host, user, password and project ref are masked in the logs as well.

After the run:

- **Migrations:** rag-mcp's two are applied in order: `20261109000000_rag.sql` (pgvector extension, documents, chunks, embedding cache, `hybrid_search`) and `20261110000000_demo_limits.sql` (demo limits and answer cache). Every table has row level security on with no policies, and the functions are revoked from the public API roles, so the public anon key cannot read anything or use up the daily cap.
- **Documents:** the `ingest-rag` step reports 111 chunks (55 English, 56 Arabic) from 8 documents, embedded in 2 Gemini requests on the free tier. Reruns skip unchanged files and reuse cached embeddings, so they cost nothing.

## 2. Connection string for Vercel

The workflow uses the session pooler. The app on Vercel uses the **transaction pooler** instead: **Connect > Connection string**, method **Transaction pooler** (port 6543), with the database password filled in. That is `DATABASE_URL` in step 3. The app disables prepared statements, which the transaction pooler does not support.

## 3. Vercel project

1. On [vercel.com](https://vercel.com): **Add New > Project**, import this GitHub repository (a second project next to the Call QA Agent).
2. **Root Directory:** `rag-mcp`. The framework preset is detected as FastAPI; leave the build settings empty. The entrypoint is `rag.api:app` (`[tool.vercel]` in `pyproject.toml`), Python 3.12 (`.python-version`), dependencies from `pyproject.toml`.
3. **Environment Variables** (Production; mark the keys as Sensitive):

   | Name | Value |
   | --- | --- |
   | `DATABASE_URL` | the transaction pooler string from step 2 |
   | `GEMINI_API_KEY` | Gemini key (one cached embedding per new question) |
   | `ANTHROPIC_API_KEY` | key from the Claude Console (`sk-ant-...`) |
   | `DEMO_STORE` | `postgres` |
   | `DEMO_QUESTIONS_PER_CLIENT` | `5` |
   | `DEMO_WINDOW_SECONDS` | `600` |
   | `DEMO_DAILY_CAP` | `150` |
   | `CLIENT_IP_HEADER` | `x-real-ip` (Vercel sets it to the client address) |
   | `MCP_PUBLIC` | `true` (public read-only MCP demo at `/mcp`, under the same limits) |

4. **Deploy.** `vercel.json` turns on Fluid compute and pins the region to Frankfurt (`fra1`); check both in **Settings > Functions** after the first deploy.

The same with the Vercel CLI, from this folder: `vercel link`, then `vercel env add <NAME> production` for each variable, then `vercel deploy --prod`. A changed variable applies from the next deployment.

## 4. Check it

Open `https://<app>.vercel.app/health` in the browser and expect `"configured"` true for database, embeddings and answers, `"mcp": "public"` and `"questions_left_today"`.

1. **Page:** open `https://<app>.vercel.app/`, click an example question, and check the cited sources expand. The note under the question box says questions go to third-party AI services. Click the same example again: it comes from the answer cache, and the demo bar's "left today" does not go down.
2. **Limits across instances:** `select count(*) from demo_requests where asked_at > now() - interval '1 hour';` in the SQL editor counts the questions; six new questions in ten minutes from one address get "Too many questions from your address".
3. **MCP:** from a phone, add the endpoint as a connector (step 5) and ask through it. From a computer, list and call the tools with the MCP Inspector, no token needed:

   ```bash
   npx @modelcontextprotocol/inspector@2.12.0 --cli https://<app>.vercel.app/mcp --transport http --method tools/list
   ```

   ```bash
   npx @modelcontextprotocol/inspector@2.12.0 --cli https://<app>.vercel.app/mcp --transport http --method tools/call --tool-name ask --tool-arg question="What is the agency fee?"
   ```

## 5. Use the public MCP endpoint from an MCP client

The endpoint is `https://<app>.vercel.app/mcp` (streamable HTTP, no authentication). Three read-only tools: `search_documents`, `ask`, `list_documents`.

- **Claude (web or desktop):** **Settings > Connectors > Add custom connector**, name it (for example "Palm Grove documents"), paste the URL, leave the authentication settings empty, and add it. Enable it in a chat from the tools menu, then ask something like "What does a returned cheque cost? Use the Palm Grove documents."
- **Other clients:** add a remote MCP server with the URL and the streamable HTTP transport, no headers.

Every `search_documents` and `ask` call counts against the same limits as the page: 5 per 10 minutes per client address and 150 a day in total; `list_documents` and repeated questions are free. Hosted clients connect from their own servers, so all their users share those servers' addresses and per-address limit; the daily cap is what bounds spending either way.

### Private endpoint instead

For a deployment only you use, set `MCP_BEARER_TOKEN` (at least 24 characters, for example from `openssl rand -hex 32`) and leave out `MCP_PUBLIC`. Every request then needs `Authorization: Bearer <token>`, and the demo limits do not apply. Use it from clients that let you add a request header, for example the Inspector with `--header "Authorization: Bearer $TOKEN"`. Remove `MCP_PUBLIC` and `MCP_BEARER_TOKEN` both to turn `/mcp` off.

## Cost and limits

| Service | Plan | What this demo uses |
| --- | --- | --- |
| Vercel | Hobby, $0 | One function, 2 to 8 s per new question |
| Supabase | Free, $0 | A few MB of chunks and vectors, plus the demo tables (rows older than two days are deleted). Free projects pause after a week without activity; resume them from the dashboard |
| Gemini | Free tier | One embedding per new question, cached |
| Anthropic | Pay per use | About $0.0064 per new question (Sonnet answer plus Haiku translation, list price); the daily cap of 150 bounds it at about $1 a day. Also set a monthly spend limit in the Claude Console as the hard stop |

The client address comes from Vercel's `x-real-ip`. Behind another proxy, set `CLIENT_IP_HEADER` to the header that proxy overwrites, or leave it empty; the daily cap still bounds spending.

## Roll back and remove

- **Roll back:** **Deployments**, pick the previous production deployment, **Promote**.
- **Turn the demo off quickly:** set `DEMO_DAILY_CAP=0` and redeploy; the page and `/mcp` then refuse new questions, and cached answers still show.
- **Remove:** delete the Vercel project; in Supabase drop the tables (`drop table demo_answers, demo_requests, chunks, documents, embedding_cache cascade;`) or delete the project.
