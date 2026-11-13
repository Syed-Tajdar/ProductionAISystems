# Bilingual Email Triage

[![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml)

**Problem.** A shared inbox gets Arabic and English emails mixed with newsletters, and urgent complaints wait behind everything else.
This workflow classifies each new email, labels it, saves a draft reply in the sender's language, and pings Telegram when something is urgent.

Type: single n8n workflow ([workflow.json](workflow.json)). Stack: n8n, Gmail, Claude or Gemini, Telegram.

**Model:** `claude-haiku-5-5` by default (`anthropic_model` in **Normalize email**). One short classification per email at inbox volume suits the cheapest model, about a twentieth of Sonnet's price per token; switch to `claude-sonnet-5-5` if category or priority accuracy is not enough. Haiku 5.5 has no server-side refusal fallback, so a refusal falls back to the keyword rules; `fallbacks: "default"` is sent automatically for Sonnet or Opus.

**Provider:** `llm_provider` in **Normalize email** switches between `anthropic` (default) and `gemini`; `gemini_model` defaults to `gemini-3.5-flash-lite`, the Haiku-class Gemini model. Gemini uses `generateContent` with `responseMimeType: application/json` and the same schema (minus `additionalProperties`, which Gemini's `responseSchema` does not accept). A 429 is retried twice, 2 seconds apart, then the workflow uses its keyword-rule fallback.

## How it works

```
Gmail trigger (unread in INBOX, every minute)   or   POST /webhook/email-triage (Header Auth)
  -> normalize: sender, subject, body (max 4,000 chars)
  -> triage: category, priority, language, English summary, draft reply
       live: Claude with a JSON schema, keyword rules as fallback
       dry run: keyword rules and template drafts
  -> Gmail: add category label (+ urgent label), save draft reply in the thread
  -> urgent: Telegram alert with the English summary
```

Categories: sales, support, billing, complaint, newsletter, spam, other. Priority: urgent, normal, low. Drafts are only saved for sales, support, billing and complaints, and never sent automatically. The prompt forbids promising refunds, prices or dates.

The webhook entry point lets other systems (a contact form, another mailbox) use the same triage. It requires an `X-Webhook-Token` header. Whether an item came from Gmail is decided by which trigger fired, never by the payload, so a webhook caller cannot touch Gmail messages.

## Setup

1. Import `workflow.json` in n8n.
2. Create credentials and select them on the matching nodes:
   - **Anthropic API**: Header Auth, name `x-api-key`.
   - **Gmail**: Gmail OAuth2 (Google Cloud OAuth client with the Gmail API enabled), used by the trigger and both Gmail nodes.
   - **Telegram Bot**: bot token from BotFather.
   - **Triage webhook token**: Header Auth, name `X-Webhook-Token`, value a long random string.
3. In Gmail, create labels such as `Triage/Billing` and `Triage/Urgent`. Put their IDs in the `label_ids` map in **Normalize email** (leave a category empty to skip labelling it). The IDs are visible in the label dropdown of any Gmail node.
4. Set the Telegram chat ID and company name in the same settings block, then publish.

## Dry run (no API keys)

```bash
curl -s -X POST "http://localhost:5678/webhook/email-triage?dry_run=1" \
  -H 'content-type: application/json' -H 'X-Webhook-Token: <token>' \
  -d @samples/ar_complaint_urgent.json
```

The response shows the triage result, the labels and draft that would be applied, and the alert text. Nothing is sent. Five synthetic emails are in `samples/`: an English sales inquiry, an urgent Arabic complaint, a billing issue, an Arabic support request and a newsletter.

## Tests

Runs on the shared test bench, see [../README.md](../README.md#test-bench).

```bash
python3 -m unittest discover -s bilingual-email-triage/tests -v
```

13 tests: each category and language in dry run, newsletters get no draft, dry run makes no outbound calls, the webhook rejects calls without the token (403), a webhook payload cannot pose as a Gmail message, the live Claude request (Haiku, no fallback parameter) and Telegram alert, fallback to rules on invalid Claude output, no alert for normal emails, the Gemini request format, a Gemini 429 retried then answered with rules, and a Gemini safety block falling back to rules.

The Gmail trigger and Gmail nodes are not covered by the mock (they need Google OAuth); they only run in a real deployment.

## Limits

- Polls Gmail every minute. For instant triage, use Gmail push notifications through Pub/Sub instead.
- Only the plain-text body is read, capped at 4,000 characters; attachments are ignored.
- Drafts are saved, not sent. Auto-sending would need per-category rules and a review period.

## Demo

[demo.gif placeholder: an Arabic complaint arriving, the label and Arabic draft in Gmail, and the Telegram alert]
