"""Limits for the public demo, so nobody can drain the API keys.

- Per client address: at most `per_client` questions in a sliding window.
- Overall: at most `daily_cap` questions per UTC day, whoever asks.
- Answers to the same question are cached for a day and served without counting.

Two stores with the same async interface (take, left_today; lookup, store):
- Postgres (PgLimiter, PgAnswerCache): shared by every instance, so the limits hold on serverless
  hosts that run many instances. The default whenever a database is configured.
- Memory (DemoLimiter, AnswerCache): per process, for local runs and tests.

The client address is only as trustworthy as the header it comes from (CLIENT_IP_HEADER), so the
daily cap is the limit that actually bounds spending.
"""

import json
import logging
import time
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

log = logging.getLogger(__name__)
# The database is unreachable, refuses the login, or the query fails.
DB_ERRORS = (psycopg.Error, OSError)


@dataclass
class Verdict:
    allowed: bool
    retry_after: int = 0  # seconds
    reason: str = ""      # "client" or "daily"


class DemoLimiter:
    def __init__(self, per_client: int = 5, window: int = 600, daily_cap: int = 150, clock=time.time):
        self.per_client, self.window, self.daily_cap, self.clock = per_client, window, daily_cap, clock
        self.clients: dict[str, deque] = {}
        self.day, self.used_today = None, 0

    def _today(self) -> str:
        day = datetime.fromtimestamp(self.clock(), UTC).date().isoformat()
        if day != self.day:
            self.day, self.used_today = day, 0
        return day

    def remaining_today(self) -> int:
        self._today()
        return max(self.daily_cap - self.used_today, 0)

    def check(self, client: str) -> Verdict:
        """Whether this client may ask now. Does not count the question; call record() for that."""
        self._today()
        if self.used_today >= self.daily_cap:
            now = self.clock()
            midnight = (int(now) // 86400 + 1) * 86400
            return Verdict(False, int(midnight - now) + 1, "daily")
        q = self.clients.get(client)
        if q:
            while q and q[0] <= self.clock() - self.window:
                q.popleft()
            if len(q) >= self.per_client:
                return Verdict(False, int(q[0] + self.window - self.clock()) + 1, "client")
        return Verdict(True)

    def record(self, client: str) -> None:
        self._today()
        self.used_today += 1
        self.clients.setdefault(client, deque()).append(self.clock())
        if len(self.clients) > 10_000:  # forget idle clients so memory stays bounded
            for key in [k for k, q in self.clients.items() if not q or q[-1] <= self.clock() - self.window]:
                del self.clients[key]

    async def take(self, client: str) -> Verdict:
        """Check and, when allowed, count one question."""
        verdict = self.check(client)
        if verdict.allowed:
            self.record(client)
        return verdict

    async def left_today(self) -> int:
        return self.remaining_today()


def cache_key(question: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", question).casefold().split())


# The page's example questions; `python -m rag.cli warm` answers them ahead of visitors (rag.warm).
EXAMPLES = [
    "What is the fee for a returned cheque?",
    "How much notice do I need to give before moving out?",
    "ما مواعيد عمل المسبح؟",
    "كم يوماً من الإجازة السنوية يستحق الموظف؟",
    "Do you offer car rentals for tenants?",
]


class AnswerCache:
    """Least recently used, with a time limit, for answers to repeated questions (the examples)."""

    def __init__(self, size: int = 200, ttl: int = 86400, clock=time.time):
        self.size, self.ttl, self.clock = size, ttl, clock
        self.items: OrderedDict[str, tuple[float, object]] = OrderedDict()

    def get(self, question: str):
        key = cache_key(question)
        hit = self.items.get(key)
        if hit is None or hit[0] < self.clock() - self.ttl:
            self.items.pop(key, None)
            return None
        self.items.move_to_end(key)
        return hit[1]

    def put(self, question: str, value) -> None:
        self.items[cache_key(question)] = (self.clock(), value)
        self.items.move_to_end(cache_key(question))
        while len(self.items) > self.size:
            self.items.popitem(last=False)

    async def lookup(self, question: str):
        return self.get(question)

    async def store(self, question: str, value) -> None:
        self.put(question, value)

    async def age(self, question: str) -> float | None:
        """Seconds since the cached answer was stored, or None when there is none (or it expired)."""
        hit = self.items.get(cache_key(question))
        return None if hit is None or hit[0] < self.clock() - self.ttl else self.clock() - hit[0]


class LimitsUnavailable(RuntimeError):
    """The shared limits could not be checked; the question is refused rather than answered uncounted."""


class PgLimiter:
    """The same limits in Postgres (demo_take in the demo_limits migration): one atomic call checks both
    limits and counts the question. `connection` is an async context manager factory (RagService.connection)."""

    def __init__(self, connection, per_client: int = 5, window: int = 600, daily_cap: int = 150):
        self.connection, self.per_client, self.window, self.daily_cap = connection, per_client, window, daily_cap

    async def take(self, client: str) -> Verdict:
        try:
            async with self.connection() as conn:
                cur = await conn.execute("select * from demo_take(%s, %s, %s, %s)",
                                         (client[:64], self.per_client, self.window, self.daily_cap))
                row = await cur.fetchone()
        except DB_ERRORS as e:
            log.warning("demo limits unavailable: %s", type(e).__name__)
            raise LimitsUnavailable("the demo limits cannot be checked right now") from e
        return Verdict(row["allowed"], row["retry_after"], row["reason"])

    async def left_today(self) -> int:
        try:
            async with self.connection() as conn:
                cur = await conn.execute("select demo_left_today(%s) as n", (self.daily_cap,))
                return (await cur.fetchone())["n"]
        except DB_ERRORS:
            return 0


class PgAnswerCache:
    """Answers to repeated questions in the demo_answers table, for `ttl` seconds, at most `size` rows
    (oldest dropped first). Failures count as a miss: the cache never blocks an answer."""

    def __init__(self, connection, size: int = 200, ttl: int = 86400, decode=lambda data: data):
        self.connection, self.size, self.ttl, self.decode = connection, size, ttl, decode

    async def lookup(self, question: str):
        try:
            async with self.connection() as conn:
                cur = await conn.execute("select answer from demo_answers where key = %s "
                                         "and created_at > now() - make_interval(secs => %s)", (cache_key(question), self.ttl))
                row = await cur.fetchone()
        except DB_ERRORS as e:
            log.warning("answer cache unavailable: %s", type(e).__name__)
            return None
        return self.decode(row["answer"]) if row else None

    async def age(self, question: str) -> float | None:
        """Seconds since the cached answer was stored, or None when there is none (or it expired)."""
        async with self.connection() as conn:
            cur = await conn.execute("select extract(epoch from now() - created_at)::float as age from demo_answers "
                                     "where key = %s and created_at > now() - make_interval(secs => %s)",
                                     (cache_key(question), self.ttl))
            row = await cur.fetchone()
        return row["age"] if row else None

    async def store(self, question: str, value) -> None:
        data = value.to_dict(trace=True) if hasattr(value, "to_dict") else value
        try:
            async with self.connection() as conn:
                await conn.execute("insert into demo_answers (key, answer) values (%s, %s::jsonb) "
                                   "on conflict (key) do update set answer = excluded.answer, created_at = now()",
                                   (cache_key(question), json.dumps(data, ensure_ascii=False)))
                await conn.execute("delete from demo_answers where created_at < now() - make_interval(secs => %s) "
                                   "or key in (select key from demo_answers order by created_at desc offset %s)",
                                   (self.ttl, self.size))
        except DB_ERRORS as e:
            log.warning("answer cache unavailable: %s", type(e).__name__)


def client_address(headers: dict[str, str], peer: str | None, header: str = "") -> str:
    """The client address: the first entry of the proxy's header when one is configured (Vercel sets
    x-real-ip and overwrites x-forwarded-for), otherwise the socket peer."""
    if header and (value := headers.get(header.lower())):
        return value.split(",")[0].strip()[:64]
    return peer or "unknown"


def message(v: Verdict) -> str:
    if v.reason == "daily":
        hours = max(1, round(v.retry_after / 3600))
        return f"The demo has reached its question limit for today. Try again in about {hours} hour{'s' if hours > 1 else ''}."
    minutes = max(1, round(v.retry_after / 60))
    return f"Too many questions from your address. Try again in about {minutes} minute{'s' if minutes > 1 else ''}."
