# Sheet to CRM Sync

[![mini-automations-n8n](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-n8n.yml)

**Problem.** The sales team keeps leads in a Google Sheet, someone copies them into the CRM by hand, and the two drift apart within a week.
This workflow upserts new and changed rows into HubSpot every 15 minutes and writes the result back into the sheet, so sales can see and fix their own errors.

Type: single n8n workflow ([workflow.json](workflow.json)). Stack: n8n, Google Sheets API, HubSpot CRM API.

**Model:** none. Mapping columns to CRM fields and validating emails and phone numbers is deterministic; an LLM would add cost and the chance of a wrong record.

## How it works

```
every 15 minutes   (or POST /webhook/sheet-crm-sync with Header Auth)
  -> Google Sheets: Leads!A1:H  (Name, Email, Phone, Company, Stage, Owner, Notes, CRM status)
  -> per row: email required and valid, lowercased; phone to E.164 (050..., 00971..., +966...);
              stage mapped to a HubSpot lifecycle stage; owner name mapped to a HubSpot owner ID;
              duplicate emails in the sheet rejected
  -> skip rows unchanged since their last successful sync (fingerprint per email in workflow static data)
  -> HubSpot: POST /crm/v3/objects/contacts/batch/upsert, idProperty = email, 100 rows per batch
  -> write "synced <time>" or "error: <reason>" into column H, only where the text changes
```

Upsert by email means re-running never creates duplicate contacts. A failed row is not remembered as synced, so it is retried on the next run once fixed. Status cells are only written when their text changes, so the sheet's edit history stays readable.

## Setup

1. Sheet: a tab named `Leads` with the header row `Name, Email, Phone, Company, Stage, Owner, Notes, CRM status`.
2. HubSpot: create a private app with `crm.objects.contacts.read` and `crm.objects.contacts.write` scopes.
3. Import `workflow.json` in n8n and create credentials:
   - **Google Sheets**: Google Sheets OAuth2 API (Google Cloud OAuth client with the Sheets API enabled), used by both sheet nodes.
   - **HubSpot private app**: Header Auth, name `Authorization`, value `Bearer <token>`.
   - **Sync webhook token**: Header Auth, name `X-Webhook-Token`.
4. Edit the settings block in **Prepare run**: spreadsheet ID, default country code, stage mapping, owner IDs (HubSpot **Settings > Users & Teams**). Publish.

## Dry run (no API keys)

POST the sheet values; the response shows the contacts that would be upserted, the HubSpot batches and the status cells that would be written. Nothing is read or sent.

```bash
curl -s -X POST "http://localhost:5678/webhook/sheet-crm-sync?dry_run=1" \
  -H 'content-type: application/json' -H 'X-Webhook-Token: <token>' \
  -d @samples/dry_run_request.json
```

The synthetic sample has 8 rows: 3 valid (a local UAE number, a Saudi number, an Arabic name with a `00971` number), an invalid email, an invalid phone, an unknown stage, a duplicate email and an empty row.

```
row 4  error: invalid email "priya.nair@example"
row 5  error: invalid phone "12345"
row 6  error: unknown stage "Hot", use New, Qualified, Opportunity, Customer
row 7  error: duplicate of row 2
```

## Tests

Runs on the shared test bench, see [../README.md](../README.md#test-bench). The test copy swaps the Google OAuth credential for a dummy header so the Sheets calls can go to the mock.

```bash
python3 -m unittest discover -s sheet-to-crm-sync/tests -v
```

6 tests: phone normalization and field mapping, readable errors per row, email as the upsert key, webhook token and no outbound calls in dry run, a full live sync (HubSpot batch, status write-back, then a second run that changes nothing and calls nothing), and a HubSpot 400 that marks rows as errors and retries them next run.

## Limits

- One-way sync, sheet to CRM. Changes made in HubSpot are not written back to the sheet.
- Sync state lives in workflow static data; if the workflow is re-imported, every row is upserted once more (harmless, because upserts match by email).
- Contacts only. Companies and deals would need their own mapping and association calls.

## Demo

[demo.gif placeholder: editing a row in Google Sheets, the contact updating in HubSpot, and the status cell turning to "synced"]
