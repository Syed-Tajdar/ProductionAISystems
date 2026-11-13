# WhatsApp Lead Qualifier

[![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml)

**Problem.** Property leads message on WhatsApp at all hours, and sales spends the first call asking the same five questions.
This workflow asks them on WhatsApp in the lead's language (English or Arabic) and alerts sales only when a lead is ready to talk.

Type: single n8n workflow ([workflow.json](workflow.json)). Stack: n8n, WhatsApp Cloud API, Claude or Gemini, Telegram.

**Model:** `claude-haiku-5-5` by default (`anthropic_model` in **Parse message**). Pulling five fields out of a short chat message is a simple, high-volume task, and Haiku costs about a twentieth of Sonnet per token; switch to `claude-sonnet-5-5` if Gulf Arabic extraction misses too often. Haiku 5.5 has no server-side refusal fallback, so a refusal falls back to the keyword rules; `fallbacks: "default"` is sent automatically for Sonnet or Opus.

**Provider:** `llm_provider` in **Parse message** switches between `anthropic` (default) and `gemini`; `gemini_model` defaults to `gemini-3.5-flash-lite`, the Haiku-class Gemini model. Gemini uses `generateContent` with `responseMimeType: application/json` and the same schema (minus `additionalProperties`, which Gemini's `responseSchema` does not accept). A 429 is retried twice, 2 seconds apart, then the workflow uses its keyword-rule fallback.

## How it works

```
WhatsApp Cloud API webhook (POST /webhook/whatsapp-lead)
  -> verify X-Hub-Signature-256 (HMAC SHA-256 of the raw body), reject with 401 if wrong
  -> parse message, load the lead's profile (workflow static data, keyed by phone)
  -> extract intent, area, bedrooms, budget, timeline
       live: Claude with a JSON schema, keyword rules as fallback
       dry run: keyword rules only
  -> merge into the profile, score: hot / warm / cold / spam
  -> reply with the next missing question in the lead's language
  -> hot and not alerted yet: Telegram alert with a wa.me link
GET on the same path answers Meta's webhook verification.
```

Claude only extracts fields. The reply text comes from fixed templates, so the bot never promises prices or availability.

## Setup

1. Import `workflow.json` in n8n (**Workflows > Import from file**).
2. Create three credentials and select them on the matching nodes:
   - **Anthropic API**: Header Auth, name `x-api-key`, value your API key.
   - **WhatsApp Cloud API**: Header Auth, name `Authorization`, value `Bearer <permanent access token>`.
   - **Telegram Bot**: Telegram API, bot token from BotFather.
3. Edit the settings block at the top of the **Parse message** node: business name, WhatsApp phone number ID, Telegram sales chat ID, model.
4. Change `VERIFY_TOKEN` in **Check verify token**.
5. Set three environment variables on the n8n server and restart it:
   - `WHATSAPP_APP_SECRET`: Meta app dashboard, **App settings > Basic > App secret**.
   - Optional `WHATSAPP_DRY_RUN_TOKEN`: a long random value that turns dry runs on (see Dry run). Leave it unset in production.
   - `NODE_FUNCTION_ALLOW_BUILTIN=crypto`: lets the Code node compute the HMAC.
   - `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`: lets the Code node read the secret. This exposes environment variables to every workflow, so only do it on an instance where you trust everyone who can edit workflows.
6. Publish the workflow. In the Meta app dashboard (**WhatsApp > Configuration**), set the callback URL to `https://<your-n8n>/webhook/whatsapp-lead` with the same verify token, and subscribe to `messages`.

`.env.example` lists every value you need.

## Signature check

Meta signs every webhook call with `X-Hub-Signature-256: sha256=<HMAC SHA-256 of the raw body, keyed with the app secret>`. **Verify signature** recomputes it over the exact bytes received (the webhook's raw body option is on) and compares in constant time. A missing or wrong signature gets a 401 and nothing else runs. If the secret is not set, every live call is rejected rather than accepted unchecked. Dry runs skip this check and need their own token instead (below).

## Dry run (no API keys)

Add `?dry_run=1` and send the header `X-Dry-Run-Token` with the value of `WHATSAPP_DRY_RUN_TOKEN` on the n8n server. Nothing is sent and nothing is stored; the response shows the extracted profile, score, reply and alert text. A missing or wrong token gets a 401, and without the variable dry runs are off, so the public webhook URL cannot be used to run the workflow for free.

```bash
curl -s -X POST "http://localhost:5678/webhook/whatsapp-lead?dry_run=1" \
  -H 'content-type: application/json' -H "X-Dry-Run-Token: $WHATSAPP_DRY_RUN_TOKEN" -d @samples/en_first_message.json
```

A dry run keeps no profile between calls. To try a conversation, put the `profile` from one answer into the next request's body next to `entry`.

Sample payloads in `samples/` use the WhatsApp Cloud API webhook format: an English two-message conversation, an Arabic message, spam and a delivery status update.

## Tests

Runs on the shared test bench (n8n plus a mock of the Claude, WhatsApp and Telegram APIs), see [../README.md](../README.md#test-bench).

```bash
python3 -m unittest discover -s whatsapp-lead-qualifier/tests -v
```

13 tests: webhook verification, status updates ignored, English conversation scored warm then hot, Arabic reply, spam, signatures (valid accepted; wrong secret, tampered body and missing header rejected with 401 and no outbound calls), live path request formats (Claude with Haiku and no fallback parameter, WhatsApp, Telegram), fallback to rules when Claude returns 529, the Gemini request format, and a Gemini 429 retried then answered with rules.

## Limits

- Lead profiles live in n8n workflow static data. Fine for a few thousand leads; move them to a database table beyond that.
- Free-form replies are only allowed within 24 hours of the lead's last message (WhatsApp rule). Follow-ups after that need an approved template.
- Keyword rules cover common English and Gulf Arabic phrasing only. Spelled-out numbers ("مية وعشرين ألف") need Claude.

## Demo

[demo.gif placeholder: a WhatsApp conversation going from first message to the Telegram hot lead alert]
