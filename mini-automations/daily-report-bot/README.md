# Daily Report Bot

[![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml)

**Problem.** Every morning someone exports yesterday's sales, builds the same comparison in a spreadsheet and pastes it into the management group.
This workflow posts that report to Telegram at 08:45 with yesterday's numbers, the trend against the last week, and three short comments.

Type: single n8n workflow ([workflow.json](workflow.json)). Stack: n8n, Supabase, Claude or Gemini, Telegram.

**Model:** `claude-haiku-5-5` by default (`anthropic_model` in **Prepare run**). The workflow computes every number itself and Claude only phrases three bullets from them, so the cheapest model is enough; use `claude-sonnet-5-5` for more insightful commentary. Haiku 5.5 has no server-side refusal fallback; a refusal falls back to the template bullets.

**Provider:** `llm_provider` in **Prepare run** switches between `anthropic` (default) and `gemini`; `gemini_model` defaults to `gemini-3.5-flash-lite`, the Haiku-class Gemini model. Gemini uses `generateContent` with `responseMimeType: application/json` and the same schema (minus `additionalProperties`, which Gemini's `responseSchema` does not accept). A 429 is retried twice, 2 seconds apart, then the workflow uses its template bullets.

## How it works

```
every day at 08:45 (n8n timezone)   or   POST /webhook/daily-report (Header Auth)
  -> Supabase: daily_sales rows for the report day and the 7 days before
  -> yesterday: revenue, orders, average order, new leads
       vs previous day and vs 7-day average, per channel; flag channels that moved 30% or more
  -> Claude: 3 bullets from the computed facts only
       guard: any number not in the facts -> use template bullets instead
  -> Telegram message
```

Example (sample data):

```
Daily report, Monday 9 November 2026

Revenue: AED 23,760 (+5.2% vs 7-day avg)
Orders: 82 | Avg order: AED 289.76 | New leads: 39

walk_in: AED 3,360, 14 orders (-44.3%)
website: AED 12,600, 42 orders (+4.6%)
whatsapp: AED 7,800, 26 orders (+73.3%)

- Revenue was AED 23,760, +5.2% vs the 7-day average.
- Top channel: website with AED 12,600.
- Unusual: walk_in -44.3%, whatsapp +73.3% vs the 7-day average.
```

If there are no rows for yesterday, the message says so instead of reporting zeros, so a failed data load is noticed the same morning.

## Setup

1. Run `supabase/daily_sales.sql` in the Supabase SQL editor and load one row per day and channel from your POS, shop or CRM export.
2. Import `workflow.json` in n8n and create credentials: **Supabase** (Custom Auth, see `.env.example`), **Anthropic API** (Header Auth, `x-api-key`), **Telegram Bot**, **Report webhook token** (Header Auth, `X-Webhook-Token`).
3. Edit the settings block in **Prepare run** (Supabase URL, Telegram chat ID, company name, anomaly threshold). The schedule uses the n8n instance timezone (`GENERIC_TIMEZONE`).
4. Publish.

## Dry run (no API keys)

POST sample rows and the report date; the response contains the KPIs and the full message. Nothing is read or sent.

```bash
curl -s -X POST "http://localhost:5678/webhook/daily-report?dry_run=1" \
  -H 'content-type: application/json' -H 'X-Webhook-Token: <token>' \
  -d @samples/dry_run_request.json
```

`samples/dry_run_request.json` holds 8 synthetic days for three channels, with a WhatsApp spike and a walk-in drop on the last day.

## Tests

Runs on the shared test bench, see [../README.md](../README.md#test-bench).

```bash
python3 -m unittest discover -s daily-report-bot/tests -v
```

8 tests: KPIs checked against an independent Python calculation, anomalies and message text, missing-day message, webhook token and no outbound calls in dry run, the live path (Supabase query, Claude request with Haiku, Telegram post), invented numbers in Claude's bullets replaced by the template, Gemini bullets checked and posted, and a Gemini 429 retried then answered with the template.

## Limits

- Daily granularity only; the 7-day average treats every weekday the same, so expect weekend swings to show up as anomalies.
- The number guard is strict: a correctly rounded number that is not written exactly as in the facts also falls back to the template.
- One currency (AED) and one Telegram chat.

## Demo

[demo.gif placeholder: the 08:45 report arriving in a Telegram group]
