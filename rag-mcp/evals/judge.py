"""LLM judge for answer faithfulness, with a fixed rubric.

The judge sees the question, each answer statement, the full text of the sources that statement
cites, and the key facts from questions.jsonl. It grades each statement against its own sources
only, so it measures faithfulness to the documents, not general correctness.
"""

from rag.answer import LLM

RUBRIC = """You grade answers from a document question-answering system. You get the question, the answer's statements with the full text of the sources each statement cites, and the key facts a complete answer should contain.

Grade each statement against its cited sources only:
- supported: everything the statement says is stated in its cited sources or follows directly from them.
- partial: part of the statement is supported and part is not in its cited sources.
- unsupported: the cited sources do not support the statement, or contradict it.

A statement may be in Arabic and cite an English source, or the reverse; a faithful translation counts as supported. Rounding, rewording and combining two facts from the cited sources are fine. Adding anything the sources do not say is not.

For each key fact, in the order given, mark covered when the answer states it, in either language, and missing otherwise.

Use no outside knowledge. Keep each reason to one short sentence."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statements", "facts"],
    "properties": {
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "verdict", "reason"],
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["supported", "partial", "unsupported"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fact", "covered"],
                "properties": {"fact": {"type": "string"}, "covered": {"type": "boolean"}},
            },
        },
    },
}

SCORE = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}


def judge_prompt(question: str, statements: list[dict], sources: dict[int, str], facts: list[str]) -> str:
    parts = [f"Question: {question}", "", "Statements:"]
    for i, st in enumerate(statements, start=1):
        parts.append(f"[{i}] {st['text']}")
        for c in st["citations"]:
            parts.append(f"    cites source {c['n']}")
    parts += ["", "Sources:"]
    for n in sorted(sources):
        parts.append(f'<source n="{n}">\n{sources[n]}\n</source>')
    parts += ["", "Key facts:"] + [f"- {f}" for f in facts] if facts else ["", "Key facts: none given"]
    return "\n".join(parts)


async def judge_answer(llm: LLM, question: str, statements: list[dict], sources: dict[int, str], facts: list[str]) -> dict:
    """Returns {statements: [{index, verdict, reason}], facts: [{fact, covered}], faithfulness, model, tokens}."""
    result = await llm.complete_json(RUBRIC, judge_prompt(question, statements, sources, facts), SCHEMA)
    verdicts = {v["index"]: v for v in result.data.get("statements", []) if isinstance(v.get("index"), int)}
    graded = []
    for i in range(1, len(statements) + 1):
        v = verdicts.get(i, {"index": i, "verdict": "unsupported", "reason": "not graded by the judge"})
        graded.append({"index": i, "verdict": v["verdict"] if v["verdict"] in SCORE else "unsupported",
                       "reason": str(v.get("reason", ""))})
    returned = result.data.get("facts", [])
    if len(returned) == len(facts):  # same order as asked
        fact_results = [{"fact": f, "covered": bool(r.get("covered"))} for f, r in zip(facts, returned, strict=True)]
    else:
        covered = {str(r.get("fact")): bool(r.get("covered")) for r in returned}
        fact_results = [{"fact": f, "covered": covered.get(f, False)} for f in facts]
    faithfulness = sum(SCORE[g["verdict"]] for g in graded) / len(graded) if graded else None
    return {"statements": graded, "facts": fact_results, "faithfulness": faithfulness, "model": result.model,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens}
