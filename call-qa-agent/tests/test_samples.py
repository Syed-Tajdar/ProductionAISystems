"""Consistency checks between rubrics/, samples/scripts/ and samples/expected.json."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUBRICS = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((ROOT / "rubrics").glob("*.yaml"))}
EXPECTED = json.loads((ROOT / "samples" / "expected.json").read_text(encoding="utf-8"))
CALLS = EXPECTED["calls"]
LINE = re.compile(r"^\[(\d{2}):(\d{2})\] (AGENT|CUSTOMER): (.+)$")


def read_script(rel: str) -> tuple[dict, list[tuple[int, str, str]]]:
    text = (ROOT / "samples" / rel).read_text(encoding="utf-8")
    _, front, body = text.split("---", 2)
    lines = [m.groups() for m in map(LINE.match, body.splitlines()) if m]
    return yaml.safe_load(front), [(int(mm) * 60 + int(ss), who, said) for mm, ss, who, said in lines]


def test_three_rubrics():
    assert set(RUBRICS) == {"us_insurance", "gulf_real_estate", "customer_support"}


@pytest.mark.parametrize("slug", RUBRICS)
def test_rubric_shape(slug):
    rubric = RUBRICS[slug]
    assert rubric["id"] == slug
    ids = [i["id"] for i in rubric["items"]]
    assert len(ids) == len(set(ids))
    assert sum(i["weight"] for i in rubric["items"]) == 100
    assert 0 < rubric["pass_threshold"] <= 100
    for item in rubric["items"]:
        assert {"title", "weight", "critical", "pass_if", "fail_if"} <= set(item)


def test_ten_calls_with_two_mixed_language():
    assert len(CALLS) == 10
    assert sum(c["language"] == "ar-en" for c in CALLS) == 2
    assert {c["expected_passed"] for c in CALLS} == {True, False}


@pytest.mark.parametrize("call", CALLS, ids=lambda c: c["call_id"])
def test_call_matches_script_and_rubric(call):
    front, lines = read_script(call["script"])
    assert front["id"] == call["call_id"]
    assert front["rubric"] == call["rubric"]
    assert 60 <= front["duration_estimate_sec"] <= 120
    assert {"AGENT", "CUSTOMER"} == {who for _, who, _ in lines}

    rubric = RUBRICS[call["rubric"]]
    assert [i["item_id"] for i in call["items"]] == [i["id"] for i in rubric["items"]]

    for item in call["items"]:
        assert item["verdict"] in {"pass", "fail", "na"}
        if item["evidence_quote"]:
            hits = [start for start, _, said in lines if item["evidence_quote"] in said]
            assert len(hits) == 1, f"{item['item_id']}: quote must appear exactly once"
            assert abs(hits[0] - item["start_time"]) <= 1
        else:
            assert item["start_time"] is None


@pytest.mark.parametrize("call", CALLS, ids=lambda c: c["call_id"])
def test_expected_score_follows_rubric(call):
    rubric = {i["id"]: i for i in RUBRICS[call["rubric"]]["items"]}
    verdicts = {i["item_id"]: i["verdict"] for i in call["items"]}
    applicable = sum(rubric[k]["weight"] for k, v in verdicts.items() if v != "na")
    passed = sum(rubric[k]["weight"] for k, v in verdicts.items() if v == "pass")
    score = round(100 * passed / applicable, 1)
    critical_fail = any(v == "fail" and rubric[k]["critical"] for k, v in verdicts.items())
    assert call["expected_overall_score"] == score
    assert call["expected_passed"] == (score >= RUBRICS[call["rubric"]]["pass_threshold"] and not critical_fail)
