# Mini Automations

[![mini-automations-python](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml) [![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml)

Small, working automations that each remove one manual task. Every automation that calls a model can use Claude (default) or Gemini, switched by one setting. Each folder has a README with the business problem, setup steps, `.env.example`, synthetic sample data, and tests or a dry-run mode that works without API keys.

| # | Automation | Problem | Type | Status |
| --- | --- | --- | --- | --- |
| 1 | [WhatsApp Lead Qualifier](whatsapp-lead-qualifier/) | Qualifies property leads on WhatsApp in English or Arabic and alerts sales on hot leads | n8n | Done |
| 2 | [Invoice to Sheet](invoice-to-sheet/) | Extracts supplier invoices (PDF or text) into Google Sheets with VAT and total checks | Python | Done |
| 3 | [Bilingual Email Triage](bilingual-email-triage/) | Labels Arabic and English emails, drafts replies in the sender's language, alerts on urgent ones | n8n | Done |
| 4 | [Appointment Reminders](appointment-reminders/) | Sends WhatsApp reminders the day before and 2 hours before, in English or Arabic, without duplicates | n8n | Done |
| 5 | [Price Monitor](price-monitor/) | Watches competitor product pages and alerts on price drops and stock changes | Python | Done |
| 6 | [CV Screener](cv-screener/) | Checks CVs against a job's requirements with quoted evidence and sorts them for review | Python | Done |
| 7 | [Daily Report Bot](daily-report-bot/) | Posts yesterday's sales vs the last week to Telegram with checked commentary | n8n | Done |
| 8 | [Sheet to CRM Sync](sheet-to-crm-sync/) | Upserts Google Sheets leads into HubSpot by email and writes each row's status back | n8n | Done |

All sample data is synthetic.

## Shared code, and where it is repeated on purpose

The three Python automations share one Gemini client, [`_shared/gemini.py`](_shared/gemini.py): the request, the retry rule for 429 and 5xx, and the refusal check. Each script adds that folder to its import path, so to run one folder on its own, copy the file next to the script. CI installs each one from its `requirements.lock`.

The n8n workflows repeat their small LLM helpers (`claudeRequest`, `geminiSchema`, `llmRequest`, `readLLM`) in each Code node that needs them. That is deliberate: an n8n workflow is one JSON file that someone imports into their own instance, and a Code node cannot import from another node or a shared file without a custom package installed on the server. A workflow that needed one would stop being importable by itself. The copies are the same except where one workflow needs something slightly different, and each workflow's tests cover its own copy against the mock APIs.

## Test bench

The n8n automations are tested against a local n8n with a mock server standing in for the Claude, WhatsApp, Telegram, Supabase, HubSpot and Google Sheets APIs. Requires Docker and Python 3.10+, no API keys. The Python automations (2, 5, 6) have their own pytest suites and need no Docker.

```bash
python3 _n8n/harness.py up      # start n8n + mock, load every */workflow.json
for t in */tests; do python3 -m unittest discover -s "$t"; done
python3 _n8n/harness.py down
```

`harness.py up` imports dummy credentials, rewrites API hosts in a temporary copy of each workflow to the mock (and swaps Google OAuth for a dummy header, since OAuth cannot be mocked), imports and publishes it. The bench's n8n also gets the environment variables the WhatsApp signature check needs. Workflows with an `llm_provider` setting are also loaded a second time with `gemini` selected, on webhook paths ending in `-gemini`, so both providers are tested. Tests cover two modes: dry run (`?dry_run=1`, no external calls) and live, where the mock records the outgoing requests so the tests can check their exact shape. n8n is on port 5679 and the mock on 8090.
