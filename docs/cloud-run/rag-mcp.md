# Deploying to Cloud Run

Alternative to the current hosting (Vercel, see [rag-mcp/DEPLOY.md](../../rag-mcp/DEPLOY.md) and [the hosting decision](../hosting-decision.md)). Commands run from the `rag-mcp` folder.

Not deployed yet. The page and JSON API run as one Cloud Run service, set up like [call-qa-agent](call-qa-agent.md): same project and region (`europe-west1`), request-based billing, 0 to 1 instances. Everything happens inside the request, so CPU throttling after the response does not matter, and one instance keeps the demo limits (in memory) exact.

The steps below assume the call-qa-agent guide has been followed up to its step 4: project, billing, budget, APIs and the `call-qa-run` service account exist, and `~/callqa.env` holds `PROJECT_ID`, `REGION` and `RUN_SA`.

## 1. Database

Postgres with pgvector: a Supabase project (Connect > Session pooler connection string), or any Postgres 15+ with pgvector 0.7+. Apply `supabase/migrations/20261109000000_rag.sql` once (Supabase SQL editor, or `psql "$DB_URL" -f ...`).

Ingest from Cloud Shell (embeddings are cached in the database, so re-runs cost nothing):

```bash
cd ~/ProductionAISystems/rag-mcp && python3 -m venv ~/rag-venv && source ~/rag-venv/bin/activate && pip install -q -e .
```

```bash
read -rsp "Session pooler connection string: " DB_URL; echo
DATABASE_URL="$DB_URL" GEMINI_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key)" python -m rag.cli ingest
```

## 2. Secrets

The Gemini and Anthropic keys are the same secrets call-qa-agent uses (create `gemini-api-key` with its `add_secret` helper if it is not there yet). Only the connection string is new, which keeps the project within Secret Manager's 6 free versions:

```bash
printf '%s' "$DB_URL" | gcloud secrets create rag-database-url --replication-policy=automatic --data-file=- && unset DB_URL
gcloud secrets add-iam-policy-binding rag-database-url --member="serviceAccount:$RUN_SA" \
  --role=roles/secretmanager.secretAccessor --condition=None --format=none
```

## 3. Deploy

```bash
cat > ~/rag-env.yaml <<'EOF'
DEMO_QUESTIONS_PER_CLIENT: "5"
DEMO_WINDOW_SECONDS: "600"
DEMO_DAILY_CAP: "150"
EOF
```

```bash
gcloud run deploy rag-docs --source . --region "$REGION" \
  --service-account "$RUN_SA" --allow-unauthenticated \
  --cpu-throttling --cpu-boost --cpu 1 --memory 512Mi \
  --min-instances 0 --max-instances 1 --concurrency 20 --timeout 120 \
  --env-vars-file ~/rag-env.yaml \
  --set-secrets "DATABASE_URL=rag-database-url:latest,GEMINI_API_KEY=gemini-api-key:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest"
```

## 4. Check it

```bash
URL=$(gcloud run services describe rag-docs --region "$REGION" --format='value(status.url)')
curl -s "$URL/health"                     # configured: database, embeddings and answers true
```

Open `$URL/` and click an example question; the second click of the same example comes from the answer cache.

## Cost and limits

- **Cloud Run:** inside the free tier at demo traffic (one answer takes 2 to 8 s of one vCPU).
- **Anthropic:** a question costs about $0.0064 (Sonnet answer plus Haiku translation, at list price). The daily cap of 150 questions bounds it at about $1 a day; repeated questions come from the cache. Also set a monthly spend limit in the Claude Console as the hard stop.
- **Gemini:** one cached query embedding per new question, within the free tier.
- The per-address limit uses `X-Forwarded-For`, which a client can fake; the daily cap is what bounds spending.

## The MCP server on Cloud Run (optional)

The same image runs the MCP server over HTTP with a different command. It needs its own bearer token, stored as a secret (`openssl rand -hex 32 | gcloud secrets create rag-mcp-token --data-file=-`, then grant access as above):

```bash
gcloud run deploy rag-mcp --source . --region "$REGION" --service-account "$RUN_SA" --allow-unauthenticated \
  --cpu-throttling --min-instances 0 --max-instances 1 --timeout 120 \
  --command rag-mcp --args="--transport,http,--host,0.0.0.0,--port,8080" \
  --set-secrets "DATABASE_URL=rag-database-url:latest,GEMINI_API_KEY=gemini-api-key:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest,MCP_BEARER_TOKEN=rag-mcp-token:latest"
```

Clients then connect to `https://<service-url>/mcp` with `Authorization: Bearer <token>`. The token is a seventh secret version, beyond the free six (about $0.06 a month).
