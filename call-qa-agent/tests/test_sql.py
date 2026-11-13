"""The migrations on a real Postgres 17: the login limit functions, their grants, and that every
migration marked safe to run twice can be. Uses TEST_DATABASE_URL (an empty database, as CI provides) or starts a
throwaway container with Docker; skipped when neither is available."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "supabase" / "migrations").glob("*.sql"))
CONTAINER, PORT = "call-qa-test-db", 55441
ROLES = ("anon", "authenticated", "service_role")  # created by Supabase; made here to test the grants


@pytest.fixture(scope="module")
def dsn():
    url = os.environ.get("TEST_DATABASE_URL")
    started = False
    if not url:
        if not shutil.which("docker"):
            pytest.skip("set TEST_DATABASE_URL or install Docker")
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        run = subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "-e", "POSTGRES_PASSWORD=test",
                              "-p", f"{PORT}:5432", "mirror.gcr.io/library/postgres:17-alpine"], capture_output=True, text=True)
        if run.returncode != 0:
            pytest.skip(f"could not start Postgres: {run.stderr.strip()[:200]}")
        started, url = True, f"postgresql://postgres:test@localhost:{PORT}/postgres"
        for _ in range(60):
            try:
                psycopg.connect(url, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                time.sleep(1)
    with psycopg.connect(url, autocommit=True) as conn:
        for role in ROLES:  # as on Supabase, the service role bypasses row level security
            extra = " bypassrls" if role == "service_role" else ""
            conn.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') "
                         f"then create role {role} nologin{extra}; end if; end $$")
        conn.execute("grant usage on schema public to anon, authenticated, service_role")
        for path in MIGRATIONS:
            conn.execute(path.read_text(encoding="utf-8"))
        for path in MIGRATIONS:  # the ones that say so must be safe to run twice
            if "Safe to run twice" in path.read_text(encoding="utf-8"):
                conn.execute(path.read_text(encoding="utf-8"))
    yield url
    if started:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


@pytest.fixture
def db(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("truncate login_failures")
        yield conn


def call(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0] if "select" in sql else conn.execute(sql, args)


def backdate(conn, client, seconds_ago, n=1):
    for _ in range(n):
        conn.execute("insert into login_failures (client, failed_at) values (%s, now() - make_interval(secs => %s))",
                     (client, seconds_ago))


def test_per_client_limit_and_retry_after(db):
    for _ in range(4):
        db.execute("select login_fail('1.1.1.1')")
    assert call(db, "select login_wait('1.1.1.1')") == 0
    db.execute("select login_fail('1.1.1.1')")
    wait = call(db, "select login_wait('1.1.1.1')")
    assert 899 <= wait <= 901
    assert call(db, "select login_wait('2.2.2.2')") == 0


def test_retry_after_counts_from_the_oldest_failure_in_the_window(db):
    backdate(db, "c", 600, n=5)  # five failures 10 minutes ago: free again in about 5 minutes
    assert 299 <= call(db, "select login_wait('c')") <= 301
    db.execute("truncate login_failures")
    backdate(db, "c", 901, n=5)  # outside the 15-minute window
    assert call(db, "select login_wait('c')") == 0


def test_overall_limit_holds_when_addresses_rotate(db):
    for i in range(50):
        backdate(db, f"10.0.0.{i}", 60)
    assert call(db, "select login_wait('fresh-client')") > 0
    assert call(db, "select login_wait('fresh-client', 5, 51, 900)") == 0


def test_reset_and_cleanup(db):
    backdate(db, "a", 10, n=5)
    backdate(db, "old", 2 * 86400)
    db.execute("select login_reset('a')")
    assert call(db, "select login_wait('a')") == 0
    db.execute("select login_fail('b')")  # also removes rows older than a day
    assert call(db, "select count(*) from login_failures where client = 'old'") == 0


def test_only_the_service_role_may_call_the_functions(db):
    with db.transaction():
        db.execute("set local role service_role")
        db.execute("select login_fail('svc')")
        assert call(db, "select login_wait('svc')") == 0
        db.execute("select login_reset('svc')")
    for role in ("anon", "authenticated"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.transaction():
                db.execute(f"set local role {role}")
                db.execute("select login_fail('x')")


def test_calls_keep_the_storage_path(db):
    row = db.execute("insert into calls (filename, storage_path) values ('a.mp3', 'abc/a.mp3') returning storage_path").fetchone()
    assert row[0] == "abc/a.mp3"
    db.execute("delete from calls where filename = 'a.mp3'")
