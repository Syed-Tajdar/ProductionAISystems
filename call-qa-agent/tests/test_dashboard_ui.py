"""The dashboard's presentation logic: titles, speaker roles, verdict reasons, item grouping, run chips,
and what the redesigned list and detail pages render."""

from app.dashboard import call_title, fail_line, group_items, language_label, results_summary, run_chips, speaker_roles, verdict_reason
from tests.test_dashboard import SCORE, UTTERANCES, call, client, item  # noqa: F401 (client is a fixture)


def items_with_meta(raw, critical=()):
    return [{**i, "title": i["item_id"].replace("_", " "), "critical": i["item_id"] in critical} for i in raw]


def test_titles_and_languages():
    assert call_title({"title": "US insurance sales: DNC request ignored", "filename": "x.wav"}) == "US insurance sales: DNC request ignored"
    assert call_title({"filename": "demo_call_04_us_insurance.wav"}) == "Call 04 us insurance"
    assert call_title({"filename": ""}) == "Untitled call"
    assert (language_label("en"), language_label("ar-en"), language_label("multi"), language_label("fr")) == \
        ("English", "Arabic + English", "Arabic + English", "FR")


def test_speaker_roles_come_from_the_evidence():
    assert speaker_roles(SCORE["items"], UTTERANCES) == {0: "Agent", 1: "Customer"}
    three = UTTERANCES + [{"speaker": 2, "start": 80, "end": 81, "text": "hello"}]
    assert speaker_roles(SCORE["items"], three) == {0: "Agent", 1: "Speaker 1", 2: "Speaker 2"}
    no_evidence = [item("x", "na"), item("y", "needs_review", "q", None)]
    assert speaker_roles(no_evidence, UTTERANCES) == {0: "Speaker 0", 1: "Speaker 1"}
    assert speaker_roles([item("x", "pass", "q", 99)], UTTERANCES) == {0: "Speaker 0", 1: "Speaker 1"}  # bad index


def test_verdict_reasons():
    raw = items_with_meta([item("a", "fail"), item("b", "fail"), item("c", "pass")], critical=("a",))
    assert verdict_reason(None, raw, 80) == ""
    assert verdict_reason({"passed": None, "overall_score": 70}, raw, 80) == "A critical item needs review before this call can pass."
    assert verdict_reason({"passed": False, "overall_score": 55.4}, raw, 80) == "Critical item failed: a."
    no_critical = items_with_meta([item("b", "fail")])
    assert verdict_reason({"passed": False, "overall_score": 55.4}, no_critical, 80) == "Score 55 is below the pass mark of 80."
    assert verdict_reason({"passed": True, "overall_score": 91}, no_critical, 80) == "Score 91 meets the pass mark of 80, and no critical item failed."
    assert verdict_reason({"passed": True, "overall_score": 91}, no_critical, None) == "Score 91 meets the pass mark, and no critical item failed."


def test_failed_items_come_first_critical_on_top():
    raw = items_with_meta([item("p", "pass"), item("r", "needs_review"), item("f", "fail"), item("cf", "fail"), item("n", "na")],
                          critical=("cf",))
    groups = group_items(raw)
    assert [i["item_id"] for i in groups["attention"]] == ["cf", "f", "r"]
    assert [i["item_id"] for i in groups["passed"]] == ["p"] and [i["item_id"] for i in groups["na"]] == ["n"]
    assert results_summary(raw) == "1 of 4 checks passed. Failed: f, cf."


def test_run_chips():
    assert run_chips(None) == []
    assert run_chips(SCORE) == ["gemini/gemini-3.8-flash", "1,500 in · 400 out tokens", "2.1 s"]
    assert run_chips({**SCORE, "cost_usd": 0.01})[-1] == "$0.0100"


def test_list_shows_titles_tags_and_a_colored_score(client):
    html = client([call(title="US insurance sales: pushy pitch", is_demo=True)]).get("/dashboard").text
    assert '<span class="call-title">US insurance sales: pushy pitch</span>' in html
    assert '<span class="tag">US insurance sales call</span>' in html and '<span class="tag">English</span>' in html
    assert '<span class="tag mono">01:21</span>' in html
    assert '<span class="score-badge review" aria-label="score">61</span>' in html


def test_detail_header_groups_roles_and_chips(client):
    html = client([call(title="US insurance sales: pushy pitch", summary="Pushy agent. Promises guaranteed approval.")]) \
        .get("/dashboard/calls/c1").text
    assert "<h1>US insurance sales: pushy pitch</h1>" in html
    assert '<p class="call-summary">Pushy agent. Promises guaranteed approval.</p>' in html  # in the Summary section
    assert '<p class="filename mono">call_03.wav</p>' in html
    assert 'class="ring review"' in html and 'stroke-dasharray="163.36"' in html and 'stroke-dashoffset="63.53"' in html
    assert '<span class="verdict-reason">A critical item needs review before this call can pass.</span>' in html
    assert html.index("No misleading") < html.index("<summary>Passed (2)</summary>")  # failed items before passed ones
    assert "<summary>Not applicable (1)</summary>" in html
    assert '<span class="who agent">Agent</span>' in html and '<span class="who customer">Customer</span>' in html
    assert '<li class="chip mono">1,500 in · 400 out tokens</li>' in html
    tech = html[html.index('<details class="tech">'):]
    assert tech.index("<summary>Technical details</summary>") < tech.index("1,500 in")  # collapsed, model and tokens inside
    assert '<a class="to-rubric" href="#rubric">' in html and 'id="rubric"' in html


def test_detail_order_and_tabs_without_script(client):
    html = client([call(title="US insurance sales: pushy pitch", summary="Pushy agent.")]).get("/dashboard/calls/c1").text
    order = ["<h1>", 'class="ring review"', 'class="verdict"', 'data-tabs hidden', 'id="summary" data-tab-panel',
             'id="transcript" data-tab-panel', 'id="checks" data-tab-panel']
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions)  # title, score, verdict, then Summary, Transcript and Checks in order
    # Without script the tab bar stays hidden and every section shows, each with its own heading.
    assert '<div class="tabs" role="tablist" aria-label="Call details" data-tabs hidden>' in html
    assert html.count("data-tab-panel") == 3 and "<section class=\"panel\" id=\"summary\" data-tab-panel hidden" not in html
    for title in ("Summary", "Transcript", "Checks"):
        assert f'<h2 id="{title.lower()}-title">{title}</h2>' in html
    checks = html[html.index('id="checks" data-tab-panel'):]
    assert '<details class="how" id="how-scoring">' in checks  # collapsed by default (no open attribute)
    assert 'src="http://testserver/static/dashboard.js" defer' in html


def test_processing_call_polls_with_a_noscript_fallback(client):
    c = client([call(id="c2", status="scoring", scores=[])])
    html = c.get("/dashboard/calls/c2").text
    assert '<noscript><meta http-equiv="refresh" content="10"></noscript>' in html
    assert '<span class="tag status scoring" data-call-status="c2" data-status="scoring">scoring</span>' in html
    assert 'This call is <span data-call-status="c2" data-status="scoring">scoring</span>.' in html
    listing = c.get("/dashboard").text
    assert '<noscript><meta http-equiv="refresh" content="10"></noscript>' in listing
    assert 'data-call-status="c2" data-status="scoring"' in listing


def test_filters_keep_an_apply_button_for_pages_without_script(client):
    html = client([call()]).get("/dashboard").text
    assert '<button type="submit" data-apply>Apply</button>' in html and '<form class="filters" method="get" action="/dashboard"' in html


def test_dashboard_script(client):
    js = client([]).get("/static/dashboard.js").text
    assert "requestSubmit" in js and "[data-apply]" in js  # filters on change, Apply hidden
    assert '[role="tab"]' in js and "ArrowRight" in js and "data-tab-panel" in js  # tabs
    assert "/calls/${encodeURIComponent(id)}" in js and "location.reload()" in js and "10000" in js  # polling


def test_detail_without_summary_falls_back_to_the_results(client):
    html = client([call()]).get("/dashboard/calls/c1").text
    assert "<h1>Call 03</h1>" in html and "2 of 4 checks passed. Failed: " in html


def test_layout_matches_the_portfolio(client):
    html = client([call()]).get("/dashboard").text
    assert '<meta name="color-scheme" content="light dark">' in html
    assert 'rel="icon" href="http://testserver/static/icon.svg"' in html and 'class="mark"' in html
    assert 'href="https://syed-tajdar.vercel.app" class="back">' in html


def test_fail_line_names_the_critical_item_or_the_pass_mark():
    rubric = SCORE["rubrics"]
    failed = {**SCORE, "passed": False, "overall_score": 72.4}
    assert fail_line(failed, rubric, 80) == "Critical item failed: No misleading claims"
    two = {**failed, "items": [*SCORE["items"], item("recording_disclosure", "fail")]}
    assert fail_line(two, rubric, 80).endswith(" and 1 more")
    below = {**failed, "items": [item("needs_discovery", "fail"), item("recording_disclosure", "pass")]}
    assert fail_line(below, rubric, 80) == "Below pass mark (72 of 80)"
    assert fail_line(below, rubric, None) == "Below pass mark (72)"
    assert fail_line(SCORE, rubric, 80) == "" and fail_line({**SCORE, "passed": True}, rubric, 80) == "" and fail_line(None, {}, 80) == ""


def test_list_shows_why_a_call_failed_and_no_file_name(client):
    critical = {**SCORE, "passed": False}
    below = {**SCORE, "passed": False, "overall_score": 72.4, "items": [item("needs_discovery", "fail")]}
    html = client([call(scores=(critical,)), {**call(scores=(below,)), "id": "c2"}, {**call(), "id": "c3"}]).get("/dashboard").text
    assert '<span class="fail-reason">Critical item failed: No misleading claims</span>' in html
    assert '<span class="fail-reason">Below pass mark (72 of 80)</span>' in html  # us_insurance pass mark is 80
    assert html.count('class="fail-reason"') == 2  # not under the undecided call
    assert "call_03.wav" not in html


def test_footer_and_demo_card_come_from_the_shared_ui(client):
    import json
    from pathlib import Path
    profile = json.loads((Path(__file__).parents[1] / "app/profile.json").read_text())
    html = client([call()]).get("/dashboard").text
    assert f"<span>Built by {profile['shortName']}</span>" in html
    assert f'<a href="{profile["linkedin"]}" target="_blank" rel="noopener">LinkedIn' in html
    assert '<a href="https://syed-tajdar.vercel.app/#contact">Contact</a>' in html
    assert 'data-du-card="call-qa"' in html and "<h2 id=\"du-card-call-qa\">What this demo shows</h2>" in html
    assert html.count("<li>", html.index("du-points"), html.index("</ol>", html.index("du-points"))) == 3
    assert 'data-du-dismiss hidden' in html  # the close button needs the script
    assert 'src="http://testserver/static/demo-ui.js"' in html


def test_shared_ui_assets(client):
    c = client([])
    js, css = c.get("/static/demo-ui.js").text, c.get("/static/demo-ui.css").text
    assert "try { return window.localStorage.getItem" in js and "try { window.localStorage.setItem" in js
    assert ".du-footer" in css and ".du-state.du-limit" in css


def test_empty_list_has_a_styled_empty_state(client):
    html = client([]).get("/dashboard").text
    assert '<div class="du-state du-empty" role="status">' in html and "No calls yet" in html


def test_pages_get_html_errors_and_the_api_keeps_json(client):
    c = client([call()])
    page = c.get("/dashboard/calls/missing")
    assert page.status_code == 404 and "text/html" in page.headers["content-type"]
    assert '<p class="du-state-title">Call not found</p>' in page.text and "Built by" in page.text
    api = c.get("/calls/missing")
    assert api.status_code == 404 and api.json() == {"detail": "call not found"}


def test_database_error_page(client):
    from app.db import SupabaseError
    from app.deps import get_db
    from app.main import app

    class Down:
        def list_calls(self, *a, **k):
            raise SupabaseError(503, "unavailable")

    c = client([])
    app.dependency_overrides[get_db] = lambda: Down()
    page = c.get("/dashboard")
    assert page.status_code == 503 and '<div class="du-state du-error" role="alert">' in page.text
    assert "unavailable" not in page.text  # no internals on the page


def test_wait_phrases():
    from app.ui import templates
    wait = templates.env.globals["ui"].wait
    assert [str(wait(s)) for s in (30, 600, 7200)] == ["in a minute", "in about 10 minutes", "in about 2 hours"]


def scored(passed, points, items, cost=None, latency=None, slug="us_insurance"):
    return {**SCORE, "passed": passed, "overall_score": points, "items": items, "cost_usd": cost, "latency_ms": latency,
            "rubrics": {**SCORE["rubrics"], "slug": slug}}


def test_overview_numbers():
    from app.dashboard import overview
    calls = [
        call(scores=[scored(True, 90, [item("recording_disclosure", "pass"), item("needs_discovery", "fail")], 0.01, 2000)]),
        call(scores=[scored(False, 50, [item("recording_disclosure", "fail"), item("needs_discovery", "fail")], 0.02, 3000)]),
        call(scores=[scored(None, 70, [item("recording_disclosure", "pass"), item("needs_discovery", "na")])]),
        call(scores=[]),
    ]
    o = overview(calls)
    assert (o["calls"], o["scored"], o["decided"], o["passed"], o["pass_rate"]) == (4, 3, 2, 1, 50)
    assert (o["avg_score"], o["critical_calls"], o["costed"], o["avg_latency_s"]) == (70, 1, 2, 2.5)
    assert abs(o["avg_cost"] - 0.015) < 1e-9
    top = [(f["title"], f["fails"], f["applied"], f["percent"], f["critical"]) for f in o["top_failing"]]
    # needs_discovery failed on both calls it applied to (na does not count); disclosure on 1 of 3
    assert top == [("Needs discovery", 2, 2, 100, False), ("Recording disclosure", 1, 3, 33, True)]


def test_overview_without_scores_or_costs():
    from app.dashboard import overview
    o = overview([call(scores=[])])
    assert (o["scored"], o["pass_rate"], o["avg_score"], o["avg_cost"], o["avg_latency_s"], o["top_failing"]) == \
        (0, None, None, None, None, [])


def test_filters_are_query_params_and_paging_keeps_them(client):
    calls = [{**call(id=f"p{i}", scores=[scored(True, 90, [])]), "language": "en"} for i in range(27)]
    calls += [{**call(id="f1", scores=[scored(False, 40, [item("needs_discovery", "fail")])]), "language": "ar"}]
    c = client(calls)
    page = c.get("/dashboard?result=pass&language=en").text
    assert page.count('class="call-card"') == 25 and "/dashboard/calls/f1" not in page
    assert '<option value="pass" selected>Pass</option>' in page and '<option value="en" selected>English</option>' in page
    assert '<a href="/dashboard?result=pass&amp;language=en&amp;page=2">Older' in page
    assert '<a href="/dashboard">Clear</a>' in page
    second = c.get("/dashboard?result=pass&language=en&page=2").text
    assert second.count('class="call-card"') == 2 and '<a href="/dashboard?result=pass&amp;language=en">&larr; Newer</a>' in second
    fails = c.get("/dashboard?result=fail").text
    assert fails.count('class="call-card"') == 1 and '<dt>Pass rate</dt><dd>0%' in fails  # the overview follows the filter
    nothing = c.get("/dashboard?result=fail&language=en").text
    assert "No calls match these filters" in nothing and 'class="overview"' not in nothing
    stale = c.get("/dashboard?rubric=gone&result=bogus").text  # unknown values are ignored
    assert stale.count('class="call-card"') == 25 and "Clear</a>" not in stale


def test_list_overview_and_chart_markup(client):
    calls = [call(scores=[scored(False, 40, [item("needs_discovery", "fail")], 0.01, 1500)])]
    html = client(calls).get("/dashboard").text
    assert '<dt>Calls scored</dt><dd>1<span class="kpi-sub">of 1</span></dd>' in html
    assert '<dt>Avg cost per call</dt><dd>$0.0100' in html and '<dt>Avg scoring time</dt><dd>1.5 s</dd>' in html
    assert '<span class="bar-label">Needs discovery</span>' in html and '<span class="bar-value">1 of 1</span>' in html
    assert '<span class="bar w-100"></span>' in html and ' style="' not in html  # the CSP allows no inline styles


def test_player_only_with_stored_audio_and_lines_carry_their_start(client):
    plain = client([call()]).get("/dashboard/calls/c1").text
    assert "<audio" not in plain and "call.js" not in plain
    assert '<div class="line cited" id="u-0" data-start="0.0">' in plain
    assert '<a class="seek" href="#u-2"><time>01:05</time></a>' in plain
    html = client([call(storage_path="demo/call_03.wav", is_demo=True)]).get("/dashboard/calls/c1").text
    assert '<audio controls preload="metadata" src="/calls/c1/audio" data-src="/calls/c1/audio" data-call-audio>' in html
    assert 'src="http://testserver/static/call.js" defer' in html and "Synthetic voices" in html
    assert '<a class="seek" href="#u-2" aria-label="Play from 01:05"><time>01:05</time></a>' in html


def test_item_time_follows_the_transcript_line():
    from app.dashboard import detail_view
    moved = [{**u, "start": u["start"] + 3} for u in UTTERANCES]  # transcript retimed to the audio after scoring
    view = detail_view(call(transcripts={"utterances": moved}))
    misleading = next(i for i in view["items"] if i["item_id"] == "no_misleading_claims")
    assert misleading["time"] == "01:08"  # the line's start now, not the 01:05 stored with the score


def test_player_script(client):
    js = client([]).get("/static/call.js").text
    assert 'classList.add("playing")' in js and "currentTime" in js and "?fresh=" in js


def test_scoring_panel_shows_the_rubric_and_this_calls_arithmetic(client):
    from app.dashboard import scoring_rules
    rubric = SCORE["rubrics"]
    view = [{**i, "verdict": i["verdict"]} for i in SCORE["items"]]
    rules = scoring_rules(rubric, view, 80)
    weights = {i["id"]: i["weight"] for i in rubric["items"]}
    # Same arithmetic as scoring.overall(): na and unscored checks are left out, needs_review counts as applicable.
    assert rules["got"] == weights["agent_identification"] + weights["recording_disclosure"]
    assert rules["applicable"] == rules["got"] + weights["no_misleading_claims"] + weights["dnc_handling"]
    from app.rubrics import load_rubric
    from app.scoring import overall
    assert round(100 * rules["got"] / rules["applicable"], 2) == overall(load_rubric("us_insurance"), SCORE["items"]).overall_score
    assert scoring_rules({}, [], 80) is None
    html = client([call()]).get("/dashboard/calls/c1").text
    assert '<details class="how" id="how-scoring">' in html and "<summary>How scoring works</summary>" in html
    assert "at least 80 and no failed critical check" in html
    assert '<span class="how-title">Recording disclosure<span class="crit">critical</span></span>' in html
    assert f'{rules["got"]} of {rules["applicable"]} applicable weight passed = 50.0 &middot; pass mark 80' in html


def test_view_as_json_link(client):
    html = client([call()]).get("/dashboard/calls/c1").text
    assert '<a href="/calls/c1" class="json-link" target="_blank" rel="noopener">View as JSON' in html


def test_pages_load_vercel_web_analytics(client):
    assert '<script defer src="/_vercel/insights/script.js"></script>' in client([]).get("/dashboard").text
