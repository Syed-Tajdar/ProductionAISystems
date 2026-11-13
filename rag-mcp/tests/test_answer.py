from types import SimpleNamespace

import pytest

from rag.answer import NOT_FOUND, SCHEMA, AnswerError, ClaudeLLM, LLMResult, answer_question, build_prompt, normalize
from rag.db import Hit

pytestmark = pytest.mark.anyio


def hit(i, content, section="3.3 Late and returned payments", path="3. Rent and payments > 3.3 Late and returned payments",
        page=2, title="Tenant Handbook"):
    return Hit(i, "en/tenant-handbook.pdf", title, section, path, "en", page, page, content, i, i, None, 0.8, 0.03)


HITS = [
    hit(11, "3.3.1 A returned cheque costs AED 500 and must be replaced within 7 days by a bank transfer or a certified cheque."),
    hit(12, "3.3.3 Rent paid by transfer more than 10 days late incurs a late payment fee of AED 300."),
    hit(30, "5.2.1 Up to two pets are allowed per unit with written approval.", section="5.2 Pets", path="5. Building rules > 5.2 Pets", page=3),
]


class FakeLLM:
    def __init__(self, data):
        self.data, self.calls = data, []

    async def complete_json(self, system, user, schema):
        self.calls.append((system, user, schema))
        return LLMResult(self.data, "claude-sonnet-5-5", 900, 120, 1500)


def st(text, *cites):
    return {"text": text, "citations": [{"source": n, "quote": q} for n, q in cites]}


async def test_cited_answer_is_rendered_with_renumbered_sources():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("A returned cheque costs AED 500.", (2, "late payment fee of AED 300"), (1, "A returned cheque costs AED 500")),
        st("It must be replaced within 7 days.", (1, "must be replaced within 7 days")),
    ]})
    a = await answer_question("What happens if my cheque bounces?", HITS, llm)
    assert a.answerable
    assert a.text == "A returned cheque costs AED 500. [1][2] It must be replaced within 7 days. [2]"
    assert [(s["n"], s["chunk_id"]) for s in a.sources] == [(1, 12), (2, 11)]  # numbered by first citation
    assert a.sources[1]["label"] == "Tenant Handbook, 3. Rent and payments > 3.3 Late and returned payments, page 2"
    assert a.sources[1]["quotes"] == ["A returned cheque costs AED 500", "must be replaced within 7 days"]
    assert (a.model, a.input_tokens, a.output_tokens) == ("claude-sonnet-5-5", 900, 120)


async def test_quotes_are_matched_ignoring_case_spacing_and_punctuation():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("Fee is AED 500.", (1, "a RETURNED cheque  costs, AED 500"))]})
    a = await answer_question("q", HITS, llm)
    assert a.answerable and a.sources[0]["chunk_id"] == 11


async def test_unsupported_statements_are_dropped():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("A returned cheque costs AED 500.", (1, "A returned cheque costs AED 500")),
        st("You will also be blacklisted.", (1, "you will be blacklisted by the bank")),  # quote not in source
        st("Pets need approval.", (7, "written approval")),  # source 7 was never retrieved
        st("Card payments are free.", ),  # no citation at all
    ]})
    a = await answer_question("q", HITS, llm)
    assert a.text == "A returned cheque costs AED 500. [1]"
    assert [d["text"] for d in a.dropped] == ["You will also be blacklisted.", "Pets need approval.", "Card payments are free."]


async def test_tiny_quotes_do_not_count():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [st("Yes.", (1, "a"))]})
    a = await answer_question("q", HITS, llm)
    assert (a.answerable, a.text) == (False, NOT_FOUND["en"])


async def test_not_answerable_and_nothing_supported_both_say_not_in_documents():
    for data in ({"answerable": False, "statements": [], "not_covered": "The documents do not mention parking fines."},
                 {"answerable": True, "statements": [st("Made up.", (1, "nothing like this appears"))], "not_covered": ""}):
        a = await answer_question("What is the parking fine?", HITS, FakeLLM(data))
        assert (a.answerable, a.text, a.sources) == (False, NOT_FOUND["en"], [])
    assert a.dropped[0]["text"] == "Made up."


async def test_partial_answer_reports_what_is_missing():
    llm = FakeLLM({"answerable": True, "not_covered": "The sources do not say whether the fee includes VAT.", "statements": [
        st("A returned cheque costs AED 500.", (1, "A returned cheque costs AED 500"))]})
    a = await answer_question("q", HITS, llm)
    assert a.answerable and a.not_covered.startswith("The sources do not say")


async def test_no_retrieved_chunks_skips_the_model():
    llm = FakeLLM({})
    a = await answer_question("anything", [], llm)
    assert (a.answerable, a.text, llm.calls) == (False, NOT_FOUND["en"], [])


def test_prompt_lists_numbered_sources_with_metadata():
    prompt = build_prompt("  Can I keep a cat? ", HITS)
    assert '<source id="3" document="Tenant Handbook" section="5. Building rules > 5.2 Pets" page="3">' in prompt
    assert prompt.rstrip().endswith("Question: Can I keep a cat?")


def test_normalize():
    assert normalize("  AED 500, within 7 Days.") == "aed 500 within 7 days"
    assert normalize("رسوم الشيك: 500 درهم") == "رسوم الشيك 500 درهم"


def test_normalize_arabic_spelling_variants():
    # alef forms, taa marbuta, alef maqsura, diacritics, tatweel and Arabic-Indic digits all match
    assert normalize("الإجازة السنويّة ٢٥ يوماً") == normalize("الاجازه السنويه 25 يوما")
    assert normalize("أسطوانة القفـــل") == normalize("اسطوانه القفل")
    assert normalize("مستشفى") == normalize("مستشفي")
    assert normalize("آخر تحديث، ١٥ فبراير") == "اخر تحديث 15 فبراير"


AR_HITS = [
    hit(41, "3.3.1 رسوم الشيك المرتجع 500 درهم، ويجب استبداله خلال 7 أيام بتحويل بنكي أو شيك مصدق.",
        section="3.3 الدفعات المتأخرة والشيكات المرتجعة", path="3. الإيجار والدفعات > 3.3 الدفعات المتأخرة والشيكات المرتجعة",
        page=None, title="دليل المستأجر"),
    hit(42, "1.1 The pool is open every day from 06:00 to 22:00.", section="1. Swimming pool",
        path="1. Swimming pool", page=None, title="Residents' Amenities Guide"),
]


async def test_arabic_question_gets_an_arabic_answer_citing_both_languages():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("رسوم الشيك المرتجع 500 درهم.", (1, "رسوم الشيك المرتجع 500 درهم")),
        st("يفتح المسبح من 06:00 إلى 22:00.", (2, "open every day from 06:00 to 22:00")),  # English source, Arabic statement
    ]})
    a = await answer_question("كم رسوم الشيك المرتجع ومتى يفتح المسبح؟", AR_HITS, llm)
    assert (a.answerable, a.language, a.direction) == (True, "ar", "rtl")
    assert a.text == "رسوم الشيك المرتجع 500 درهم. [1] يفتح المسبح من 06:00 إلى 22:00. [2]"
    assert "\u200f" not in a.text and "\u202b" not in a.text  # plain text, no bidi control characters
    system, _, _ = llm.calls[0]
    assert "Write the statements and not_covered in Arabic" in system


async def test_arabic_quote_with_spelling_variants_is_accepted():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("الرسوم 500 درهم.", (1, "رسوم الشيك المرتجع ٥٠٠ درهم، ويجب استبدالة"))]})  # Arabic digits, taa marbuta
    a = await answer_question("ما رسوم الشيك المرتجع؟", AR_HITS, llm)
    assert a.answerable and a.citations_valid == 1


async def test_arabic_not_found_and_language_override():
    llm = FakeLLM({"answerable": False, "statements": [], "not_covered": "لا تذكر المصادر ذلك."})
    a = await answer_question("هل تقدمون خدمات تنظيف؟", AR_HITS, llm)
    assert (a.answerable, a.text, a.direction) == (False, NOT_FOUND["ar"], "rtl")
    b = await answer_question("هل تقدمون خدمات تنظيف؟", AR_HITS, llm, language="en")
    assert (b.text, b.language) == (NOT_FOUND["en"], "en")
    assert "in English" in llm.calls[1][0]
    c = await answer_question("ما ساعات المسبح؟", [], FakeLLM({}))
    assert c.text == NOT_FOUND["ar"]


async def test_citation_counts():
    llm = FakeLLM({"answerable": True, "not_covered": "", "statements": [
        st("Fee AED 500.", (1, "A returned cheque costs AED 500"), (2, "not in this source at all")),
        st("Late fee AED 300.", (2, "late payment fee of AED 300"))]})
    a = await answer_question("q", HITS, llm)
    assert (a.citations_total, a.citations_valid) == (3, 2)


class FakeAnthropic:
    """Records the request and returns a canned Messages response."""

    class APIStatusError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code

    class APIConnectionError(Exception):
        pass

    def __init__(self, response=None, error=None):
        self.requests, self.response, self.error = [], response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.requests.append(kw)
        if self.error:
            raise self.error
        return self.response


def message(text, stop_reason="end_turn", model="claude-sonnet-5-5"):
    return SimpleNamespace(stop_reason=stop_reason, model=model, usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                           content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


def claude(fake):
    llm = ClaudeLLM("", client=fake)
    llm.anthropic = fake  # error classes
    return llm


async def test_claude_request_shape():
    fake = FakeAnthropic(message('{"answerable": false, "statements": [], "not_covered": ""}'))
    res = await claude(fake).complete_json("sys", "user text", SCHEMA)
    req = fake.requests[0]
    assert req["model"] == "claude-sonnet-5-5" and req["max_tokens"] == 16000
    assert req["betas"] == ["server-side-fallback-2026-07-01"] and req["fallbacks"] == "default"
    assert req["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert req["system"] == "sys" and req["messages"] == [{"role": "user", "content": "user text"}]
    assert res.data["answerable"] is False and res.model == "claude-sonnet-5-5"


async def test_claude_errors():
    for fake, match in [
        (FakeAnthropic(error=FakeAnthropic.APIStatusError(529)), "HTTP 529"),
        (FakeAnthropic(message("", stop_reason="refusal")), "declined"),
        (FakeAnthropic(message("not json")), "invalid JSON"),
        (FakeAnthropic(message('{"answerable"', stop_reason="max_tokens")), "max_tokens"),
    ]:
        with pytest.raises(AnswerError, match=match):
            await claude(fake).complete_json("s", "u", SCHEMA)


def test_claude_needs_a_key():
    with pytest.raises(AnswerError, match="ANTHROPIC_API_KEY"):
        ClaudeLLM("")


async def test_llm_translator_caches_and_survives_errors():
    from rag.retrieval import LLMTranslator

    llm = FakeLLM({"translation": " swimming pool hours "})
    t = LLMTranslator(llm)
    assert await t.translate("ساعات المسبح", "en") == "swimming pool hours"
    assert await t.translate("ساعات المسبح", "en") == "swimming pool hours"
    assert len(llm.calls) == 1 and "into English" in llm.calls[0][0]

    class Down:
        async def complete_json(self, *a):
            raise AnswerError("provider returned HTTP 529")

    assert await LLMTranslator(Down()).translate("x", "ar") is None


async def test_trace_records_every_quote_check_without_changing_the_answer():
    data = {"answerable": True, "not_covered": "", "statements": [
        st("Fee.", (1, "A returned cheque costs AED 500"), (9, "A returned cheque costs AED 500")),
        st("Short.", (1, "AED")),
        st("Made up.", (1, "the pool is open all night long")),
    ]}
    plain = await answer_question("What does a returned cheque cost?", HITS, FakeLLM(data))
    trace = {}
    traced = await answer_question("What does a returned cheque cost?", HITS, FakeLLM(data), trace=trace)
    assert traced.to_dict() == plain.to_dict() and traced.trace is trace and "trace" not in traced.to_dict()
    assert traced.to_dict(trace=True)["trace"] is trace
    assert [(c["source"], c["ok"], c["reason"]) for c in trace["answer"]["checks"]] == [
        (1, True, "found in the source"), (9, False, "no such source"), (1, False, "quote too short"),
        (1, False, "quote not in the source")]
    assert trace["answer"]["model"] == "claude-sonnet-5-5" and trace["timings_ms"]["answer"] == 1500
