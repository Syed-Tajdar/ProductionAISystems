# Deploying n8n

The public demo runs n8n on **Render's free plan** (no card) with its own data in the existing Supabase database, and the demo page on **Vercel**. Every step below works from a phone browser. Other ways to run n8n are at the end.

| Part | Where | Cost |
| --- | --- | --- |
| n8n (workflows, editor) | Render free web service, `render.yaml` | $0, 750 free instance hours a month |
| n8n's own data (workflows, credentials, executions) | Supabase, schema `n8n`, apart from the lead tables | $0, inside the Supabase free plan |
| Lead tables and demo limits | Supabase, schema `public` | $0 |
| Demo page | Vercel Hobby, `landing/` | $0 |
| Lead classification | Claude Haiku, about $0.001 per lead; the demo allows 100 leads a day | at most about $0.10 a day |

What the free plan means: n8n sleeps after 15 minutes without traffic, and the next request waits while it starts. Measured with the same limits (512 MB, 0.1 CPU): `/healthz` answers after about 80 s, the webhooks after about 2 minutes, then about 4 s per lead. The demo page wakes n8n as soon as it opens and waits up to 5 minutes, so a visitor sees a calm "starting up" message, not an error. Nothing is lost on a sleep or restart: everything n8n stores is in Postgres, and `N8N_ENCRYPTION_KEY` is a fixed value you keep.

Six steps, all from a phone browser: Render blueprint, n8n owner signup, an n8n API key, the GitHub variable and secrets, the **setup-n8n** workflow, and Vercel. The setup-n8n workflow does everything inside n8n: it creates the credentials, imports the four workflows with every node pointed at its credential, links the error workflow and activates them.

> **Without the workflows.** setup-n8n, n8n-errors, n8n-leads-schedule and check-db-certificate are operations workflows kept in the private working copy of this repository; the public repository has the scripts they run. From a computer, step 5 is `python .github/scripts/setup_n8n.py` with the variable and secrets of step 4 set in the environment (the script's first lines list them), reading errors is `python .github/scripts/n8n_errors.py`, and the schedules can be any scheduler that calls the two webhooks with the `X-Scheduler-Token` header ([README](README.md#workflow-b-follow-up-and-daily-summary)).

> **Use the exact URL Render shows, never a guessed one.** Render names the service after `render.yaml` (`n8n-leads`), but when that name is taken it adds a suffix (`n8n-leads-<suffix>.onrender.com`), and the plain name can belong to someone else's n8n. Copy the address from the top of your service's page in the Render dashboard. `N8N_HOST`, `WEBHOOK_URL` and `LEAD_WEBHOOK_URL` must all use that exact address. Below it is written `<your-service>.onrender.com`.

## 0. Before you start (5 minutes)

1. **Migrations.** On GitHub: **Actions > setup-supabase > Run workflow**, step `migrations`. It applies `20261111000000_lead_demo.sql`, which creates the `n8n` schema and the demo limit tables (and the earlier lead migrations if they are missing).
2. **Database connection.** Supabase project > **Connect** (top of the page) > **Connection string**, method **Session pooler**. Note the host (`aws-0-<region>.pooler.supabase.com`), the user (`postgres.<project-ref>`) and the database password you already use for the other demos. Do not reset the password unless you also update the RAG and Call QA settings that use it.
3. **Two long random values**, saved in your password manager (its generator works on a phone; on a computer, `openssl rand -hex 32`):
   - `N8N_ENCRYPTION_KEY`: 40 or more letters and digits. n8n encrypts the saved credentials with it; if it is lost, run setup-n8n again after fixing it.
   - Scheduler token: another 40 or more letters and digits.
4. **Telegram chat id.** Reuse the chat of the uptime alerts (the `TELEGRAM_CHAT_ID` secret) for both sales and ops: its bot, `TELEGRAM_BOT_TOKEN`, is the one n8n will use. Demo alerts start with **[DEMO]** and stop after 20 a day.

## 1. n8n on Render (10 minutes)

1. dashboard.render.com > **New > Blueprint**. Connect the GitHub repository, set **Blueprint Path** to `n8n-leads/render.yaml`, and continue. Render shows one free web service.
2. Fill in the values Render asks for. The address is not known yet, so `N8N_HOST` and `WEBHOOK_URL` get a temporary value now and the real one in step 4:

   | Key | Value |
   | --- | --- |
   | `DB_POSTGRESDB_HOST` | the session pooler host from 0.2 |
   | `DB_POSTGRESDB_USER` | `postgres.<project-ref>` |
   | `DB_POSTGRESDB_PASSWORD` | the database password |
   | `N8N_ENCRYPTION_KEY` | the value from 0.3 |
   | `N8N_HOST` | `set-after-deploy.invalid` for now |
   | `WEBHOOK_URL` | `https://set-after-deploy.invalid/` for now |
   | `LEADS_SUPABASE_URL` | `https://<project-ref>.supabase.co` |
   | `LEADS_SALES_CHAT_ID`, `LEADS_OPS_CHAT_ID` | the chat id from 0.4 |

3. **Apply.** The first start creates n8n's tables in the `n8n` schema and takes 3 to 5 minutes.
4. Open the service in the Render dashboard and copy its address from the top of the page (`https://<your-service>.onrender.com`). Under **Environment**, set `N8N_HOST` to that host (`<your-service>.onrender.com`, no `https://`) and `WEBHOOK_URL` to `https://<your-service>.onrender.com/`, then press **Manual Deploy**.

Everything else is already in `render.yaml`: the demo mode and its caps (`LEADS_DEMO_*`), no email sender, one week of execution history, and the settings that keep n8n inside 512 MB and let its Code runner start on 0.1 CPU.

## 2. n8n owner signup (2 minutes)

Open the address you copied in 1.4, `https://<your-service>.onrender.com`, and create the owner account: your email and a strong password. This is n8n's own login, for the editor and the executions list.

Check the address bar against the Render dashboard before you sign up. Every new n8n shows the same signup page, so signing up on a guessed address gives you a working login and API key on someone else's instance, and the setup would then put your credentials there.

## 3. n8n API key (2 minutes)

In n8n: your name (bottom left) > **Settings > n8n API > Create an API key**. Label `setup-n8n`; expiration as you like (a new key just means updating one secret). If n8n shows scopes, leave all of them selected; setup-n8n needs at least `credential:create`, `credential:list`, `credential:update`, `credential:delete`, `workflow:create`, `workflow:list`, `workflow:read`, `workflow:update` and `workflow:activate`, and n8n-errors needs `execution:list` and `execution:read`. Copy the key: n8n shows it only once.

## 4. GitHub variable and secrets (3 minutes)

Repository **Settings > Secrets and variables > Actions**:

| Kind | Name | Value | Status |
| --- | --- | --- | --- |
| Variable | `N8N_HOST` | `<your-service>.onrender.com`, exactly as in 1.4 (host only: no `https://`, no slash) | new |
| Secret | `N8N_API_KEY` | the API key from step 3 | new |
| Secret | `N8N_SCHEDULER_TOKEN` | the scheduler token from 0.3 | new |
| Secret | `ANTHROPIC_API_KEY` | your Claude API key, with a monthly spend limit in the Claude Console | exists |
| Secret | `SUPABASE_SERVICE_KEY` | the Supabase secret key (`sb_secret_...`, or the legacy service_role key) | exists |
| Secret | `TELEGRAM_BOT_TOKEN` | the bot token from BotFather | exists |

`N8N_HOST` also turns on the `n8n-leads-schedule` workflow (step 7).

## 5. Run setup-n8n (5 to 20 minutes)

**Actions > setup-n8n > Run workflow**. It runs in this order:

1. **Host check, before anything is written.** It waits for `https://<N8N_HOST>/healthz` (a sleeping instance takes 2 to 3 minutes), then reads the workflow list with `N8N_API_KEY`. If the key is rejected, it stops with "This host may not be your n8n instance" and changes nothing: copy the exact URL from the Render dashboard into `N8N_HOST`, and create the key on that instance.
2. **Credentials:** the six the workflows use, by name: `Supabase`, `Anthropic API`, `Telegram Bot`, `Scheduler token`, and two placeholders, `Gemini API` and `SMTP`, that the demo never calls (kept once they exist). On a standard n8n the four real ones are updated in place. Only on an instance that cannot list or update credentials does each run make fresh copies (see the fallbacks below).
3. **Workflows:** imports the four, or updates them when they already exist, with every node pointed at its credential; sets "Lead workflow errors (Workflow C)" as the error workflow of Workflow A and both Workflow B workflows; publishes each one and waits until n8n shows it active.
4. **Cleanup:** deletes every credential copy that setup-n8n made earlier and no longer uses, including copies from a run that stopped halfway. Their ids are kept in an inactive workflow, **setup-n8n credential ledger**, because an instance without a credential list gives no other way to find them. On a standard n8n nothing is created after the first run, so the ledger stays empty. Leave it in place; deleting it only means later runs cannot clean up copies made before.
5. **Health check:** n8n's `/healthz`, the form webhook (a honeypot submission, which stores nothing and calls no API) and that the follow-up webhook refuses a call without the token.

The run summary lists every credential and workflow with its id and what happened to it (created, updated, replaced, kept, deleted). No secret is printed. Run it again whenever a workflow file changes or a key is rotated: it ends with the same six credentials and four workflows every time.

The first run on the free plan is the slow one: n8n wakes up, and activating each workflow can take more than a minute on 0.1 CPU. The job allows 30 minutes.

**How to verify.** Check that `N8N_HOST` matches the service page in the Render dashboard, then run setup-n8n twice. On a standard instance such as ours, both runs show the four real credentials as "updated", the placeholders as "kept (placeholder)", the ledger as "0 credential(s) on record", and every health check as "ok". "replaced" and "deleted (earlier copy)" appear only when the fallback path below made copies. The n8n Credentials page should show exactly six.

### Fallbacks for instances whose public API differs

A standard n8n 2.45 has every route setup-n8n needs. During setup we also met an n8n instance (on another host) whose public API answered differently. setup-n8n handles each of these cases, so it works there too, and its tests (`tests/test_setup_n8n.py`, with a fake n8n in `tests/bench/fake_n8n.py`) cover each one. None of them is expected on a standard instance:

| If the instance... | setup-n8n... |
| --- | --- |
| answers 405 to `GET /credentials` and `PATCH /credentials/{id}` (credentials cannot be listed, read or updated) | finds the current credentials through the workflows that use them, creates fresh copies, points the workflows at them, then deletes the old copies |
| treats the credential `id` as read-only on create | creates credentials without an id and rewrites each node's credential reference to the new id |
| answers 404 to `POST /workflows/{id}/publish` | uses `POST /workflows/{id}/activate` instead |
| finishes activation after the request has ended | reads the workflow back every 10 s until it shows active, up to 5 minutes per workflow |
| sits behind a firewall that blocks some plain JSON bodies (workflow code) | sends every request body gzip-compressed |
| answers errors with HTML pages | reports the page title and text instead of raw HTML |

## 6. Demo page on Vercel (5 minutes)

1. vercel.com > **Add New > Project**, import the repository.
2. **Project Name** `syed-leads`, **Root Directory** `n8n-leads/landing`, **Framework Preset** Other. `vercel.json` sets the build. If the name is taken, Vercel gives the page a different address: use the one shown on the project page wherever `syed-leads.vercel.app` appears below.
3. **Environment Variables**: `LEAD_WEBHOOK_URL` = `https://<your-service>.onrender.com/webhook/lead-intake`, with the exact host from 1.4. The build fails without it, so a page without a workflow never goes live.
4. **Deploy**. Optional: **Analytics > Enable** for page views (free on Hobby).
5. Test: open the page, tap **Hot rental**, **Send enquiry**. The first send after a sleep shows the start-up message and answers in 2 to 3 minutes; the result shows "Hot" with a reason, the Telegram alert arrives marked [DEMO], and the reply email is shown on the page. Send it again: "Repeat" and no second email.

The page's security policy allows requests to `*.onrender.com` only. For n8n on a custom domain, add it to `connect-src` in `landing/vercel.json`.

## 7. Schedules, uptime and the portfolio link

- **Schedules:** with `N8N_HOST` set, `n8n-leads-schedule` calls the follow-up every 6 hours (at :07 past 0, 6, 12 and 18 UTC) and the daily summary at 8:53 Dubai time, retrying while n8n wakes up. Every 6 hours keeps the Actions minutes low on a private repository; demo leads never get follow-ups anyway. Test it once: **Actions > n8n-leads-schedule > Run workflow > lead-daily-summary**; the summary arrives on Telegram.
- **Uptime:** add the repository variable `LEADS_DEMO_URL` = `https://syed-leads.vercel.app`. The uptime check then loads the demo page (only the page, never n8n).
- **Portfolio:** once the demo answers, the portfolio's Vercel project > **Settings > Environment Variables** > `LEADS_DEMO_URL` = `https://syed-leads.vercel.app` (Production) > **Deployments > Redeploy**. The project card, the project page, the recruiter block and the JSON-LD then link the demo, and the status line says it is live. Without the variable the site shows no lead demo link.

## Check the database certificate

n8n checks the database server's certificate (`DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=true`). This needs one extra step on a new deploy, because Supabase signs with its own CA, which is not in the system's list. To check it, give n8n that CA. The CA certificate is public and committed as [`supabase/prod-ca-2021.crt`](supabase/prod-ca-2021.crt) (Supabase Root 2021 CA, valid to April 2031, SHA-256 fingerprint `80:70:25:AD:50:D4:ED:21:9D:2C:9C:7D:29:9C:00:4F:82:4E:B0:0C:F7:F6:5A:FE:F6:07:D0:7B:72:E6:CA:FA`). It is the file Supabase offers under **Project Settings > Database > SSL Configuration > Download certificate**; compare the two if in doubt.

Do the steps in this order. Each one can be undone, and the service keeps running on the old settings until step 3.

1. **Check first, without touching Render.** GitHub: **Actions > check-db-certificate > Run workflow**. It makes a TLS handshake with the session pooler host from `SUPABASE_DB_URL` and verifies the chain and the host name against the committed CA only (`.github/scripts/check_db_cert.py`; no login, no query). Go on only if it prints `OK: certificate and host name verified against prod-ca-2021.crt`. If it prints `FAILED`, change nothing: the pooler does not present a certificate from this CA, and the setting below would stop n8n from starting.
2. **Add the CA.** Render: the n8n service > **Environment > Secret Files > Add Secret File**. Filename `supabase-ca.crt`, contents: the whole text of `supabase/prod-ca-2021.crt`, from `-----BEGIN CERTIFICATE-----` to `-----END CERTIFICATE-----` (on GitHub open the file, tap **Raw**, select all, copy). Then add the variable `DB_POSTGRESDB_SSL_CA_FILE` = `/etc/secrets/supabase-ca.crt`. A secret file keeps the line breaks intact; pasting the text into a variable named `DB_POSTGRESDB_SSL_CA` also works. Save without deploying. Nothing changes yet: n8n still does not reject.
3. **Turn the check on.** Same page: set `DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED` to `true`, then **Save, rebuild, and deploy**.
4. **Watch the deploy** under **Logs**. Good: a line starting `n8n ready on` and then `Editor is now accessible`. Bad: `self-signed certificate in certificate chain` or `unable to verify the first certificate`, and n8n stops.
5. **Test:** open the demo page and send one enquiry.
6. **If anything fails:** set `DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED` back to `false` and **Save, rebuild, and deploy**. That is the whole rollback; the CA variable can stay.
7. **Keep it:** `render.yaml` sets the check to `"true"` and points `DB_POSTGRESDB_SSL_CA_FILE` at the secret file, so a Blueprint sync keeps it. A Blueprint cannot carry the file's contents, so on a new service add the secret file (step 2) before the first deploy, or that deploy fails until it is added.

What is tested here: n8n 2.45 against a local Postgres with TLS starts with the right CA, given as text or as a file (`DB_POSTGRESDB_SSL_CA_FILE`), and refuses to connect with another CA; the check script accepts the right CA and rejects a wrong CA and a wrong host name (`tests/test_db_tls.py`). Not tested from here: the Supabase pooler itself, which step 1 covers.

## Reading n8n errors from GitHub

**Actions > n8n-errors > Run workflow** (`.github/workflows/n8n-errors.yml`) shows where recent executions went wrong without opening the n8n editor. Input `limit`: how many of the latest executions to read (default 3). For each one it prints the status, start time and workflow id, the workflow error if there is one, and every node that failed or returned something that looks like an error (an error field, a status code, "unauthorized", "not found"). It only reads: `GET /executions` with `N8N_HOST` and `N8N_API_KEY`, values of secrets in its environment are masked, and lead data is not printed: names, phone numbers, emails and messages are replaced before anything reaches the log. If n8n was asleep and the run times out, run it again: the first request wakes it.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Render log: `Connection terminated due to connection timeout` once or twice at start | Normal on 0.1 CPU; n8n retries. If it repeats, check the pooler host, user and password. |
| Render log: `password authentication failed` | User must be `postgres.<project-ref>` for the pooler, not `postgres`. |
| Render log: `Mismatching encryption keys` | `N8N_ENCRYPTION_KEY` changed. Put back the saved value. |
| Render event: out of memory | Check that `NODE_OPTIONS` and `N8N_RUNNERS_MAX_OLD_SPACE_SIZE` from `render.yaml` are set. |
| Demo page: "Not connected yet" | `LEAD_WEBHOOK_URL` is missing on the Vercel project; set it and redeploy. |
| Demo page: "Demo limit reached" | 5 leads per visitor in 10 minutes, or 100 a day in total (UTC). |
| Result says the alert was skipped | The 20 demo alerts of the day are used up. |
| Schedule run red with HTTP 403 | `N8N_SCHEDULER_TOKEN` changed after the last setup: run setup-n8n again. |
| setup-n8n: "rejected N8N_API_KEY ... may not be your n8n instance" | `N8N_HOST` is not the instance the key was made on. Copy the exact URL from the Render dashboard (1.4), and create the key there (step 3). Nothing was changed. |
| setup-n8n: "lacks a scope" | Create a new key (step 3) with all scopes and update the secret. |
| setup-n8n: "did not answer within 420 s" | n8n did not start: check the Render logs, then run again. Nothing was changed. |
| setup-n8n: "... is not active 300 s after it was activated" | Activation did not finish. Run n8n-errors and check the Render logs, then run setup-n8n again; it picks up where it stopped and removes the copies it made. |
| setup-n8n: "left in place, deleted on the next run" | A delete failed; the id stays in the ledger and the next run deletes it. |
| setup-n8n: "missing settings: ..." | The named variable or secrets are not set (step 4). |
| setup-n8n: health check fails on the form webhook | n8n cannot read its settings: check `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and the `LEADS_*` values on Render, and run n8n-errors for the failing node. |
| setup-n8n: "2 workflows are named ..." | Copies made by hand: delete the extra ones in n8n, then run again. |

---

## Other ways to run n8n

The workflows are the same everywhere; only `LEADS_DEMO_MODE` decides whether emails are sent. For a real business, leave `LEADS_DEMO_MODE` unset, set `LEADS_EMAIL_FROM` and a real SMTP credential, and use a host that does not sleep.

| | n8n Cloud (trial) | Railway | Render paid |
| --- | --- | --- | --- |
| What you run | Nothing, n8n hosts it | `n8nio/n8n` image + Railway Postgres | `render.yaml` with `plan: starter` |
| Always on | Yes | Yes | Yes |
| Rough monthly cost | Free for the trial, then about EUR 24 (Starter, 2,500 executions) | About $5 to $10 (Hobby $5 includes $5 of usage) | About $7 (Starter), database stays on Supabase |

Prices are approximate, from public pricing pages checked in November 2026.

### n8n Cloud trial

1. Sign up at n8n.io and start the free trial. The URL becomes `https://<name>.app.n8n.cloud`.
2. In **Settings**, set the timezone to `Asia/Dubai`.
3. Create an API key and run setup-n8n as in steps 3 to 5, with `N8N_HOST` set to `<name>.app.n8n.cloud`. n8n Cloud does not allow `$env`, so then set the values at the top of **Normalize lead** in Workflow A in the editor.
4. Before the trial ends, download each workflow as JSON; credentials do not export.

### Railway

1. New project > **Create > Database > PostgreSQL**, then **Create > Docker Image** `n8nio/n8n:2.45.0`, renamed `n8n`.
2. In the n8n service, **Variables > Raw Editor**: the `DB_POSTGRESDB_*` values as `${{Postgres.PGHOST}}`, `${{Postgres.PGPORT}}`, `${{Postgres.PGDATABASE}}`, `${{Postgres.PGUSER}}`, `${{Postgres.PGPASSWORD}}`, plus `DB_TYPE=postgresdb`, `N8N_ENCRYPTION_KEY`, `N8N_PORT=5678`, `N8N_LISTEN_ADDRESS=::`, `N8N_PROTOCOL=https`, `N8N_HOST`, `WEBHOOK_URL`, `N8N_PROXY_HOPS=1`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, `GENERIC_TIMEZONE=Asia/Dubai` and the `LEADS_*` values (see `.env.example`).
3. **Settings > Networking > Generate Domain** (port 5678), put the domain into `N8N_HOST` and `WEBHOOK_URL`, redeploy.
4. **Workspace > Usage**: set a usage limit.

### Local or VPS with Docker

```bash
cp .env.example .env              # then set N8N_ENCRYPTION_KEY and POSTGRES_PASSWORD
docker compose up -d
docker compose ps                 # both services should show (healthy)
curl localhost:5678/healthz       # {"status":"ok"}
```

Mailpit at http://localhost:8025 shows every email the workflows send. If n8n restarts with `n8n's address '::' is not available`, set `N8N_LISTEN_ADDRESS=0.0.0.0`. On a VPS, put n8n behind a reverse proxy with HTTPS. On an always-on host you can enable the disabled Schedule Trigger in each Workflow B instead of GitHub Actions; use one scheduler, never two.
