"""Warming the answer cache with the example questions: same keys as the page, no visitor limits used."""

import pytest
from fastapi.testclient import TestClient

from rag.api import create_app
from rag.config import Settings
from rag.demo import EXAMPLES, AnswerCache, DemoLimiter
from rag.service import ServiceError
from rag.warm import page_key, warm

from .test_page_ui import CITED, Stub

pytestmark = pytest.mark.anyio


class Counting(Stub):
    def __init__(self, answer, fail=()):
        super().__init__(answer)
        self.asked, self.fail = [], set(fail)

    async def ask(self, question, language=None, trace=None):
        self.asked.append(question)
        if question in self.fail:
            raise ServiceError("provider returned HTTP 529")
        return self.answer


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


async def test_warmed_examples_are_answered_from_the_cache_without_using_any_limit():
    clock = Clock()
    cache, service = AnswerCache(clock=clock), Counting(CITED)
    report = await warm(service, cache, EXAMPLES, log=lambda *_: None)
    assert report.answered == EXAMPLES and service.asked == EXAMPLES and not report.failed
    limiter = DemoLimiter(daily_cap=0)  # no question at all would be allowed
    app = TestClient(create_app(Settings(_env_file=None), service=service, limiter=limiter, cache=cache))
    for example in EXAMPLES:  # what tapping an example chip sends
        page = app.post("/", data={"q": example})
        assert page.status_code == 200 and '<span class="statement">A returned cheque costs AED <span class="tail">500.' in page.text
    assert service.asked == EXAMPLES  # every one came from the cache
    assert limiter.used_today == 0 and limiter.clients == {}  # warming and the cached answers counted nothing


async def test_fresh_answers_are_kept_unless_forced():
    clock = Clock()
    cache, service = AnswerCache(clock=clock), Counting(CITED)
    await warm(service, cache, EXAMPLES[:2], log=lambda *_: None)
    clock.t += 3600
    again = await warm(service, cache, EXAMPLES[:2], log=lambda *_: None)
    assert again.answered == [] and again.fresh == EXAMPLES[:2]
    clock.t += 20 * 3600  # older than 20 hours: refreshed before the 24 h expiry
    older = await warm(service, cache, EXAMPLES[:2], log=lambda *_: None)
    assert older.answered == EXAMPLES[:2]
    forced = await warm(service, cache, EXAMPLES[:2], force=True, log=lambda *_: None)
    assert forced.answered == EXAMPLES[:2] and len(service.asked) == 6
    assert await cache.age(page_key(EXAMPLES[0])) == 0


async def test_a_failed_question_does_not_stop_the_others():
    cache, service = AnswerCache(clock=Clock()), Counting(CITED, fail={EXAMPLES[1]})
    report = await warm(service, cache, EXAMPLES[:3], log=lambda *_: None)
    assert report.answered == [EXAMPLES[0], EXAMPLES[2]] and list(report.failed) == [EXAMPLES[1]]
    assert await cache.lookup(page_key(EXAMPLES[1])) is None


def test_cli_warm_command(monkeypatch):
    import rag.cli as cli
    seen = {}

    async def fake(args, settings):
        seen.update(force=args.force, fresh=args.fresh_hours)
        return 0

    monkeypatch.setattr(cli, "run_warm", fake)
    assert cli.main(["warm", "--force"]) == 0 and seen == {"force": True, "fresh": 20}
    assert cli.main(["warm", "--fresh-hours", "6"]) == 0 and seen == {"force": False, "fresh": 6}
