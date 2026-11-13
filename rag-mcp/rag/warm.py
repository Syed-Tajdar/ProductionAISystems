"""Warm the shared answer cache with the page's example questions, so the example chips answer at once.

Runs outside the web app (python -m rag.cli warm; the warm-rag-cache workflow runs it after each production deploy
and once a day). Answers are written straight into the answer cache under the key the page and MCP use for a
question asked without a language, so nothing goes through the visitor limiter: no visitor's per-address limit
and nothing of the daily cap is used. Each new answer costs one model call, as for a visitor.
"""

from dataclasses import dataclass, field

from rag.service import ServiceError

FRESH_HOURS = 20  # the cache keeps answers 24 h; a daily run refreshes anything older than this


@dataclass
class WarmReport:
    answered: list[str] = field(default_factory=list)
    fresh: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def page_key(question: str) -> str:
    """The cache key of a question asked on the page or over MCP (no language given)."""
    return f"|{question}"


async def warm(service, cache, questions: list[str], *, force: bool = False, fresh_hours: float = FRESH_HOURS,
               log=print) -> WarmReport:
    """Answers each question unless the cache has an answer younger than fresh_hours (force: answer all again,
    for a deploy that may have changed how answers are made)."""
    report = WarmReport()
    for question in questions:
        key = page_key(question)
        age = None if force else await cache.age(key)
        if age is not None and age < fresh_hours * 3600:
            report.fresh.append(question)
            log(f"fresh   {question} ({age / 3600:.1f} h old)")
            continue
        try:
            answer = await service.ask(question, trace={})
        except ServiceError as e:
            report.failed[question] = str(e)
            log(f"failed  {question}: {e}")
            continue
        await cache.store(key, answer)
        report.answered.append(question)
        log(f"cached  {question} ({'answerable' if answer.answerable else 'not in the documents'}, "
            f"{(answer.trace or {}).get('timings_ms', {}).get('total', '?')} ms)")
    return report
