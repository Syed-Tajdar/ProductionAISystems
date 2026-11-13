# Invoice to Sheet

[![mini-automations-python](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml)

**Problem.** Small businesses retype every supplier invoice into a spreadsheet, and typos in totals or VAT only surface at month end.
This script reads a folder of invoice PDFs or text files, extracts the header fields, checks the maths and VAT, and appends new rows to a Google Sheet.

Type: Python script, 299 lines ([invoice_to_sheet.py](invoice_to_sheet.py)). Stack: Python 3.10+, Claude or Gemini, Google Sheets (gspread).

**Model:** `claude-sonnet-5-5` by default (`ANTHROPIC_MODEL`). PDF layouts vary and a misread total costs more than the API call, so this uses the more accurate model; `claude-haiku-5-5` is about 20x cheaper per token and fine for clean, digital invoices.

**Provider:** `LLM_PROVIDER=gemini` switches from Claude to Gemini; `GEMINI_MODEL` defaults to `gemini-3.8-flash`, the Sonnet-class Gemini model. Gemini uses `generateContent` with `responseMimeType: application/json` and the same schema (minus `additionalProperties`, which Gemini's `responseSchema` does not accept). A 429 from either provider gets a short backoff (Gemini: 2 retries at 1 s and 2 s, honouring `Retry-After` up to 5 s; Claude: the SDK's own retries), then text invoices fall back to the keyword rules (source `rules (rate limited)`) and PDFs are reported as failed for the next run.

## How it works

```
folder of .pdf / .txt
  -> extract: Claude with a JSON schema (live) or "Label: value" rules (dry run)
  -> validate: required fields, subtotal + VAT = total, VAT = 5% of subtotal for AED
  -> skip duplicates: same supplier TRN (or name) + invoice number already in the sheet
  -> append rows with status ok / check and the list of issues
```

Columns: file, supplier, supplier_trn, invoice_number, invoice_date, due_date, currency, subtotal, vat, total, status, issues, source.

PDFs go to Claude as document blocks, so scanned and digital PDFs both work. One failing file is reported and skipped; the rest of the batch still runs.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # then fill in, and export the variables
```

Google Sheets:
1. In Google Cloud, create a service account and download its JSON key as `service-account.json` (keep it out of git).
2. Enable the Google Sheets API for the project.
3. Share the target sheet with the service account email (Editor). Create a tab named `Invoices`; the header row is added on first run.

## Dry run (no API keys)

Keyword rules instead of Claude, CSV instead of Google Sheets. PDFs are skipped because reading them needs Claude.

```bash
python invoice_to_sheet.py samples/invoices --dry-run
```

```
ok     inv_001_desert_line.txt: Desert Line Office Supplies LLC DL-2026-0412 1102.5
check  inv_002_gulf_fresh.txt: Gulf Fresh Catering GFC-7781 3410.0
ok     inv_003_elite_bilingual.txt: شركة النخبة للصيانة | Elite Maintenance Co. EM-1093 2520.0
dup    inv_004_desert_line_resent.txt: DL-2026-0412 already recorded
skip   inv_005_blue_harbor.pdf: PDFs need Claude (live mode)
{"added": 3, "duplicates": 1, "needs_check": 1, "failed": 0}
```

Live: `python invoice_to_sheet.py inbox/` (Google Sheet) or `python invoice_to_sheet.py inbox/ --csv out/invoices.csv`.

Sample invoices in `samples/invoices/` are synthetic: an English invoice, one with a wrong total, a bilingual Arabic/English invoice, a resent duplicate and a PDF.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

13 tests: rule extraction (English and bilingual), validation, dry run end to end including re-run idempotency, Google Sheets sink with a fake worksheet, the exact Claude request (Sonnet, JSON schema, PDF document block, fallback header) against a local fake API, no fallback parameter when Haiku is chosen, refusal handling, Claude 429 falling back to rules, the Gemini request (model in the URL, `x-goog-api-key`, JSON mode, schema, PDF as `inlineData`), Gemini 429 with two short waits then rules, and a clear error when the Gemini key is missing.

## Limits

- Header fields only, no line items.
- Duplicate check reads the whole sheet each run; fine up to tens of thousands of rows.
- The rules extractor expects "Label: value" lines; anything else needs Claude.

## Demo

[demo.gif placeholder: dropping invoices into the folder and the rows appearing in Google Sheets]
