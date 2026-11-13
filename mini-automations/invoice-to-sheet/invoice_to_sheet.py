"""Extract supplier invoices (PDF or text) into a Google Sheet.

    python invoice_to_sheet.py samples/invoices --dry-run           # rules only, writes out/invoices.csv
    python invoice_to_sheet.py inbox/                                # Claude + Google Sheet
    python invoice_to_sheet.py inbox/ --csv out/invoices.csv         # Claude + CSV
LLM_PROVIDER=gemini switches extraction from Claude to Gemini.
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# The Gemini request and the rate-limit error are shared with the other Python automations.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from gemini import RateLimited, gemini_json  # noqa: E402

COLUMNS = ["file", "supplier", "supplier_trn", "invoice_number", "invoice_date", "due_date",
           "currency", "subtotal", "vat", "total", "status", "issues", "source"]
REQUIRED = ["supplier", "invoice_number", "invoice_date", "total"]

# Label keywords per field, checked in order (e.g. "due date" before "date", "subtotal" before "total").
LABELS = [
    ("due_date", ["due date", "تاريخ الاستحقاق"]), ("invoice_date", ["invoice date", "تاريخ الفاتورة"]),
    ("invoice_number", ["invoice no", "invoice number", "invoice #", "رقم الفاتورة"]),
    ("supplier_trn", ["trn", "الرقم الضريبي"]), ("subtotal", ["subtotal", "sub-total", "المجموع الفرعي"]),
    ("total", ["total", "الإجمالي"]), ("vat", ["vat", "ضريبة القيمة المضافة"]),
    ("supplier", ["supplier", "from", "المورد"]), ("invoice_date", ["date", "التاريخ"]),
]
AMOUNTS = {"subtotal", "vat", "total"}

STRING, NUMBER = {"type": "string"}, {"type": "number"}
SCHEMA = {"type": "object", "additionalProperties": False, "required": [
    "supplier", "supplier_trn", "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "vat", "total"],
    "properties": {
        "supplier": STRING, "invoice_number": STRING,
        "supplier_trn": {**STRING, "description": "Tax registration number, empty if absent"},
        "invoice_date": {**STRING, "description": "YYYY-MM-DD"},
        "due_date": {**STRING, "description": "YYYY-MM-DD, empty if absent"},
        "currency": {**STRING, "description": "ISO code such as AED"},
        "subtotal": {**NUMBER, "description": "Before VAT, 0 if absent"},
        "vat": {**NUMBER, "description": "VAT amount, 0 if absent"},
        "total": {**NUMBER, "description": "Amount payable including VAT"}}}

# What can go wrong with one invoice: it cannot be read or parsed, the model call fails, or the answer has the wrong shape.
INVOICE_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)

def parse_amount(value: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    return float(m.group().replace(",", "")) if m else None


def parse_date(value: str) -> str:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return value.strip()


def extract_with_rules(text: str) -> dict:
    """Read "Label: value" lines. Handles English, Arabic and bilingual labels."""
    data: dict = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, value = (part.strip() for part in line.split(":", 1))
        label = label.lower()
        for field, keys in LABELS:
            if field not in data and value and any(k in label for k in keys):
                if field in AMOUNTS:
                    data[field] = parse_amount(value)
                    if "currency" not in data and (cur := re.search(r"\b(AED|SAR|USD|EUR)\b", value)):
                        data["currency"] = cur.group(1)
                elif field.endswith("date"):
                    data[field] = parse_date(value)
                else:
                    data[field] = value
                break
    if "supplier" not in data:
        data["supplier"] = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if "currency" not in data and "درهم" in text:
        data["currency"] = "AED"
    return data


INSTRUCTION = "Extract the invoice header fields. Use empty strings or 0 for anything not on the invoice."


def extract_with_gemini(path: Path) -> dict:
    # Flash: the Sonnet-class option, for the same accuracy reasons.
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
    if path.suffix.lower() == ".pdf":
        parts = [{"inlineData": {"mimeType": "application/pdf", "data": base64.standard_b64encode(path.read_bytes()).decode()}}]
    else:
        parts = [{"text": path.read_text(encoding="utf-8")}]
    data = gemini_json(model, "You extract header fields from supplier invoices.", parts + [{"text": INSTRUCTION}], SCHEMA)
    return {k: v for k, v in data.items() if v not in ("", None)}


def extract_with_llm(path: Path, client=None) -> dict:
    """Live extraction with the provider chosen by LLM_PROVIDER (anthropic or gemini)."""
    provider = os.environ.get("LLM_PROVIDER") or "anthropic"
    if provider == "gemini":
        return extract_with_gemini(path)
    if provider != "anthropic":
        raise ValueError(f"LLM_PROVIDER must be anthropic or gemini, not {provider!r}")
    import anthropic

    try:
        return extract_with_claude(path, client or anthropic.Anthropic())
    except anthropic.RateLimitError as e:  # the SDK has already retried with backoff
        raise RateLimited("Claude rate limit (429) after retries") from e
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude request failed: {e}") from e


def extract_with_claude(path: Path, client) -> dict:
    if path.suffix.lower() == ".pdf":
        source = {"type": "base64", "media_type": "application/pdf",
                  "data": base64.standard_b64encode(path.read_bytes()).decode()}
        content = [{"type": "document", "source": source}]
    else:
        content = [{"type": "text", "text": path.read_text(encoding="utf-8")}]
    content.append({"type": "text", "text": INSTRUCTION})

    # Sonnet by default: invoice layouts vary and a misread total costs more than the call.
    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5-5"
    # Haiku 5.5 has no server-side refusal fallback; send it only to models that support it.
    fallback = {} if model.startswith("claude-haiku") else {
        "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    response = client.beta.messages.create(
        model=model,
        max_tokens=4096,
        **fallback,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{path.name}: model declined the request")
    text = next(b.text for b in reversed(response.content) if b.type == "text")
    data = json.loads(text)
    return {k: v for k, v in data.items() if v not in ("", None)}


def validate(inv: dict) -> list[str]:
    issues = [f"missing {f}" for f in REQUIRED if not inv.get(f)]
    sub, vat, total = inv.get("subtotal"), inv.get("vat"), inv.get("total")
    if sub and total and vat is not None and abs(sub + vat - total) > 0.01:
        issues.append("subtotal + VAT does not equal total")
    if inv.get("currency") == "AED" and sub and vat is not None and abs(vat - round(sub * 0.05, 2)) > 0.01:
        issues.append("VAT is not 5% of subtotal")
    return issues


def dedupe_key(inv: dict) -> str:
    who = inv.get("supplier_trn") or inv.get("supplier", "")
    return f"{who}|{inv.get('invoice_number', '')}".lower().strip()


def to_row(inv: dict) -> list:
    return [inv.get(c, "") if inv.get(c) is not None else "" for c in COLUMNS]


class CsvSink:
    def __init__(self, path: Path):
        self.path = path

    def existing(self) -> list[dict]:
        return list(csv.DictReader(self.path.open(encoding="utf-8", newline=""))) if self.path.exists() else []

    def append(self, rows: list[list]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        with self.path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(([COLUMNS] if new else []) + rows)


class SheetSink:
    def __init__(self, worksheet):
        self.ws = worksheet

    @classmethod
    def from_env(cls):
        import gspread

        gc = gspread.service_account(filename=os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
        ws = gc.open_by_key(os.environ["GOOGLE_SHEET_ID"]).worksheet(os.environ.get("GOOGLE_SHEET_TAB", "Invoices"))
        if not ws.row_values(1):
            ws.append_row(COLUMNS)
        return cls(ws)

    def existing(self) -> list[dict]:
        values = self.ws.get_all_values()
        return [dict(zip(values[0], r, strict=False)) for r in values[1:]] if values else []  # rows can be shorter than the header

    def append(self, rows: list[list]) -> None:
        self.ws.append_rows(rows, value_input_option="USER_ENTERED")


def run(folder: Path, sink, dry_run: bool, client=None) -> dict:
    seen = {dedupe_key(r) for r in sink.existing()}
    rows, summary = [], {"added": 0, "duplicates": 0, "needs_check": 0, "failed": 0}
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in {".pdf", ".txt"}):
        if dry_run and path.suffix.lower() == ".pdf":
            print(f"skip   {path.name}: PDFs need Claude (live mode)")
            continue
        source = "rules" if dry_run else os.environ.get("LLM_PROVIDER") or "anthropic"
        try:
            inv = extract_with_rules(path.read_text(encoding="utf-8")) if dry_run else extract_with_llm(path, client)
        except RateLimited as e:
            if path.suffix.lower() == ".pdf":  # no rules for PDFs: report it and retry on the next run
                summary["failed"] += 1
                print(f"error  {path.name}: {e}", file=sys.stderr)
                continue
            print(f"warn   {path.name}: {e}, used keyword rules")
            inv, source = extract_with_rules(path.read_text(encoding="utf-8")), "rules (rate limited)"
        except INVOICE_ERRORS as e:  # one bad file must not stop the batch
            summary["failed"] += 1
            print(f"error  {path.name}: {e}", file=sys.stderr)
            continue
        key = dedupe_key(inv)
        if key in seen:
            summary["duplicates"] += 1
            print(f"dup    {path.name}: {inv.get('invoice_number')} already recorded")
            continue
        seen.add(key)
        issues = validate(inv)
        inv.update(file=path.name, status="check" if issues else "ok", issues="; ".join(issues),
                   source=source.replace("anthropic", "claude"))
        summary["needs_check"] += bool(issues)
        summary["added"] += 1
        rows.append(to_row(inv))
        print(f"{inv['status']:<6} {path.name}: {inv.get('supplier')} {inv.get('invoice_number')} {inv.get('total')}")
    if rows:
        sink.append(rows)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="keyword rules only, no API calls, writes CSV")
    ap.add_argument("--csv", type=Path, help="write to this CSV instead of Google Sheets")
    args = ap.parse_args(argv)
    csv_path = args.csv or (Path("out/invoices.csv") if args.dry_run else None)
    sink = CsvSink(csv_path) if csv_path else SheetSink.from_env()
    summary = run(args.folder, sink, args.dry_run)
    print(json.dumps(summary))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
