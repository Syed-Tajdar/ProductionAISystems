# Price Monitor

[![mini-automations-python](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml/badge.svg?branch=main)](https://github.com/Syed-Tajdar/ProductionAISystems/actions/workflows/mini-automations-python.yml)

**Problem.** E-commerce teams check competitor prices by opening tabs every morning, and a price cut on a key product is noticed days late.
This script checks a watchlist of competitor product pages, keeps a price history, and sends a Telegram alert only when something changes.

Type: Python script, 280 lines ([price_monitor.py](price_monitor.py)). Stack: Python 3.10+ standard library, Claude or Gemini (fallback only), Telegram.

**Model:** `claude-haiku-5-5` by default (`ANTHROPIC_MODEL`), and only for pages without structured price data. Most shops publish the price in JSON-LD or meta tags, which costs nothing to read; for the rest, finding one number in a page is a simple task where Haiku's price (about a twentieth of Sonnet's) matters more than extra accuracy. Haiku 5.5 has no server-side refusal fallback; a refusal is reported as "price not found".

**Provider:** `LLM_PROVIDER=gemini` switches from Claude to Gemini; `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`, the Haiku-class Gemini model. Gemini uses `generateContent` with `responseMimeType: application/json` and the same schema (minus `additionalProperties`, which Gemini's `responseSchema` does not accept). A 429 from either provider gets a short backoff (Gemini: 2 retries at 1 s and 2 s, honouring `Retry-After` up to 5 s; Claude: the SDK's own retries), then the product is skipped for this run (counted as `rate_limited`, no false "not found" alert, history untouched).

## How it works

```
watchlist.csv (id, name, url, our_price, currency, drop_alert_pct)
  -> robots.txt check, fetch page (2 s between requests)
  -> price: JSON-LD offers -> product meta tags -> Claude or Gemini on the visible page text
  -> compare with the last stored price
  -> alerts: drop >= drop_alert_pct, newly below our price, out of stock, back in stock, price not found
  -> Telegram, and save history (last 30 checks per product) to state/prices.json
```

Alerts fire on changes only: a competitor that stays below our price is reported once, not every run. Claude gets the visible text (scripts and styles removed) and is asked for the current price, not the crossed-out one.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in, then export the variables
python price_monitor.py watchlist.csv
```

Run it from cron, for example every morning at 07:00:

```
0 7 * * * cd /opt/price-monitor && .venv/bin/python price_monitor.py watchlist.csv >> monitor.log 2>&1
```

Only monitor pages you are allowed to access. The script obeys robots.txt and waits 2 seconds between requests.

## Dry run (no API keys, no network)

Reads saved pages from `samples/pages/<id>.html`, starts from `samples/state_before.json`, and prints alerts instead of sending them.

```bash
python price_monitor.py samples/watchlist.csv --dry-run
```

```
ALERT  tv-55: AED 1,149.00 (json-ld)
ok     kettle: AED 349.00 (meta)
ALERT  laptop-14: AED 2,199.00 (json-ld)
ALERT  headset: not found (-)
...
Competitor price drop: Falcon 55-inch 4K TV
AED 1,299.00 -> AED 1,149.00 (-11.5%)
{"checked": 4, "alerts": 4, "not_found": 1, "errors": 0}
```

The headset page has no structured data, so the dry run reports it as not found; live mode asks Claude. Delete `out/prices.json` to start the dry run over. All shops and products in `samples/` are synthetic.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

6 tests: structured extraction (JSON-LD object, JSON-LD list, meta tags), alert rules, dry run with a quiet second run, and a full live run against one local server acting as the shops, the Claude API and Telegram (robots.txt respected, Claude only called for the page without structured data, exact request with Haiku and no fallback parameter, 4 Telegram alerts), the same live run on Gemini, and a Gemini 429 that skips the product without a false alert.

## Limits

- Pages that render prices with JavaScript only need a headless browser; this reads the HTML as served.
- State is a JSON file. For many products or several machines, move it to a database table.
- One currency per product; no conversion between currencies.

## Demo

[demo.gif placeholder: the morning run in a terminal and the price drop alert arriving in Telegram]
