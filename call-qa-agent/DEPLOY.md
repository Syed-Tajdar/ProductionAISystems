# Deploying the Call QA Agent to Vercel

One Vercel project serves the API, the dashboard and the upload page: FastAPI on Vercel Functions with Fluid compute, on the free Hobby plan. Data and uploaded audio live in Supabase (free plan, Frankfurt); the function runs in Frankfurt too (`fra1`, set in `vercel.json`). Deepgram and the LLM are called per call. Why this setup: [docs/hosting-decision.md](../docs/hosting-decision.md). The Cloud Run alternative: [docs/cloud-run/call-qa-agent.md](../docs/cloud-run/call-qa-agent.md).

What the platform limits mean here:

| Limit | Value | How the app deals with it |
| --- | --- | --- |
| Request body of a Vercel Function | 4.5 MB | The upload page sends audio straight from the browser to Supabase Storage with a signed URL; the function only gets the storage path. `MAX_UPLOAD_MB=4` for files posted to `/calls` directly. |
| File size on the Supabase free plan | 50 MB per file | Bigger recordings: give a public URL (`url` on `POST /calls`), which Deepgram fetches itself. |
| Function duration (Hobby, Fluid compute) | 300 s per request | `PROCESSING_MODE=inline`: transcription and scoring run inside the request, usually 5 to 60 s. |
| Instances | as many as traffic needs | The login rate limit is in Postgres (`login_failures`), so it holds across instances. |

Hobby is for non-commercial use, which fits a portfolio demo.

## 1. Supabase

Use the existing project (Frankfurt) or create one at [supabase.com](https://supabase.com) in Frankfurt.

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
| `seed-demo-calls` | `scripts/seed_demo.py --skip-existing` with Anthropic: the 10 Call QA demo calls, scored from their script text, with their sample audio uploaded to Storage for the player. Calls already there are not scored again, but get their audio if they have none. |
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

After the `migrations` step:

- **Tables:** call-qa-agent's three migrations are applied in order: `20261104000000_init.sql` (tables, view, row level security), `20261106000000_demo_calls.sql` (`calls.is_demo`) and `20261107000000_vercel.sql` (`calls.storage_path`, the login limit table and functions).
- **Storage bucket:** **Storage > Buckets** shows `call-audio`: private, 50 MB file size limit, allowed MIME types `audio/*`. Do not add storage policies: the browser uploads with a signed URL made by the server, and Deepgram reads with a signed URL that expires after an hour.
- **Keys for Vercel:** the project URL and the secret key you added above. The secret key bypasses row level security; besides GitHub's secrets it only goes into Vercel's environment variables.

## 2. Vercel project

1. On [vercel.com](https://vercel.com): **Add New > Project**, import this GitHub repository.
2. **Root Directory:** `call-qa-agent`. The framework preset is detected as FastAPI; leave the build settings empty. The entrypoint is `app.main:app` (`[tool.vercel]` in `pyproject.toml`), the Python version 3.12 (`.python-version`), and the dependencies come from `pyproject.toml`.
3. **Environment Variables** (Production; mark the keys as Sensitive):

   | Name | Value |
   | --- | --- |
   | `APP_ENV` | `production` (secure session cookie) |
   | `APP_PASSCODE` | a long random value, for example from `openssl rand -base64 24` |
   | `SESSION_SECRET` | another long random value, for example from `openssl rand -base64 32`; signs the session cookie. Setting or changing it signs everyone out once |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_SERVICE_KEY` | the secret key (`sb_secret_...`) |
   | `DEEPGRAM_API_KEY` | Deepgram key |
   | `LLM_PROVIDER` | `anthropic` |
   | `ANTHROPIC_API_KEY` | key from the Claude Console (`sk-ant-...`) |
   | `PROCESSING_MODE` | `inline` |
   | `MAX_UPLOAD_MB` | `4` |
   | `STORAGE_BUCKET` | `call-audio` |
   | `STORAGE_MAX_MB` | `50` |
   | `LOGIN_LIMIT_STORE` | `database` |
   | `CLIENT_IP_HEADER` | `x-real-ip` (Vercel sets it to the client address) |
   | `DEMO_MODE` | `true` (visitors see the demo calls, read-only) |
   | `DEEPGRAM_KEYTERMS` | optional: brand and product names, comma-separated |

4. **Deploy.** `vercel.json` turns on Fluid compute and pins the function region to Frankfurt (`fra1`). After the first deploy, check **Settings > Functions**: Fluid compute on, region Frankfurt.

With the Vercel CLI instead (from this folder):

```bash
npm i -g vercel@latest && vercel login && vercel link
```

```bash
vercel env add APP_PASSCODE production
```

Repeat `vercel env add` for each variable in the table (it asks for the value, so keys stay out of the shell history), then:

```bash
vercel deploy --prod
```

Changing a variable takes effect on the next deployment: **Deployments > ... > Redeploy**.

## 3. Demo calls

Run **setup-supabase** with the step `seed-demo-calls` (or `all`, which includes it). It scores the 10 sample calls (synthetic data) from their script text with Anthropic and marks them `is_demo`, so visitors can browse them; calls already there are not scored again. It also uploads each demo call's sample audio (mono MP3 at 48 kbps, committed in `samples/audio/`) to `call-audio/demo/v2/` for the player on the call page. Demo calls that already exist get the current audio and transcript timings without being scored again, and their older audio file is deleted; this costs nothing at LLM prices. A call that fails is deleted again and the step turns red; run it again later to add the missing ones. Cost: about $0.10 for all 10 at list price.

To remove the demo calls later, from a computer: `python scripts/seed_demo.py --remove` with `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` set.

## 4. Check it

Replace `<app>` with the deployment's domain. Open `https://<app>.vercel.app/health` in the browser and expect `"configured"` true for supabase, deepgram and anthropic, `"processing_mode": "inline"` and `"storage_bucket": "call-audio"`.

1. **Demo:** `https://<app>.vercel.app/dashboard` lists the 10 demo calls without logging in; a real call's URL answers 404 to visitors.
2. **Upload from the browser:** log in at `/login`, open **Upload**, choose an audio file over 4.5 MB (to prove it bypasses the function limit) and a rubric. The page shows the upload progress, then "Transcribing and scoring...", then opens the scored call. In Supabase, **Storage > call-audio** has the file under a random folder, and the `calls` row has `storage_path` set and no `source_url`.
3. **URL upload from the API** (from a computer, optional):

   ```bash
   read -rsp "Passcode: " PASSCODE; echo
   ```

   ```bash
   curl -s -H "X-Passcode: $PASSCODE" -F url=https://<public-audio-url>.mp3 -F rubric=us_insurance https://<app>.vercel.app/calls
   ```

   The answer is HTTP 200 with the final `status`, `overall_score` and `passed`.
4. **Login limit:** five wrong passcodes in a row give "Too many failed attempts" (HTTP 429), from any instance. `select client, failed_at from login_failures order by failed_at desc limit 10;` in the SQL editor shows them; `select login_reset('<address>');` clears one address early.
5. **Logs:** **Deployments > (deployment) > Logs**. Keys and signed URLs are not logged.

## Cost

| Service | Plan | What this demo uses |
| --- | --- | --- |
| Vercel | Hobby, $0 | One function; each upload holds it for the transcription and scoring time |
| Supabase | Free, $0 | Tables of a few MB; audio in Storage (1 GB on the free plan). Free projects pause after a week without activity; resume them from the dashboard |
| Deepgram | Pay as you go (new accounts get free credit) | Per audio minute |
| Anthropic | Pay per use | About $0.01 per scored call with `claude-sonnet-5-5` at list price |

Uploaded audio stays in the bucket. To free space, delete old folders under **Storage > call-audio**; the transcripts and scores stay in the tables.

## Roll back and remove

- **Roll back:** **Deployments**, pick the previous production deployment, **Promote** (instant rollback).
- **Remove:** `python scripts/seed_demo.py --remove` (from a computer, see step 3), empty and delete the `call-audio` bucket, delete the Vercel project (**Settings > Advanced > Delete Project**). Rotate the Supabase secret key afterwards if it was ever exposed.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Upload page: "storage answered 400" | The file is not audio or is over 50 MB, or the bucket's limits differ from step 1.2. |
| Upload page: "server answered 502" when preparing | Wrong `SUPABASE_SERVICE_KEY`, or the bucket does not exist. |
| A call fails with a Deepgram error | Deepgram could not fetch the file; check the object exists in the bucket and `DEEPGRAM_API_KEY`. |
| HTTP 413 on `POST /calls` with a file | Over `MAX_UPLOAD_MB` (or Vercel's 4.5 MB): use the upload page or a URL. |
| Every route answers 503 | `APP_PASSCODE` is not set. |
| Logins never get limited | `LOGIN_LIMIT_STORE` not `database`, or the third migration was not run (the app then falls back to a per-instance limit and logs a warning). |
