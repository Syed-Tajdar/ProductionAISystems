import hashlib
import math
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from rag.config import Settings, get_settings

Settings.model_config["env_file"] = None  # the tests never read a local .env

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
CONTAINER = "rag-mcp-test-db"
PORT = 55432


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Tests never read keys or settings from the shell."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()


class FakeEmbedder:
    """Deterministic: hashed bag of words, so texts sharing words are close. No network."""

    provider, model, dim = "fake", "bag-of-words", 768

    def __init__(self):
        self.calls: list[tuple[int, str]] = []

    async def embed(self, texts, task, titles=None):
        self.calls.append((len(texts), task))
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"\w+", t.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 12) % 2 else -1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


def _docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture(scope="session")
def database_url():
    """A throwaway pgvector Postgres with the migrations applied. TEST_DATABASE_URL points at an empty
    database to use instead (CI runs one as a service container); otherwise a Docker container is started."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        import psycopg

        with psycopg.connect(url, autocommit=True) as c:
            for path in MIGRATIONS:
                c.execute(path.read_text(encoding="utf-8"))
        yield url
        return
    _docker("rm", "-f", CONTAINER, check=False)
    _docker("run", "-d", "--name", CONTAINER, "-e", "POSTGRES_PASSWORD=test", "-p", f"{PORT}:5432",
            "mirror.gcr.io/pgvector/pgvector:pg17")
    for _ in range(60):
        if _docker("exec", CONTAINER, "pg_isready", "-U", "postgres", check=False).returncode == 0:
            break
        time.sleep(1)
    time.sleep(1)
    for path in MIGRATIONS:
        subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", "-q"],
                       input=path.read_text(encoding="utf-8"), text=True, check=True, capture_output=True)
    yield f"postgresql://postgres:test@localhost:{PORT}/postgres"
    _docker("rm", "-f", CONTAINER, check=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def conn(database_url):
    from rag.db import connect

    c = await connect(database_url)
    await c.execute("truncate documents, chunks, embedding_cache cascade")
    yield c
    await c.close()
