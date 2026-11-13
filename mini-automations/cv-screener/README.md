# CV Screener

[![mini-automations-python](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml)

**Problem.** Recruiters spend hours on first-pass CV screening, and different reviewers apply the same job requirements differently.
This script checks every CV against the job's requirements, quotes the evidence for each verdict, and sorts candidates for a person to review.

Type: Python script, 257 lines ([cv_screener.py](cv_screener.py)). Stack: Python 3.10+, Claude or Gemini, pypdf.

**Model:** `claude-sonnet-5-5` by default (`ANTHROPIC_MODEL`). Telling "Arabic (basic)" from "fluent Arabic", or a support role from a sales role, is judgment, and a wrong rejection costs a good candidate; at CV volumes Sonnet costs roughly a cent per CV (estimate), so accuracy wins over the cheaper Haiku.

**Provider:** `LLM_PROVIDER=gemini` switches from Claude to Gemini; `GEMINI_MODEL` defaults to `gemini-3.8-flash`, the Sonnet-class Gemini model. Gemini uses `generateContent` with `responseMimeType: application/json` and the same schema (minus `additionalProperties`, which Gemini's `responseSchema` does not accept). A 429 from either provider gets a short backoff (Gemini: 2 retries at 1 s and 2 s, honouring `Retry-After` up to 5 s; Claude: the SDK's own retries), then the CV is scored with the keyword rules and always sent to `review`.

## How it works

```
job.yaml (requirements: must / nice, min_years or keywords)   +   folder of CVs (.txt, .md, .pdf)
  -> read text (pypdf for PDFs)
  -> redact: email, phone, links, and lines with date of birth, age, nationality, gender, marital status, religion
  -> assess each requirement: met / partial / missing + exact quote from the CV
       live: Claude with a JSON schema (requirement ids as an enum)
       dry run: keyword rules and a years-of-experience regex
  -> quote check: a quote that is not in the CV becomes needs_review
  -> score: must = 1, nice = 0.5; met 1, partial 0.5
  -> decision: shortlist / review / not shortlisted
  -> out/ranking.csv and out/shortlist.md with the quotes
```

Decisions: **shortlist** when no must-have is missing and the score reaches `shortlist_score`; **review** when no must-have is missing but the score is lower, or any quote could not be verified; **not shortlisted** when a must-have is missing. The output says it in plain words: the script sorts, a person decides.

## Fairness and privacy

- Contact details and demographic lines are removed before the CV reaches Claude or the report. The prompt also tells the model to ignore names and personal characteristics.
- Every verdict needs a quote from the CV. Invented evidence is caught by the quote check and sent to a person.
- Names cannot be fully removed by rules; review shortlists blind (by file name) if that matters for your process.
- CVs are personal data under the UAE and KSA personal data protection laws. Check your obligations before automating screening, keep CVs only as long as needed, and tell candidates how they are processed.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python cv_screener.py job.yaml cvs/
```

Write `job.yaml` like `samples/job.yaml`: each requirement has an `id`, `type` (must or nice) and `text` for Claude; `keywords` or `min_years` are only used by the dry run.

## Dry run (no API keys)

```bash
python cv_screener.py samples/job.yaml samples/cvs --dry-run
```

```
shortlist        100.0  cv_01_layla.txt
not shortlisted   50.0  cv_02_arjun.txt
review            62.5  cv_03_omar.txt
shortlist         87.5  cv_04_daniel.pdf
shortlist         87.5  cv_05_noura.txt
```

Five synthetic CVs for a Customer Success Specialist role in Dubai, one of them a PDF and one bilingual Arabic/English. The dry run shows why the model is needed: keyword rules count Daniel's "Arabic (basic)" as meeting "fluent Arabic"; Claude marks it partial.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

10 tests: redaction (and date ranges not mistaken for phone numbers), PDF reading, rule verdicts, the quote check, scoring rules, dry run outputs, and the Claude request against a local fake API (Sonnet with fallback, requirement ids as enum, redacted text, fairness instruction, invented quote sent to review), Claude 429 sending the CV to review, the Gemini request, and Gemini 429 with two short waits then review.

## Limits

- Text only: scanned image PDFs need OCR first.
- One job per run; CVs in languages other than Arabic and English were not tested.
- Years of experience come from what the CV states, not from computing date ranges.

## Demo

[demo.gif placeholder: screening the sample folder and opening shortlist.md with quoted evidence]
