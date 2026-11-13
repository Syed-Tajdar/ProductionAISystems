"""Demo limits and answer cache in Postgres (the demo_limits migration), shared by every instance."""

import asyncio
from pathlib import Path

import psycopg
import pytest

from rag.answer import Answer
from rag.config import Settings
from rag.demo import LimitsUnavailable, PgAnswerCache, PgLimiter
from rag.service import RagService

pytestmark = pytest.mark.anyio
MIGRATION = Path(__file__).resolve().parents[1] / "supabase" / "migrations" / "20261110000000_demo_limits.sql"


@pytest.fixture
async def svc(database_url):
    s = RagService(Settings(_env_file=None, database_url=database_url))
    async with s.connection() as conn:
        await conn.execute("truncate demo_requests, demo_answers")
    yield s
    await s.close()


async def test_per_client_limit(svc):
    limiter = PgLimiter(svc.connection, per_client=3, window=600, daily_cap=150)
    verdicts = [await limiter.take("1.1.1.1") for _ in range(4)]
    assert [v.allowed for v in verdicts] == [True, True, True, False]
    assert verdicts[-1].reason == "client" and 599 <= verdicts[-1].retry_after <= 601
    assert (await limiter.take("2.2.2.2")).allowed
    assert await limiter.left_today() == 146


async def test_window_slides(svc):
    limiter = PgLimiter(svc.connection, per_client=2, window=600, daily_cap=150)
    async with svc.connection() as conn:
        await conn.execute("insert into demo_requests (client, asked_at) values ('c', now() - interval '11 minutes'), "
                           "('c', now() - interval '9 minutes')")
    assert (await limiter.take("c")).allowed  # one of the two is outside the window
    refused = await limiter.take("c")
    assert not refused.allowed and 59 <= refused.retry_after <= 61  # the 9-minute-old one frees up first


async def test_daily_cap_counts_everyone_and_resets_at_utc_midnight(svc):
    limiter = PgLimiter(svc.connection, per_client=5, window=600, daily_cap=3)
    async with svc.connection() as conn:
        await conn.execute("insert into demo_requests (client, asked_at) select 'yesterday', "
                           "date_trunc('day', now() at time zone 'utc') at time zone 'utc' - interval '1 minute' "
                           "from generate_series(1, 10)")
    assert all([(await limiter.take(f"10.0.0.{i}")).allowed for i in range(3)])
    refused = await limiter.take("10.0.0.99")
    assert not refused.allowed and refused.reason == "daily" and 0 < refused.retry_after <= 86401
    assert await limiter.left_today() == 0


async def test_concurrent_instances_never_exceed_the_cap(svc, database_url):
    # Separate services stand in for separate serverless instances, each with its own pool.
    services = [RagService(Settings(_env_file=None, database_url=database_url)) for _ in range(4)]
    limiters = [PgLimiter(s.connection, per_client=100, window=600, daily_cap=7) for s in services]
    verdicts = await asyncio.gather(*[limiters[i % 4].take(f"client-{i}") for i in range(20)])
    for s in services:
        await s.close()
    assert sum(v.allowed for v in verdicts) == 7
    async with svc.connection() as conn:
        assert (await (await conn.execute("select count(*) as n from demo_requests")).fetchone())["n"] == 7


async def test_old_rows_are_removed(svc):
    async with svc.connection() as conn:
        await conn.execute("insert into demo_requests (client, asked_at) values ('old', now() - interval '3 days')")
    await PgLimiter(svc.connection).take("new")
    async with svc.connection() as conn:
        rows = await (await conn.execute("select client from demo_requests")).fetchall()
    assert [r["client"] for r in rows] == ["new"]


async def test_database_down_refuses_instead_of_answering_uncounted():
    s = RagService(Settings(_env_file=None, database_url="postgresql://nobody:x@127.0.0.1:9/none?connect_timeout=1"))
    s.pool = None
    with pytest.raises(LimitsUnavailable):
        await PgLimiter(s.connection).take("c")
    assert await PgAnswerCache(s.connection).lookup("q") is None  # a cache failure is just a miss
    await s.close()


async def test_answer_cache(svc):
    cache = PgAnswerCache(svc.connection, size=2, ttl=3600, decode=lambda d: Answer(**d))
    a = Answer(question="What is the fee?", answerable=True, text="AED 500. [1]", language="en",
               sources=[{"n": 1, "title": "Tenant Handbook", "quotes": ["AED 500"]}])
    await cache.store("|What is the fee?", a)
    hit = await cache.lookup("|What  IS the fee? ")  # same key after case and space folding
    assert isinstance(hit, Answer) and hit.text == "AED 500. [1]" and hit.sources[0]["quotes"] == ["AED 500"]
    arabic = Answer(question="ما الرسوم؟", answerable=False, text="لا تحتوي المستندات على إجابة لهذا السؤال.", language="ar", direction="rtl")
    await cache.store("|ما الرسوم؟", arabic)
    assert (await cache.lookup("|ما الرسوم؟")).text == arabic.text
    await cache.store("|third question", a)  # size 2: the oldest entry is dropped
    assert await cache.lookup("|What is the fee?") is None
    async with svc.connection() as conn:
        await conn.execute("update demo_answers set created_at = now() - interval '2 hours'")
    assert await cache.lookup("|third question") is None  # past the time limit


async def test_public_api_roles_cannot_use_the_limits(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("do $$ begin if not exists (select 1 from pg_roles where rolname = 'anon') then "
                     "create role anon nologin; create role authenticated nologin; end if; end $$")
        conn.execute(MIGRATION.read_text(encoding="utf-8"))  # safe to run twice; now with the roles present
        conn.execute("grant usage on schema public to anon")
        for sql in ("select demo_take('x', 5, 600, 150)", "select demo_left_today(150)", "select * from demo_requests"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute("set local role anon")
                    conn.execute(sql)


async def test_answer_age_for_the_warm_up(svc):
    cache = PgAnswerCache(svc.connection, ttl=3600, decode=lambda d: Answer(**d))
    assert await cache.age("|What is the fee for a returned cheque?") is None
    trace = {"timings_ms": {"total": 900}}
    await cache.store("|What is the fee for a returned cheque?", Answer("q", True, "AED 500 [1]", trace=trace))
    assert 0 <= await cache.age("|what is the fee for a  returned cheque?") < 5  # same key as the page's lookup
    assert (await cache.lookup("|What is the fee for a returned cheque?")).trace == trace  # the trace is kept
    async with svc.connection() as conn:
        await conn.execute("update demo_answers set created_at = now() - interval '2 hours'")
    assert await cache.age("|What is the fee for a returned cheque?") is None  # past the ttl: as good as gone
