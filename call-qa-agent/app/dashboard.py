"""Server-rendered dashboard: call list and call detail with highlighted evidence."""

import math
import re
from collections import Counter
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import page_viewer
from app.config import Settings, get_settings
from app.db import Supabase
from app.deps import get_db
from app.pipeline import public_view
from app.rubrics import RubricError, load_rubric
from app.rubrics import available as available_rubrics
from app.scoring import NEEDS_REVIEW, normalize
from app.ui import templates

router = APIRouter()
LANGUAGES = {"en": "English", "ar": "Arabic", "multi": "Arabic + English", "ar-en": "Arabic + English"}
RING = 2 * math.pi * 26  # circumference of the score ring (r=26 in call.html)
PAGE_SIZE = 25
LIST_LIMIT = 500  # calls read for the overview and filters; the list pages through them
RESULTS = {"pass": "Pass", "fail": "Fail", "undecided": "Undecided", "unscored": "Not scored"}
IN_PROGRESS = {"queued", "transcribing", "scoring"}


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = float(seconds)
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def outcome(score: dict | None) -> dict:
    """Pass, fail, undecided (critical item needs review) or not scored yet."""
    if not score:
        return {"label": "Not scored", "css": "muted"}
    if score["passed"] is None:
        return {"label": "Undecided", "css": "review"}
    return {"label": "Pass", "css": "pass"} if score["passed"] else {"label": "Fail", "css": "fail"}


def call_title(call: dict) -> str:
    """The stored title, or one made from the file name."""
    if call.get("title"):
        return call["title"]
    stem = re.sub(r"\.[a-z0-9]{2,4}$", "", call.get("filename") or "", flags=re.I)
    stem = re.sub(r"^demo_", "", stem).replace("_", " ").replace("-", " ").strip()
    return (stem[:1].upper() + stem[1:]) if stem else "Untitled call"


def language_label(code: str | None) -> str:
    return LANGUAGES.get((code or "").lower(), (code or "").upper())


def speaker_roles(items: list[dict], utterances: list[dict]) -> dict:
    """Agent and Customer from the evidence: the rubric judges the agent, so the speaker whose lines are
    quoted as evidence most often is the agent. With exactly two speakers the other is the customer;
    otherwise the rest keep their numbers. No evidence, no labels."""
    quoted = Counter(utterances[i["utterance_index"]]["speaker"] for i in items
                     if i.get("verdict") in ("pass", "fail") and isinstance(i.get("utterance_index"), int)
                     and 0 <= i["utterance_index"] < len(utterances))
    speakers = sorted({u["speaker"] for u in utterances})
    if not quoted:
        return {s: f"Speaker {s}" for s in speakers}
    agent = quoted.most_common(1)[0][0]
    roles = {s: f"Speaker {s}" for s in speakers}
    roles[agent] = "Agent"
    if len(speakers) == 2:
        roles[next(s for s in speakers if s != agent)] = "Customer"
    return roles


def pass_mark(rubric: dict) -> int | None:
    try:
        return load_rubric(rubric["slug"])["pass_threshold"] if rubric.get("slug") else None
    except (RubricError, OSError, KeyError):
        return None


def verdict_reason(score: dict | None, items: list[dict], threshold: int | None) -> str:
    """One sentence on why the call passed, failed or is undecided."""
    if not score:
        return ""
    if score["passed"] is None:
        return "A critical item needs review before this call can pass."
    points = f"{float(score['overall_score']):.0f}"
    critical = [i["title"] for i in items if i["critical"] and i["verdict"] == "fail"]
    if not score["passed"]:
        if critical:
            return f"Critical item failed: {', '.join(critical)}." if len(critical) == 1 \
                else f"Critical items failed: {', '.join(critical)}."
        return f"Score {points} is below the pass mark of {threshold}." if threshold else f"Score {points} is below the pass mark."
    mark = f" of {threshold}" if threshold else ""
    return f"Score {points} meets the pass mark{mark}, and no critical item failed."


def fail_line(score: dict | None, rubric: dict, threshold: int | None) -> str:
    """Short reason under a failed call in the list: the failed critical item, else the score against the pass mark."""
    if not score or score.get("passed") is not False:
        return ""
    meta = {i["id"]: i for i in rubric.get("items") or []}
    critical = [meta[i["item_id"]].get("title", i["item_id"]) for i in score.get("items") or []
                if i.get("verdict") == "fail" and meta.get(i.get("item_id"), {}).get("critical")]
    if critical:
        more = f" and {len(critical) - 1} more" if len(critical) > 1 else ""
        return f"Critical item failed: {critical[0]}{more}"
    points = f"{float(score['overall_score']):.0f}"
    return f"Below pass mark ({points} of {threshold})" if threshold else f"Below pass mark ({points})"


def results_summary(items: list[dict]) -> str:
    """Fallback summary for calls without one: what the scoring found."""
    applicable = [i for i in items if i["verdict"] != "na"]
    passed = sum(i["verdict"] == "pass" for i in applicable)
    failed = [i["title"] for i in applicable if i["verdict"] == "fail"]
    text = f"{passed} of {len(applicable)} checks passed."
    return text + (f" Failed: {', '.join(failed)}." if failed else "")


def run_chips(score: dict | None) -> list[str]:
    """Model, tokens, latency and cost of the scoring run, as short labels."""
    if not score:
        return []
    chips = [score.get("model") or "model unknown"]
    if score.get("input_tokens") is not None:
        chips.append(f"{score['input_tokens']:,} in · {score.get('output_tokens') or 0:,} out tokens")
    if score.get("latency_ms") is not None:
        chips.append(f"{score['latency_ms'] / 1000:.1f} s")
    if score.get("cost_usd") is not None:
        chips.append(f"${float(score['cost_usd']):.4f}")
    return chips


ORDER = {"fail": 0, NEEDS_REVIEW: 1}


def group_items(items: list[dict]) -> dict:
    """Failed and needs-review items first (critical ones at the top), then passed, then not applicable."""
    attention = sorted((i for i in items if i["verdict"] in ORDER), key=lambda i: (ORDER[i["verdict"]], not i["critical"]))
    return {"attention": attention, "passed": [i for i in items if i["verdict"] == "pass"],
            "na": [i for i in items if i["verdict"] == "na"]}


def review_count(score: dict | None) -> int:
    return sum(i.get("verdict") == NEEDS_REVIEW for i in (score or {}).get("items", []))


def quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """Where a quote sits in the original text, ignoring case and punctuation (as the evidence check does)."""
    words = normalize(quote).split()
    if not words:
        return None
    pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(w) for w in words) + r"(?!\w)"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.span() if match else None


def highlight(text: str, quotes: list[tuple[str, str]]) -> list[dict]:
    """Split text into segments; segments covered by evidence quotes carry the item ids that cite them."""
    spans = sorted((span, item_id) for item_id, quote in quotes if (span := quote_span(text, quote)))
    merged: list[list] = []
    for (start, end), item_id in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            merged[-1][2].append(item_id)
        else:
            merged.append([start, end, [item_id]])
    segments, pos = [], 0
    for start, end, ids in merged:
        if start > pos:
            segments.append({"text": text[pos:start], "items": []})
        segments.append({"text": text[start:end], "items": ids})
        pos = end
    if pos < len(text):
        segments.append({"text": text[pos:], "items": []})
    return segments


def scoring_rules(rubric: dict, items: list[dict], threshold: int | None) -> dict | None:
    """The rubric as the panel shows it, and this call's arithmetic. Mirrors scoring.overall(), read-only."""
    if not rubric.get("items"):
        return None
    verdicts = {i["item_id"]: i["verdict"] for i in items}
    checks = [{"title": i.get("title", i["id"]), "weight": i.get("weight", 0), "critical": bool(i.get("critical")),
               "pass_if": i.get("pass_if", ""), "verdict": verdicts.get(i["id"])} for i in rubric["items"]]
    applicable = sum(c["weight"] for c in checks if c["verdict"] not in (None, "na"))
    got = sum(c["weight"] for c in checks if c["verdict"] == "pass")
    return {"checks": checks, "total": sum(c["weight"] for c in checks), "threshold": threshold,
            "applicable": applicable, "got": got, "scored": bool(verdicts)}


def detail_view(call: dict) -> dict:
    utterances = (call.get("transcripts") or {}).get("utterances") or []
    scores = call.get("scores") or []
    score = scores[0] if scores else None
    rubric = (score or {}).get("rubrics") or {}
    rubric_items = {i["id"]: i for i in rubric.get("items") or []}

    items = []
    for raw in (score or {}).get("items", []):
        meta = rubric_items.get(raw["item_id"], {})
        index = raw.get("utterance_index")
        # the line's own start, so the time shown matches the transcript (and the audio, once retimed)
        start = utterances[index]["start"] if isinstance(index, int) and 0 <= index < len(utterances) else raw.get("start_time")
        items.append({**raw, "title": meta.get("title", raw["item_id"]), "critical": meta.get("critical", False),
                      "weight": meta.get("weight"), "anchor": f"u-{index}" if index is not None else None,
                      "time": mmss(start)})

    cited: dict[int, list] = {}
    for item in items:
        if item["anchor"] and item["verdict"] != NEEDS_REVIEW:
            cited.setdefault(item["utterance_index"], []).append((item["item_id"], item["evidence_quote"]))
    verdict_of = {i["item_id"]: i["verdict"] for i in items}
    roles = speaker_roles(items, utterances)
    lines = []
    for index, u in enumerate(utterances):
        segments = highlight(u["text"], cited.get(index, []))
        for seg in segments:
            seg["css"] = "fail" if any(verdict_of[i] == "fail" for i in seg["items"]) else "pass" if seg["items"] else ""
        lines.append({"index": index, "time": mmss(u["start"]), "start": round(float(u["start"]), 2),
                      "speaker": roles.get(u["speaker"], f"Speaker {u['speaker']}"),
                      "role": roles.get(u["speaker"], "").lower() if roles.get(u["speaker"]) in ("Agent", "Customer") else "",
                      "segments": segments, "cited": bool(cited.get(index))})

    threshold = pass_mark(rubric)
    points = float(score["overall_score"]) if score and score.get("overall_score") is not None else None
    return {"call": call, "score": score, "rubric": rubric, "outcome": outcome(score), "items": items,
            "groups": group_items(items), "lines": lines, "review": review_count(score),
            "in_progress": call["status"] in IN_PROGRESS, "undecided": bool(score) and score["passed"] is None,
            "duration": mmss(call.get("duration_sec")), "title": call_title(call),
            "summary": call.get("summary") or (results_summary(items) if items else ""),
            "reason": verdict_reason(score, items, threshold), "threshold": threshold, "chips": run_chips(score),
            "rules": scoring_rules(rubric, items, threshold),
            "language": language_label(call.get("language")),
            "audio_url": f"/calls/{call['id']}/audio" if call.get("storage_path") else None,
            "ring": {"length": round(RING, 2), "offset": round(RING * (1 - (points or 0) / 100), 2)} if points is not None else None}


def result_key(score: dict | None) -> str:
    if not score:
        return "unscored"
    return {True: "pass", False: "fail", None: "undecided"}[score["passed"]]


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def top_failing(scores: list[dict], limit: int = 5) -> list[dict]:
    """Rubric checks that fail most often: failures out of the scored calls the check applied to."""
    fails, applied, meta = Counter(), Counter(), {}
    for score in scores:
        rubric = score.get("rubrics") or {}
        items = {i["id"]: i for i in rubric.get("items") or []}
        for r in score.get("items") or []:
            if r.get("verdict") == "na":
                continue
            key = (rubric.get("slug"), r["item_id"])
            applied[key] += 1
            fails[key] += r.get("verdict") == "fail"
            meta[key] = {"title": items.get(r["item_id"], {}).get("title", r["item_id"]), "rubric": rubric.get("name", ""),
                         "critical": items.get(r["item_id"], {}).get("critical", False)}
    rows = [{**meta[k], "fails": fails[k], "applied": applied[k], "rate": fails[k] / applied[k]} for k in fails if fails[k]]
    rows.sort(key=lambda r: (-r["rate"], -r["fails"], r["title"]))
    # width: a CSS class in 5% steps (the page's CSP allows no inline styles); at least 5% so a bar always shows
    return [{**r, "percent": round(100 * r["rate"]), "width": f"w-{max(5, 5 * round(20 * r['rate']))}"} for r in rows[:limit]]


def overview(calls: list[dict]) -> dict:
    """Headline numbers for the calls in view, from each call's latest score."""
    scores = [s for s in ((c.get("scores") or [None])[0] for c in calls) if s]
    decided = [s for s in scores if s.get("passed") is not None]
    critical = 0
    for s in scores:
        crit = {i["id"] for i in ((s.get("rubrics") or {}).get("items") or []) if i.get("critical")}
        critical += any(r.get("verdict") == "fail" and r.get("item_id") in crit for r in s.get("items") or [])
    costs = [float(s["cost_usd"]) for s in scores if s.get("cost_usd") is not None]
    latencies = [float(s["latency_ms"]) for s in scores if s.get("latency_ms") is not None]
    points = [float(s["overall_score"]) for s in scores if s.get("overall_score") is not None]
    passed = sum(s["passed"] is True for s in decided)
    return {
        "calls": len(calls), "scored": len(scores), "decided": len(decided), "passed": passed,
        "pass_rate": round(100 * passed / len(decided)) if decided else None,
        "avg_score": round(average(points)) if points else None,
        "critical_calls": critical,
        "avg_cost": average(costs), "costed": len(costs),
        "avg_latency_s": round(average(latencies) / 1000, 1) if latencies else None,
        "top_failing": top_failing(scores),
    }


def list_filters(params, calls: list[dict]) -> dict:
    """Result, rubric and language filters from the query string, with the options the data offers.
    Unknown values are ignored, so a stale shared link still opens."""
    rubrics, languages = {}, {}
    for c in calls:
        score = (c.get("scores") or [None])[0]
        rubric = (score or {}).get("rubrics") or {}
        if rubric.get("slug"):
            rubrics[rubric["slug"]] = rubric.get("name") or rubric["slug"]
        if c.get("language"):
            languages[c["language"]] = language_label(c["language"])
    active = {"result": params.get("result", ""), "rubric": params.get("rubric", ""), "language": params.get("language", "")}
    if active["result"] not in RESULTS:
        active["result"] = ""
    if active["rubric"] not in rubrics:
        active["rubric"] = ""
    if active["language"] not in languages:
        active["language"] = ""
    return {"active": active, "on": any(active.values()), "results": RESULTS,
            "rubrics": dict(sorted(rubrics.items(), key=lambda kv: kv[1])),
            "languages": dict(sorted(languages.items(), key=lambda kv: kv[1]))}


def matches(call: dict, active: dict) -> bool:
    score = (call.get("scores") or [None])[0]
    if active["result"] and result_key(score) != active["result"]:
        return False
    if active["rubric"] and ((score or {}).get("rubrics") or {}).get("slug") != active["rubric"]:
        return False
    return not active["language"] or call.get("language") == active["language"]


def page_link(active: dict, page: int) -> str:
    query = {k: v for k, v in active.items() if v}
    if page > 1:
        query["page"] = page
    return "/dashboard" + ("?" + urlencode(query) if query else "")


@router.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def call_list(request: Request, page: int = 1, db: Supabase = Depends(get_db), viewer: str = Depends(page_viewer)):
    page = max(page, 1)
    everything = [public_view(c, viewer) for c in db.list_calls(limit=LIST_LIMIT, offset=0, demo_only=viewer == "demo")]
    filters = list_filters(request.query_params, everything)
    calls = [c for c in everything if matches(c, filters["active"])]
    shown = calls[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    rows, marks = [], {}
    for call in shown:
        score = (call.get("scores") or [None])[0]
        rubric = (score or {}).get("rubrics") or {}
        if score and score.get("passed") is False and rubric.get("slug") not in marks:
            marks[rubric.get("slug")] = pass_mark(rubric)
        rows.append({"call": call, "score": score, "outcome": outcome(score), "review": review_count(score),
                     "rubric": rubric, "duration": mmss(call.get("duration_sec")),
                     "fail_reason": fail_line(score, rubric, marks.get(rubric.get("slug"))),
                     "title": call_title(call), "language": language_label(call.get("language"))})
    return templates.TemplateResponse(request, "calls.html", {
        "rows": rows, "page": page, "has_next": len(calls) > page * PAGE_SIZE, "viewer": viewer,
        "total": len(everything), "capped": len(everything) >= LIST_LIMIT, "filters": filters,
        "overview": overview(calls) if calls else None,
        "newer": page_link(filters["active"], page - 1), "older": page_link(filters["active"], page + 1),
        "clear": page_link({}, 1),
        "refresh": any(r["call"]["status"] in IN_PROGRESS for r in rows)})


@router.get("/dashboard/calls/{call_id}", response_class=HTMLResponse, include_in_schema=False)
def call_detail(request: Request, call_id: str, db: Supabase = Depends(get_db), viewer: str = Depends(page_viewer)):
    call = db.get_call(call_id)
    if call is None or (viewer == "demo" and not call.get("is_demo")):
        raise HTTPException(404, "call not found")
    return templates.TemplateResponse(request, "call.html", {**detail_view(public_view(call, viewer)), "viewer": viewer})


@router.get("/upload", response_class=HTMLResponse, include_in_schema=False)
def upload_page(request: Request, viewer: str = Depends(page_viewer), settings: Settings = Depends(get_settings)):
    """Owner only. The browser sends the file straight to Supabase Storage (static/upload.js), then
    asks the server to transcribe and score it from there."""
    if viewer != "owner":
        return RedirectResponse("/login?next=/upload", status_code=303)
    return templates.TemplateResponse(request, "upload.html", {
        "viewer": viewer, "rubrics": available_rubrics(), "max_mb": settings.storage_max_mb,
        "inline": settings.processing_mode == "inline"})
