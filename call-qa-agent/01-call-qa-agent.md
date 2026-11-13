# Project 1: Call QA Agent

**One line for CV:** AI agent that transcribes sales/support calls, scores them against a compliance and quality rubric with quoted evidence, and shows results on a dashboard. Python, FastAPI, Deepgram, Claude/OpenAI, Supabase.

## Problem

Call centers and lead-gen companies review only a small sample of calls by hand. Missed disclosures and bad agent behaviour cost money and create legal risk. This tool reviews every call automatically and points to the exact line that passed or failed.

## Scope (v1)

1. Upload an audio file (mp3/wav) or paste a public URL.
2. Transcribe with Deepgram (speaker diarization on, English + Arabic models).
3. Send transcript to an LLM with a rubric. Get back structured JSON.
4. Store call, transcript, scores in Supabase (Postgres).
5. Dashboard: list of calls, overall score, pass/fail per rubric item, click to see quoted evidence with timestamps.
6. Rubric is editable (YAML file or a settings page). Ship 3 sample rubrics: US insurance call (disclosure + consent), Gulf real-estate inquiry (Arabic/English), customer support call.

Out of scope for v1: live call streaming, phone system integration, user accounts beyond a single passcode.

## Architecture

```
[Upload UI (Next.js or simple HTML)]
        |
        v
[FastAPI backend] --(async)--> [Deepgram STT]
        |                          |
        |<------ transcript -------+
        v
[Scoring service] --> [Claude or OpenAI, JSON schema output]
        |
        v
[Supabase: calls, transcripts, scores, rubric_items]
        |
        v
[Dashboard]
```

## Data model (Supabase)

- `calls`: id, filename, duration_sec, language, status (queued, transcribing, scoring, done, failed), created_at
- `transcripts`: call_id, utterances jsonb (speaker, start, end, text)
- `rubrics`: id, name, version, items jsonb
- `scores`: call_id, rubric_id, overall_score, items jsonb (item_id, verdict pass/fail/na, evidence_quote, timestamp, reasoning), model, latency_ms, cost_usd

## LLM scoring contract

- Use structured output (JSON schema / tool call), never free text parsing.
- Each rubric item must return `verdict`, `evidence_quote` (exact words from transcript), `start_time`.
- Validate: if `evidence_quote` is not found in the transcript, mark the item `needs_review` (hallucination guard).
- Retry with exponential backoff on 429/5xx. Log tokens, latency and cost per call.
- Provider switch via env var: `LLM_PROVIDER=anthropic|openai`.

## Sample data

Write 10 short scripted calls (60 to 120 seconds) with known good/bad behaviour, for example an agent who forgets the recording disclosure. Generate audio with a TTS API (two different voices). Keep the scripts in `/samples/scripts` with the expected verdicts in `/samples/expected.json`.

## Evals (this is what impresses reviewers)

- `python -m evals.run` scores all 10 sample calls and compares to `expected.json`.
- Print a table: accuracy per rubric item, overall accuracy, average cost and latency per call.
- Put the final eval table in the README.

## Phases (gate after each)

1. Repo scaffold, FastAPI health check, Supabase schema migration, `.env.example`.
2. Deepgram transcription with diarization; store utterances.
3. Rubric loader + LLM scoring with JSON schema + evidence validation.
4. Dashboard (list + detail view with highlighted evidence).
5. Sample calls + eval runner + README with results, architecture diagram, demo video link.
6. Deploy (Render/Railway for API, Vercel for UI), passcode-protected uploads.

## Done means

- A stranger can open the demo, pick a sample call and see scores in under 1 minute.
- Eval table is in the README with real numbers.
- No secrets in the repo; spend cap set on API accounts.
