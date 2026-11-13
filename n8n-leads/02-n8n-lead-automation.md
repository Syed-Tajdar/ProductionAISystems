# Project 2: n8n Lead-to-CRM Automation

> Build status: email intake (the Gmail trigger in Workflow A, step 1) is not built. Leads come in through the web form webhook only, and alerts go to Telegram only. The planned push-based route for email is described in the n8n-leads README under "Next step: email intake".

**One line for CV:** Self-hosted n8n workflow that captures leads from web forms and email, uses an LLM to classify and enrich them, stores them in Supabase, routes hot leads to sales on Telegram/WhatsApp, and follows up automatically.

## Problem

Small and mid-size Gulf businesses (real estate, clinics, e-commerce) get leads from forms, email and WhatsApp, and many go cold because nobody replies fast. This workflow replies in minutes and tells sales who to call first.

## Scope (v1)

Workflow A: Intake
1. Trigger: webhook from a web form (build a simple landing page form) and a Gmail trigger for a test inbox.
2. Normalize fields (name, phone with country code, email, message, source).
3. Deduplicate: look up by phone or email in Supabase; if found, update instead of insert.
4. LLM node (Claude or OpenAI): classify intent (buy, rent, info, spam), urgency (hot, warm, cold), language (EN/AR), and a one-line summary. Output as JSON.
5. Insert into Supabase `leads` table.
6. If hot: send Telegram (or WhatsApp Cloud API) alert to a "sales" chat with summary and a click-to-call link.
7. Send an auto-reply email in the lead's language.

Workflow B: Follow-up (scheduled)
1. Every hour, find leads with status `new` older than 24 hours.
2. Send a polite follow-up email; set status `followed_up`.
3. Daily 9 AM summary to Telegram: new leads, hot leads, conversion count.

Workflow C: Error handling
1. n8n Error Trigger workflow: post failures to a Telegram "ops" chat with workflow name, node and error message.

## Tech

- n8n self-hosted with Docker Compose (n8n + Postgres for n8n itself)
- Supabase for the leads data
- Claude or OpenAI node
- Telegram bot (free) and optional WhatsApp Cloud API
- Simple HTML landing page (Vercel) posting to the n8n webhook

## Data model (Supabase)

- `leads`: id, name, phone, email, source, message, intent, urgency, language, summary, status (new, followed_up, contacted, won, lost), created_at, updated_at
- `lead_events`: lead_id, event (created, classified, alerted, auto_replied, followed_up), payload jsonb, created_at

## Phases (gate after each)

1. Docker Compose for n8n, Supabase tables, landing page form.
2. Workflow A without LLM (normalize, dedupe, insert, alert).
3. Add LLM classification with JSON output and a fallback if the LLM fails (store as `unclassified`, still alert).
4. Workflow B and C.
5. Export all workflows as JSON into `/workflows`, write README with diagram, screenshots of each workflow, and a 2-minute video.

## Done means

- Submitting the form produces a Supabase row, an AI summary, and a Telegram alert within 30 seconds.
- Workflows JSON importable by anyone.
- Error workflow proven by forcing a failure on camera.
