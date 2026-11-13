"""Track competitor prices on product pages and alert on Telegram when they move.

    python price_monitor.py samples/watchlist.csv --dry-run    # saved pages, prints alerts
    python price_monitor.py watchlist.csv                       # fetch pages, send Telegram alerts
LLM_PROVIDER=gemini switches the price fallback from Claude to Gemini.
"""

import argparse
import csv
import html as htmllib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path

# The Gemini request and the rate-limit error are shared with the other Python automations.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from gemini import RateLimited, gemini_json  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; price-monitor/1.0)"
HISTORY = 30
LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["found", "price", "currency", "in_stock"],
    "properties": {
        "found": {"type": "boolean", "description": "False if the page shows no current price"},
        "price": {"type": "number", "description": "Current selling price, not the old or crossed-out price"},
        "currency": {"type": "string", "description": "ISO code such as AED"},
        "in_stock": {"type": "boolean"},
    },
}

# What can go wrong with one product page: it cannot be read or parsed, the model call fails, or the answer has the wrong shape.
PAGE_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)

def _offers(node):
    """Yield offer dicts from any JSON-LD structure."""
    if isinstance(node, list):
        for n in node:
            yield from _offers(n)
    elif isinstance(node, dict):
        if "price" in node or "lowPrice" in node:
            yield node
        for key in ("offers", "@graph", "mainEntity"):
            if key in node:
                yield from _offers(node[key])


def extract_structured(page: str) -> dict | None:
    """Price from JSON-LD offers or product meta tags. Most shops publish one of them."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for offer in _offers(data):
            price = offer.get("price", offer.get("lowPrice"))
            if price not in (None, ""):
                return {"price": float(str(price).replace(",", "")), "currency": offer.get("priceCurrency", ""),
                        "in_stock": "outofstock" not in str(offer.get("availability", "")).lower(), "source": "json-ld"}
    meta = dict(re.findall(r'<meta[^>]+(?:property|itemprop)="([^"]+)"[^>]+content="([^"]*)"', page, re.I))
    amount = meta.get("product:price:amount") or meta.get("price")
    if amount:
        return {"price": float(amount.replace(",", "")),
                "currency": meta.get("product:price:currency") or meta.get("priceCurrency", ""),
                "in_stock": "out" not in meta.get("product:availability", "in stock").lower(), "source": "meta"}
    return None


def page_text(page: str, limit: int = 6000) -> str:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", page))).strip()[:limit]


def extract_with_llm(page: str, name: str, client=None) -> dict | None:
    """Price from the page text with the provider chosen by LLM_PROVIDER (anthropic or gemini)."""
    provider = os.environ.get("LLM_PROVIDER") or "anthropic"
    prompt = f"Product: {name}\nFind the current price on this product page.\n\n{page_text(page)}"
    if provider == "gemini":
        # Flash-Lite: the Haiku-class option for the same simple lookup.
        model = os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"
        try:
            data = gemini_json(model, "You read prices from product pages.", [{"text": prompt}], LLM_SCHEMA)
        except RuntimeError as e:
            if isinstance(e, RateLimited):
                raise
            return None  # declined
        return {**data, "source": "gemini"} if data.get("found") else None
    if provider != "anthropic":
        raise ValueError(f"LLM_PROVIDER must be anthropic or gemini, not {provider!r}")
    import anthropic

    try:
        return extract_with_claude(prompt, client or anthropic.Anthropic())
    except anthropic.RateLimitError as e:  # the SDK has already retried with backoff
        raise RateLimited("Claude rate limit (429) after retries") from e
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude request failed: {e}") from e


def extract_with_claude(prompt: str, client) -> dict | None:
    # Haiku: short, simple extraction on every page without structured data. No server-side fallback on Haiku 5.5.
    model = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-5-5"
    extra = {} if model.startswith("claude-haiku") else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    response = client.beta.messages.create(
        model=model, max_tokens=1024, **extra,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": LLM_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        return None
    data = json.loads(next(b.text for b in reversed(response.content) if b.type == "text"))
    return {**data, "source": "claude"} if data.get("found") else None


def allowed_by_robots(url: str, cache: dict) -> bool:
    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(url))
    if root not in cache:
        rp = urllib.robotparser.RobotFileParser(root + "/robots.txt")
        try:
            rp.read()
        except OSError:
            rp = None  # unreachable robots.txt: allow, like most crawlers
        cache[root] = rp
    return cache[root] is None or cache[root].can_fetch(USER_AGENT, url)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def money(currency: str, value: float) -> str:
    return f"{currency} {value:,.2f}".strip()


def compare(item: dict, found: dict | None, last: dict | None) -> list[str]:
    name, url = item["name"], item["url"]
    if not found:
        return [] if last and last.get("missing") else [f"Price not found: {name}\n{url}"]
    cur, price, ours = found["currency"] or item["currency"], found["price"], float(item["our_price"])
    alerts = []
    if last and not last.get("missing"):
        old = last["price"]
        drop = (old - price) / old * 100 if old else 0
        if drop >= float(item["drop_alert_pct"]):
            alerts.append(f"Competitor price drop: {name}\n{money(cur, old)} -> {money(cur, price)} (-{drop:.1f}%)")
        if last.get("in_stock", True) and not found["in_stock"]:
            alerts.append(f"Competitor out of stock: {name}")
        if not last.get("in_stock", True) and found["in_stock"]:
            alerts.append(f"Competitor back in stock: {name} at {money(cur, price)}")
    was_below = bool(last) and not last.get("missing") and last["price"] < ours
    if price < ours and not was_below:
        alerts.append(f"Competitor now below our price: {name}\nThem {money(cur, price)}, us {money(cur, ours)}")
    return [f"{a}\n{url}" for a in alerts]


def send_telegram(text: str) -> None:
    base = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")
    url = f"{base}/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
    body = json.dumps({"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    urllib.request.urlopen(req, timeout=20).close()


def run(watchlist: Path, state_path: Path, dry_run: bool, pages: Path | None = None, client=None) -> dict:
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    items = list(csv.DictReader(watchlist.open(encoding="utf-8")))
    robots, alerts, summary = {}, [], {"checked": 0, "alerts": 0, "not_found": 0, "rate_limited": 0, "errors": 0}
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for i, item in enumerate(items):
        try:
            if dry_run:
                page = (pages / f"{item['id']}.html").read_text(encoding="utf-8")
            else:
                if not allowed_by_robots(item["url"], robots):
                    print(f"skip   {item['id']}: robots.txt disallows {item['url']}")
                    continue
                if i:
                    time.sleep(2)  # be polite to shared hosts
                page = fetch(item["url"])
            found = extract_structured(page)
            if not found and not dry_run:
                found = extract_with_llm(page, item["name"], client)
        except RateLimited as e:  # skip this product without a false "not found" alert; next run retries
            summary["rate_limited"] += 1
            print(f"skip   {item['id']}: {e}")
            continue
        except PAGE_ERRORS as e:  # one bad page must not stop the run
            summary["errors"] += 1
            print(f"error  {item['id']}: {e}", file=sys.stderr)
            continue
        summary["checked"] += 1
        last = state.get(item["id"], {}).get("last")
        new_alerts = compare(item, found, last)
        alerts += new_alerts
        entry = {**found, "missing": False, "checked_at": now} if found else {**(last or {}), "missing": True, "checked_at": now}
        record = state.setdefault(item["id"], {"history": []})
        record["last"] = entry
        if found:
            record["history"] = (record["history"] + [{"price": found["price"], "at": now}])[-HISTORY:]
        summary["not_found"] += not found
        price = money(found["currency"] or item["currency"], found["price"]) if found else "not found"
        print(f"{'ALERT' if new_alerts else 'ok':<6} {item['id']}: {price} ({found['source'] if found else '-'})")
    for text in alerts:
        if dry_run:
            print(f"\n--- alert ---\n{text}")
        else:
            send_telegram(text)
    summary["alerts"] = len(alerts)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("watchlist", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="read samples/pages/<id>.html, print alerts, no API calls")
    ap.add_argument("--state", type=Path, help="price history file (default state/prices.json, out/prices.json in dry run)")
    args = ap.parse_args(argv)
    state = args.state or Path("out/prices.json" if args.dry_run else "state/prices.json")
    seed = args.watchlist.parent / "state_before.json"
    if args.dry_run and not state.exists() and seed.exists():
        state.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(seed, state)  # sample history, so the first dry run has something to compare against
    summary = run(args.watchlist, state, args.dry_run, pages=args.watchlist.parent / "pages")
    print(json.dumps(summary))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
