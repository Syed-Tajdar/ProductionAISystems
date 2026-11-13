# Deploying the Call QA Agent to Cloud Run

Alternative to the current hosting (Vercel, see [call-qa-agent/DEPLOY.md](../../call-qa-agent/DEPLOY.md) and [the hosting decision](../hosting-decision.md)). Kept for running the same image as a container. Commands run from the `call-qa-agent` folder.

One Cloud Run service runs the API and the dashboard. Data lives in Supabase (Frankfurt). Deepgram and one LLM provider are called per call. Why Cloud Run: [docs/hosting-decision.md](../hosting-decision.md).

| Setting | Value | Why |
| --- | --- | --- |
| Region | `europe-west1` (Belgium) | Tier 1 price, low CO2, the closest Tier 1 region to Frankfurt (about 340 km). Frankfurt itself (`europe-west3`) is Tier 2. |
| Billing | request-based (`--cpu-throttling`) | CPU is only allocated while a request runs; free tier covers this app's traffic |
| Instances | min 0, max 1 | Scales to zero when idle. One instance, because the login rate limit lives in memory |
| Processing | `PROCESSING_MODE=inline` | CPU is throttled after the response, so transcription and scoring run inside the upload request |
| Uploads | `MAX_UPLOAD_MB=30` | Cloud Run rejects HTTP/1 requests over 32 MiB; send bigger files as a `url` |
| LLM | `LLM_PROVIDER=anthropic` (`claude-sonnet-5-5`) | Gemini's free tier allows 20 requests a day for `gemini-3.8-flash` and returned 503s at busy hours; Gemini stays available as an optional provider |
| Timeout | 900 s | An inline upload waits for Deepgram and the LLM, retries included (Cloud Run allows up to 3600 s) |

All commands run in [Cloud Shell](https://shell.cloud.google.com), signed in with the Google account that owns the billing account. You need: the Supabase project URL and secret key (`sb_secret_...`), a Deepgram API key, and an Anthropic API key from the Claude Console (keys start with `sk-ant-api` or, for personal keys, `sk-ant-usr`) with a monthly spend limit set. A Gemini key from AI Studio is optional.

## 0. Get the code and save the settings

```bash
git clone https://github.com/Syed-Tajdar/ProductionAISystems.git ~/ProductionAISystems
```

```bash
cd ~/ProductionAISystems/call-qa-agent
```

Cloud Shell forgets shell variables when it restarts, so they go in a file. The project id must be globally unique; the command below adds a random suffix.

```bash
cat > ~/callqa.env <<EOF
export PROJECT_ID=call-qa-agent-$RANDOM
export REGION=europe-west1
export SERVICE=call-qa-agent
EOF
source ~/callqa.env && echo "$PROJECT_ID"
```

After any Cloud Shell restart: `cd ~/ProductionAISystems/call-qa-agent && source ~/callqa.env`.

## 1. New project and billing

Use a new project. If you also keep a Gemini key from AI Studio, this matters: linking billing to the AI Studio project moves that key to the paid tier.

```bash
gcloud projects create "$PROJECT_ID" --name="Call QA Agent"
```

```bash
gcloud config set project "$PROJECT_ID"
```

```bash
gcloud billing accounts list
```

Copy the `ACCOUNT_ID` of your open billing account into the file, then link it:

```bash
echo "export BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX" >> ~/callqa.env && source ~/callqa.env
```

```bash
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
```

```bash
echo "export PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')" >> ~/callqa.env && source ~/callqa.env
```

Only if you have an AI Studio key: check that its project still has no billing. Its id is shown in AI Studio > API keys; `billingEnabled` must be `False`:

```bash
gcloud billing projects describe <ai-studio-project-id> --format='value(billingEnabled)'
```

## 2. Enable the APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com billingbudgets.googleapis.com
```

## 3. Budget alert

Emails the billing account admins at 50%, 90% and 100% of $5 spent, and when the month is forecast to pass $5. A budget only alerts; it does not stop the service.

```bash
gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="call-qa-agent" \
  --budget-amount=5USD --filter-projects="projects/$PROJECT_ID" \
  --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 \
  --threshold-rule=percent=1.0,basis=forecasted-spend
```

## 4. Service accounts

The service runs as its own account, which can read its secrets and nothing else. Deploying from source builds with the Compute Engine default account, which needs the Cloud Run Builder role (it takes a couple of minutes to apply).

```bash
gcloud iam service-accounts create call-qa-run --display-name="Call QA Agent runtime"
```

```bash
echo "export RUN_SA=call-qa-run@$PROJECT_ID.iam.gserviceaccount.com" >> ~/callqa.env && source ~/callqa.env
```

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com" --role=roles/run.builder --condition=None
```

## 5. Secrets

Four secrets, one version each (five with the optional Gemini key). Secret Manager's free tier is 6 active versions and 10,000 accesses a month per billing account, so destroy old versions when you rotate (see "Rotate a secret") and count any secrets in other projects on the same billing account.

This helper reads a value without echoing it or saving it in the shell history, stores it, and lets the runtime account read it:

```bash
add_secret() {
  read -rsp "Value for $1: " value; echo
  printf '%s' "$value" | gcloud secrets create "$1" --replication-policy=automatic --data-file=- && unset value
  gcloud secrets add-iam-policy-binding "$1" --member="serviceAccount:$RUN_SA" \
    --role=roles/secretmanager.secretAccessor --condition=None --format=none
}
```

```bash
add_secret supabase-service-key
```

```bash
add_secret deepgram-api-key
```

```bash
add_secret anthropic-api-key
```

Only if you want Gemini as an alternative (`LLM_PROVIDER=gemini`), also run `add_secret gemini-api-key`.

The passcode is created directly in Secret Manager:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24), end='')" | \
  gcloud secrets create app-passcode --replication-policy=automatic --data-file=-
```

```bash
gcloud secrets add-iam-policy-binding app-passcode --member="serviceAccount:$RUN_SA" \
  --role=roles/secretmanager.secretAccessor --condition=None --format=none
```

To read it later (for the login page): `gcloud secrets versions access latest --secret=app-passcode`.

## 6. Plain settings

```bash
read -rp "Supabase project URL (https://<ref>.supabase.co): " SUPABASE_URL
```

```bash
cat > ~/callqa-env.yaml <<EOF
APP_ENV: production
DEMO_MODE: "true"
PROCESSING_MODE: inline
MAX_UPLOAD_MB: "30"
LLM_PROVIDER: anthropic
SUPABASE_URL: $SUPABASE_URL
DEEPGRAM_KEYTERMS: Harborline Insurance,Palm Crest Properties,Brightwave Internet
EOF
```

To use Gemini instead, change `LLM_PROVIDER` to `gemini` in that file. Set `DEMO_MODE` to `"false"` if visitors should see nothing without the passcode.

## 7. Deploy from source

Cloud Build builds the `Dockerfile` in this folder and pushes the image to Artifact Registry. The first run asks to create the `cloud-run-source-deploy` repository: answer `Y`.

```bash
gcloud run deploy "$SERVICE" --source . --region "$REGION" \
  --service-account "$RUN_SA" --allow-unauthenticated \
  --cpu-throttling --cpu-boost --cpu 1 --memory 512Mi \
  --min-instances 0 --max-instances 1 --concurrency 20 --timeout 900 \
  --env-vars-file ~/callqa-env.yaml \
  --set-secrets "APP_PASSCODE=app-passcode:latest,SUPABASE_SERVICE_KEY=supabase-service-key:latest,DEEPGRAM_API_KEY=deepgram-api-key:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest"
```

With Gemini, append `,GEMINI_API_KEY=gemini-api-key:latest` inside the `--set-secrets` value.

`--allow-unauthenticated` makes the URL reachable by anyone; the app's passcode protects everything except `/health` and the demo dashboard.

Keep at most three images, so Artifact Registry stays inside its 0.5 GB free storage:

```bash
cat > ~/ar-cleanup.json <<'EOF'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"tagState": "any", "olderThan": "1d"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location="$REGION" --policy="$HOME/ar-cleanup.json" --no-dry-run
```

To redeploy after a code change: `git pull`, then the same `gcloud run deploy` command.

## 8. Demo migration

The database tables come from `supabase/migrations/20261104000000_init.sql` (already applied if earlier phases ran against this Supabase project; on a new project run it first, the same way). Then add the `is_demo` column. Either paste `supabase/migrations/20261106000000_demo_calls.sql` into the Supabase SQL editor and run it, or from Cloud Shell with the **Session pooler** connection string (Supabase > Connect; the direct connection is IPv6 only):

```bash
command -v psql >/dev/null || sudo apt-get install -y postgresql-client
```

```bash
read -rsp "Session pooler connection string: " DB_URL; echo
psql "$DB_URL" -v ON_ERROR_STOP=1 -f supabase/migrations/20261106000000_demo_calls.sql; unset DB_URL
```

The migration is safe to run twice.

## 9. Seed the demo calls

Runs from Cloud Shell. The keys are read from Secret Manager and passed to the seeding process only, never exported:

```bash
python3 -m venv ~/callqa-venv && source ~/callqa-venv/bin/activate && pip install -q -e .
```

```bash
SUPABASE_URL="$(awk '/^SUPABASE_URL:/ {print $2}' ~/callqa-env.yaml)" LLM_PROVIDER=anthropic \
SUPABASE_SERVICE_KEY="$(gcloud secrets versions access latest --secret=supabase-service-key)" \
ANTHROPIC_API_KEY="$(gcloud secrets versions access latest --secret=anthropic-api-key)" \
python scripts/seed_demo.py
```

It scores the 10 sample scripts (synthetic data) and marks them `is_demo`. A call that fails is deleted again; if the provider is overloaded the script stops, and running it later adds the rest (`python scripts/seed_demo.py call_06 call_07` for specific ones). `python scripts/seed_demo.py --remove` deletes all demo calls.

## 10. Check it

```bash
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)') && echo "$URL"
```

```bash
curl -s "$URL/health"
```

`env` must be `production` and `configured` must show `true` for supabase, deepgram and anthropic.

```bash
curl -s -o /dev/null -w 'dashboard %{http_code}\n' "$URL/dashboard"
curl -s -o /dev/null -w 'upload without passcode %{http_code}\n' -F url=https://example.com/a.wav "$URL/calls"
curl -s -o /dev/null -w 'cold start %{time_total}s\n' "$URL/health"
```

Expected: `200` (demo dashboard), `401`. Run the last line again after 20 minutes without traffic to see the cold start time.

In a browser: `$URL/dashboard` shows the demo calls and the yellow demo bar. `$URL/login` with the passcode shows every call and a **Log out** button.

One real upload, with a synthetic sample clip (the response arrives when scoring is done, usually under a minute):

```bash
source ~/callqa-venv/bin/activate && pip install -q -e ".[audio]" && python scripts/make_sample_audio.py call_01
```

```bash
curl -s -H "X-Passcode: $(gcloud secrets versions access latest --secret=app-passcode)" \
  -F file=@samples/audio/call_01.wav -F rubric=us_insurance "$URL/calls"
```

Expected: `"status": "done"` with `overall_score` and `passed`. To remove that test call, run `delete from calls where filename = 'call_01.wav' and not is_demo;` in the Supabase SQL editor.

Logs: `gcloud run services logs read "$SERVICE" --region "$REGION" --limit 50`.

## Roll back

Every deploy is a new revision; older ones stay available.

```bash
gcloud run revisions list --service "$SERVICE" --region "$REGION"
```

```bash
gcloud run services update-traffic "$SERVICE" --region "$REGION" --to-revisions=<previous-revision>=100
```

While traffic is pinned, new deploys get no traffic. After a fixed deploy, send traffic to the newest revision again:

```bash
gcloud run services update-traffic "$SERVICE" --region "$REGION" --to-latest
```

Database migrations are not rolled back; the demo migration only adds a column, so older revisions keep working.

To take the site offline without deleting anything (the URL then answers 403):

```bash
gcloud run services remove-iam-policy-binding "$SERVICE" --region "$REGION" --member=allUsers --role=roles/run.invoker
```

Undo with `add-iam-policy-binding` and the same arguments.

## Operating it

- **Rate limit:** 5 failed attempts per address in 15 minutes, and 50 in total, then logins wait. The counters live in memory, so they reset when the instance scales to zero or restarts. The address comes from `X-Forwarded-For`, which a client can fake, so the per-address limit only keeps one visitor from locking out the rest; the overall cap is what bounds guessing.
- **Inline processing:** an upload holds its request open until scoring is done. If it times out or the instance is replaced mid-request, the call stays in `transcribing` or `scoring`; send it again, or call `POST /calls/<id>/score` once it has a transcript. Without `PROCESSING_MODE=inline`, background work would run with throttled CPU after the response and could stall.
- **Secret accesses:** each instance start reads every secret once (4 or 5 accesses), so the 10,000 free accesses cover about 2,000 cold starts a month.
- **Cost at portfolio traffic: $0 for Cloud Run.** The free tier is 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests a month per billing account. One inline upload uses about 30 vCPU-seconds, so the CPU allowance covers several thousand calls. Above the free tier, Tier 1 prices are $0.000024 per vCPU-second, $0.0000025 per GiB-second and $0.40 per million requests. Cloud Build includes 2,500 build-minutes a month per billing account. Artifact Registry is free up to 0.5 GB. Small costs that can appear: the uploaded source archives in Cloud Storage (a few MB per deploy) and outbound data to Deepgram and Supabase. Deepgram bills per audio minute. Claude Sonnet 5.5 costs $2 per million input tokens and $10 per million output tokens; each score row stores its tokens and cost, and the dashboard shows them.

### Rotate a secret

Add a version, start a new revision (secrets are read when an instance starts), then destroy the old version so it stops counting against the free tier. Disabled versions still count; destroyed ones do not. A new passcode also ends every login session.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24), end='')" | gcloud secrets versions add app-passcode --data-file=-
```

For an API key instead: `read -rsp "New key: " v; printf '%s' "$v" | gcloud secrets versions add deepgram-api-key --data-file=-; unset v`.

```bash
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars=SECRETS_ROTATED_AT=$(date +%s)
```

```bash
gcloud secrets versions list app-passcode
```

```bash
gcloud secrets versions destroy <old-version> --secret=app-passcode
```

## Delete everything

1. Remove the demo calls from Supabase (needs the Supabase key, so do it before deleting the project):

```bash
source ~/callqa-venv/bin/activate && SUPABASE_URL="$(awk '/^SUPABASE_URL:/ {print $2}' ~/callqa-env.yaml)" \
SUPABASE_SERVICE_KEY="$(gcloud secrets versions access latest --secret=supabase-service-key)" python scripts/seed_demo.py --remove
```

2. Delete the budget, which lives on the billing account, not the project:

```bash
gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" --format='value(name,displayName)'
```

```bash
gcloud billing budgets delete <budget-name> --billing-account="$BILLING_ACCOUNT"
```

3. Delete the project. This removes the service, secrets, images and buckets and stops billing for them. The project can be restored with `gcloud projects undelete` for 30 days, then it is gone.

```bash
gcloud projects delete "$PROJECT_ID"
```

4. Remove the local files: `rm -rf ~/callqa.env ~/callqa-env.yaml ~/ar-cleanup.json ~/callqa-venv`.

## Run the container locally

```bash
docker build -t call-qa-agent .
docker run --rm -p 8080:8080 --env-file .env call-qa-agent
```

Without `PROCESSING_MODE`, uploads answer at once and transcription and scoring run in the background, as with `uvicorn` locally.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Build fails with a permission error for the compute service account | The `roles/run.builder` grant from step 4 is missing or has not applied yet; wait two minutes and deploy again |
| Deploy fails with "Permission denied on secret" | The runtime account lacks `secretAccessor` on that secret; rerun the `add-iam-policy-binding` line for it |
| `--allow-unauthenticated` fails with "do not belong to a permitted customer" | An organization policy (domain restricted sharing) blocks public services; use a project outside that organization |
| Pages and API return 503 "APP_PASSCODE is not set" | The `app-passcode` secret is missing from `--set-secrets` |
| Upload answers 413 | The file is over `MAX_UPLOAD_MB` (Cloud Run's limit is 32 MiB); upload it somewhere and send `-F url=...` |
| Upload answers 504 after 900 s | Deepgram or the LLM was very slow; raise `--timeout` (up to 3600) or re-score later |
| Calls fail with "Supabase returned HTTP 401" | Wrong `supabase-service-key`; use a secret key, not the publishable key |
| Demo dashboard errors on a column `is_demo` | Step 8 has not been run |
| Calls fail with "provider returned HTTP 401" | The LLM key is wrong or revoked; add a new version of the secret and start a new revision. Check an Anthropic key with `curl -s -o /dev/null -w '%{http_code}\n' https://api.anthropic.com/v1/models -H "x-api-key: $KEY" -H "anthropic-version: 2023-06-01"` (expect 200). |
| Calls fail with "provider returned HTTP 503" | The LLM provider is overloaded; re-score later with `POST /calls/<id>/score` |
| Login answers 429 | Too many failed attempts; wait 15 minutes |
