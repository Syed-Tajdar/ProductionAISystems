"""Pure functions that compare one scored call with samples/expected.json."""

from app.scoring import NEEDS_REVIEW, normalize


def item_results(expected: dict, scored_items: list[dict], rubric: dict) -> list[dict]:
    got = {i["item_id"]: i for i in scored_items}
    critical = {i["id"] for i in rubric["items"] if i.get("critical")}
    rows = []
    for exp in expected["items"]:
        verdict = got.get(exp["item_id"], {}).get("verdict", "missing")
        rows.append({"item": f"{expected['rubric']}.{exp['item_id']}", "expected": exp["verdict"], "got": verdict,
                     "correct": verdict == exp["verdict"], "critical": exp["item_id"] in critical,
                     "needs_review": verdict == NEEDS_REVIEW})
    return rows


def diarization_accuracy(utterances: list[dict], truth: list[dict]) -> tuple[float | None, int]:
    """Share of transcribed speech time attributed to the right role, after mapping each
    Deepgram speaker to the role it overlaps most. Returns (accuracy, number of speakers)."""
    speakers: dict[int, dict[str, float]] = {}
    spans = []
    for u in utterances:
        overlaps = {}
        for line in truth:
            overlap = min(u["end"], line["end"]) - max(u["start"], line["start"])
            if overlap > 0:
                overlaps[line["role"]] = overlaps.get(line["role"], 0) + overlap
        for role, seconds in overlaps.items():
            speakers.setdefault(u["speaker"], {}).setdefault(role, 0)
            speakers[u["speaker"]][role] += seconds
        spans.append((u["speaker"], overlaps))
    if not speakers:
        return None, 0
    role_of = {spk: max(roles, key=roles.get) for spk, roles in speakers.items()}
    total = sum(sum(o.values()) for _, o in spans)
    right = sum(o.get(role_of[spk], 0) for spk, o in spans)
    return (round(right / total, 3) if total else None), len({u["speaker"] for u in utterances})


def evidence_timing(expected: dict, scored_items: list[dict], truth: list[dict], tolerance: float = 1.5) -> tuple[int, int]:
    """For items both sides agree on with a quote, does the stored start_time land on the line
    the expected quote comes from? Returns (matched, checked)."""
    got = {i["item_id"]: i for i in scored_items}
    matched = checked = 0
    for exp in expected["items"]:
        quote = normalize(exp.get("evidence_quote") or "")
        item = got.get(exp["item_id"])
        if not quote or not item or item["verdict"] != exp["verdict"] or item.get("start_time") is None:
            continue
        line = next((t for t in truth if quote in normalize(t["text"])), None)
        if line is None:
            continue
        checked += 1
        matched += line["start"] - tolerance <= item["start_time"] <= line["end"] + tolerance
    return matched, checked


def ratio(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def summarize(calls: list[dict]) -> dict:
    scored = [c for c in calls if c.get("items")]
    items = [i for c in scored for i in c["items"]]
    crit = [i for i in items if i["critical"]]
    diar = [c["diarization"] for c in calls if c.get("diarization") is not None]
    timing = [c["timing"] for c in scored if c.get("timing")]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    costs = [c["llm_cost_usd"] for c in scored if c.get("llm_cost_usd") is not None]
    return {
        "calls": len(calls), "scored": len(scored),
        "item_accuracy": ratio(sum(i["correct"] for i in items), len(items)),
        "critical_accuracy": ratio(sum(i["correct"] for i in crit), len(crit)),
        "pass_fail_accuracy": ratio(sum(c["passed"] == c["expected_passed"] for c in scored), len(scored)),
        "needs_review_rate": ratio(sum(i["needs_review"] for i in items), len(items)),
        "diarization_accuracy": f"{100 * mean(diar):.0f}%" if diar else "n/a",
        "evidence_timing": ratio(sum(t[0] for t in timing), sum(t[1] for t in timing)),
        "transcription_s": mean([c["transcription_s"] for c in calls if c.get("transcription_s") is not None]),
        "scoring_s": mean([c["scoring_s"] for c in scored if c.get("scoring_s") is not None]),
        "llm_cost_usd": mean(costs) if len(costs) == len(scored) and scored else None,
        "deepgram_cost_usd": mean([c["deepgram_cost_usd"] for c in calls if c.get("deepgram_cost_usd") is not None]),
    }


def per_item(calls: list[dict]) -> list[tuple[str, str]]:
    totals: dict[str, list[int]] = {}
    for c in calls:
        for i in c.get("items") or []:
            t = totals.setdefault(i["item"], [0, 0])
            t[0] += i["correct"]
            t[1] += 1
    return [(item, ratio(*t)) for item, t in sorted(totals.items())]


def markdown(summary: dict, calls: list[dict], provider: str) -> str:
    money = lambda v: f"${v:.4f}" if v is not None else "price not set"  # noqa: E731
    secs = lambda v: f"{v:.1f} s" if v is not None else "n/a"  # noqa: E731
    lines = [f"Eval: {summary['scored']}/{summary['calls']} calls scored ({provider})", "",
             "| Metric | Result |", "| --- | --- |",
             f"| Item accuracy | {summary['item_accuracy']} |",
             f"| Critical-item accuracy | {summary['critical_accuracy']} |",
             f"| Pass/fail accuracy | {summary['pass_fail_accuracy']} |",
             f"| needs_review rate | {summary['needs_review_rate']} |",
             f"| Diarization accuracy | {summary['diarization_accuracy']} |",
             f"| Evidence on the right line | {summary['evidence_timing']} |",
             f"| Avg transcription latency per call | {secs(summary['transcription_s'])} |",
             f"| Avg scoring latency per call | {secs(summary['scoring_s'])} |",
             f"| Avg LLM cost per call | {money(summary['llm_cost_usd'])} |",
             f"| Avg Deepgram cost per call | {money(summary['deepgram_cost_usd'])} |",
             "", "| Call | Items correct | Pass/fail | needs_review | Speakers | Diarization |", "| --- | --- | --- | --- | --- | --- |"]
    for c in calls:
        if c.get("items"):
            correct = sum(i["correct"] for i in c["items"])
            pf = "ok" if c["passed"] == c["expected_passed"] else f"got {c['passed']}, expected {c['expected_passed']}"
            review = sum(i["needs_review"] for i in c["items"])
            diar = "n/a" if c.get("diarization") is None else f"{100 * c['diarization']:.0f}%"
            lines.append(f"| {c['call_id']} | {correct}/{len(c['items'])} | {pf} | {review} | {c.get('speakers', '-')} | {diar} |")
        else:
            lines.append(f"| {c['call_id']} | failed: {c.get('error', 'not scored')[:60]} | | | | |")
    lines += ["", "| Rubric item | Accuracy |", "| --- | --- |"] + [f"| {item} | {acc} |" for item, acc in per_item(calls)]
    return "\n".join(lines)
