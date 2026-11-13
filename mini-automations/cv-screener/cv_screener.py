"""First-pass CV screening against a job's requirements, with quoted evidence.

    python cv_screener.py samples/job.yaml samples/cvs --dry-run    # keyword rules, no API calls
    python cv_screener.py job.yaml cvs/                             # Claude
Writes out/ranking.csv and out/shortlist.md. A person makes every decision; this only sorts.
LLM_PROVIDER=gemini switches screening from Claude to Gemini.
"""

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

import yaml

# The Gemini request and the rate-limit error are shared with the other Python automations.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
from gemini import RateLimited, gemini_json  # noqa: E402

VERDICT_POINTS = {"met": 1.0, "partial": 0.5, "missing": 0.0, "needs_review": 0.0}
# Lines that must not influence screening, and contact details nobody needs for it.
SENSITIVE_LINE = re.compile(r"^\s*(date of birth|dob|age|nationality|gender|marital status|religion|"
                            r"تاريخ الميلاد|الجنسية|الجنس|الحالة الاجتماعية)\b.*$", re.I | re.M)
CONTACT = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
           (re.compile(r"\+\d[\d\s()-]{7,}\d|\b0\d{1,2}[\s-]?\d{3}[\s-]?\d{4}\b"), "[phone]"),
           (re.compile(r"(https?://|www\.)\S+|linkedin\.com/\S+", re.I), "[link]")]

# What can go wrong with one CV: it cannot be read or parsed, the model call fails, or the answer has the wrong shape.
CV_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)


def read_cv(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        except PyPdfError as e:
            raise ValueError(f"unreadable PDF: {e}") from e
    return path.read_text(encoding="utf-8")


def redact(text: str) -> str:
    text = SENSITIVE_LINE.sub("[removed]", text)
    for pattern, label in CONTACT:
        text = pattern.sub(label, text)
    return text


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def years_of_experience(text: str) -> int:
    found = [int(n) for n in re.findall(r"(\d{1,2})\+?\s*(?:years|yrs|سنوات|سنة)", text, re.I)]
    return max(found, default=0)


def assess_with_rules(job: dict, text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    years = years_of_experience(text)
    results = []
    for req in job["requirements"]:
        if "min_years" in req:
            need = req["min_years"]
            verdict = "met" if years >= need else "partial" if years >= need - 1 else "missing"
            quote = next((line for line in lines if re.search(rf"\b{years}\+?\s*(years|yrs)", line, re.I)), "") if years else ""
        else:
            quote = next((line for line in lines if any(k.lower() in line.lower() for k in req["keywords"])), "")
            verdict = "met" if quote else "missing"
        results.append({"id": req["id"], "verdict": verdict, "evidence_quote": quote, "note": "keyword rules"})
    return {"years_experience": years, "requirements": results, "summary": ""}


def schema_for(job: dict) -> dict:
    ids = [r["id"] for r in job["requirements"]]
    item = {"type": "object", "additionalProperties": False, "required": ["id", "verdict", "evidence_quote", "note"],
            "properties": {"id": {"type": "string", "enum": ids},
                           "verdict": {"type": "string", "enum": ["met", "partial", "missing"]},
                           "evidence_quote": {"type": "string", "description": "Exact words copied from the CV, empty if missing"},
                           "note": {"type": "string", "description": "One short reason"}}}
    return {"type": "object", "additionalProperties": False, "required": ["years_experience", "requirements", "summary"],
            "properties": {"years_experience": {"type": "integer"},
                           "requirements": {"type": "array", "items": item},
                           "summary": {"type": "string", "description": "Two sentences on fit for this role"}}}


SYSTEM = ("You screen CVs against job requirements. Judge only skills, experience and qualifications stated in the CV. "
          "Ignore names, age, gender, nationality, religion, photos and any other personal characteristic. "
          "For each requirement give a verdict and copy the exact supporting words from the CV; never paraphrase a quote.")


def assess_with_llm(job: dict, text: str, client=None) -> dict:
    """Assessment with the provider chosen by LLM_PROVIDER (anthropic or gemini)."""
    provider = os.environ.get("LLM_PROVIDER") or "anthropic"
    reqs = "\n".join(f"- {r['id']} ({r['type']}): {r['text']}" for r in job["requirements"])
    prompt = f"Job: {job['title']}\nRequirements:\n{reqs}\n\nCV:\n{text}"
    if provider == "gemini":
        # Flash: the Sonnet-class option, for the same fairness and accuracy reasons.
        return gemini_json(os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash", SYSTEM, [{"text": prompt}], schema_for(job))
    if provider != "anthropic":
        raise ValueError(f"LLM_PROVIDER must be anthropic or gemini, not {provider!r}")
    import anthropic

    try:
        return assess_with_claude(job, prompt, client or anthropic.Anthropic())
    except anthropic.RateLimitError as e:  # the SDK has already retried with backoff
        raise RateLimited("Claude rate limit (429) after retries") from e
    except anthropic.APIError as e:
        raise RuntimeError(f"Claude request failed: {e}") from e


def assess_with_claude(job: dict, prompt: str, client) -> dict:
    # Sonnet: judging partial matches fairly matters more than cost at CV volumes.
    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-5-5"
    extra = {} if model.startswith("claude-haiku") else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    response = client.beta.messages.create(
        model=model, max_tokens=4096, **extra,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema_for(job)}},
        system=SYSTEM, messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined the request")
    return json.loads(next(b.text for b in reversed(response.content) if b.type == "text"))


def verify_quotes(result: dict, text: str) -> dict:
    """Hallucination guard: a quote that is not in the CV turns the verdict into needs_review."""
    cv = norm(text)
    for r in result["requirements"]:
        if r["verdict"] != "missing" and (not r["evidence_quote"] or norm(r["evidence_quote"]) not in cv):
            r["note"] = f"quote not found in CV, was {r['verdict']}"
            r["verdict"] = "needs_review"
    return result


def score(job: dict, result: dict) -> tuple[float, str]:
    by_id = {r["id"]: r["verdict"] for r in result["requirements"]}
    weight = {"must": 1.0, "nice": 0.5}
    total = sum(weight[r["type"]] for r in job["requirements"])
    got = sum(weight[r["type"]] * VERDICT_POINTS[by_id.get(r["id"], "missing")] for r in job["requirements"])
    pct = round(100 * got / total, 1)
    musts = [by_id.get(r["id"], "missing") for r in job["requirements"] if r["type"] == "must"]
    if "needs_review" in by_id.values():
        decision = "review"
    elif "missing" not in musts and pct >= job.get("shortlist_score", 70):
        decision = "shortlist"
    elif "missing" not in musts:
        decision = "review"
    else:
        decision = "not shortlisted"
    return pct, decision


def write_outputs(job: dict, rows: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: ({"shortlist": 0, "review": 1, "not shortlisted": 2}[r["decision"]], -r["score"]))
    ids = [r["id"] for r in job["requirements"]]
    with (out / "ranking.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["candidate", "score", "decision", "years_experience", *ids])
        for r in rows:
            verdicts = {x["id"]: x["verdict"] for x in r["result"]["requirements"]}
            w.writerow([r["candidate"], r["score"], r["decision"], r["result"]["years_experience"], *[verdicts.get(i, "") for i in ids]])
    lines = [f"# {job['title']}: screening results", "", "Sorted, not decided: a person reviews every candidate.", ""]
    for r in rows:
        lines += [f"## {r['candidate']}: {r['decision']} ({r['score']})", ""]
        if r["result"]["summary"]:
            lines += [r["result"]["summary"], ""]
        for x in r["result"]["requirements"]:
            quote = f' "{x["evidence_quote"]}"' if x["evidence_quote"] else ""
            lines.append(f"- **{x['id']}**: {x['verdict']}{quote}")
        lines.append("")
    (out / "shortlist.md").write_text("\n".join(lines), encoding="utf-8")


def run(job_path: Path, folder: Path, out: Path, dry_run: bool, client=None) -> dict:
    job = yaml.safe_load(job_path.read_text(encoding="utf-8"))
    rows, summary = [], {"screened": 0, "shortlist": 0, "review": 0, "not shortlisted": 0, "failed": 0}
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in {".txt", ".md", ".pdf"}):
        fallback = False
        try:
            text = redact(read_cv(path))
            try:
                result = assess_with_rules(job, text) if dry_run else assess_with_llm(job, text, client)
            except RateLimited as e:  # keyword rules are not good enough to decide, so a person reviews this one
                print(f"warn   {path.name}: {e}, used keyword rules")
                result, fallback = assess_with_rules(job, text), True
        except CV_ERRORS as e:  # one unreadable CV must not stop the batch
            summary["failed"] += 1
            print(f"error  {path.name}: {e}", file=sys.stderr)
            continue
        result = verify_quotes(result, text)
        pct, decision = score(job, result)
        if fallback:
            decision, result["summary"] = "review", "Rate limited: scored with keyword rules, needs a manual check."
        rows.append({"candidate": path.stem, "score": pct, "decision": decision, "result": result})
        summary["screened"] += 1
        summary[decision] += 1
        print(f"{decision:<16} {pct:>5}  {path.name}")
    write_outputs(job, rows, out)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", type=Path)
    ap.add_argument("cvs", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="keyword rules instead of Claude, no API calls")
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args(argv)
    summary = run(args.job, args.cvs, args.out, args.dry_run)
    print(json.dumps(summary))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
