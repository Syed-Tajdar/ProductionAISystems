"""Applies the Supabase migrations of call-qa-agent, n8n-leads and rag-mcp to one database with psql.

    DATABASE_URL=postgresql://... python .github/scripts/apply_migrations.py

- Order: by file name (the timestamp prefix), across the three projects.
- Each applied file is recorded in ops.applied_migrations (name, checksum), so reruns skip it. A file
  and its record are written in one transaction: either both happen or neither.
- A migration not recorded yet whose plain `create table` tables all exist already (applied by hand)
  is recorded as "baseline" instead of run. If only some of them exist, it stops and says which.
- Then makes sure the private call-audio Storage bucket exists.

Standard library only. Never prints the connection string; psql errors are scrubbed of it.
Writes counts to $GITHUB_OUTPUT when that is set.
"""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ["call-qa-agent", "n8n-leads", "rag-mcp"]
NAME = re.compile(r"^[a-z0-9-]+/[0-9]{14}_[a-z0-9_]+\.sql$")
# Tables created without "if not exists": the statements that fail when the migration ran before.
CREATE_TABLE = re.compile(r"^\s*create\s+table\s+(?!if\s+not\s+exists\b)([a-z_][a-z0-9_.]*)", re.I | re.M)
URI = re.compile(r"postgres(?:ql)?://\S+")

BOOTSTRAP = """
create schema if not exists ops;
create table if not exists ops.applied_migrations (
  name        text primary key,
  checksum    text not null,
  how         text not null check (how in ('applied', 'baseline')),
  applied_at  timestamptz not null default now()
);
-- Not part of the public API: the anon and authenticated roles get nothing here.
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on schema ops from anon, authenticated;
    revoke all on all tables in schema ops from anon, authenticated;
  end if;
end $$;
"""

BUCKET = """
do $$ begin
  if to_regclass('storage.buckets') is null then
    raise notice 'bucket: no storage schema';
  elsif exists (select 1 from storage.buckets where id = 'call-audio') then
    raise notice 'bucket: existed';
  else
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('call-audio', 'call-audio', false, 52428800, array['audio/*']);
    raise notice 'bucket: created';
  end if;
end $$;
"""


class MigrationError(RuntimeError):
    pass


def scrub(text: str, url: str) -> str:
    return URI.sub("<connection string>", text.replace(url, "<connection string>")).strip()


def psql(url: str, *args: str, sql: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", url, *args]
    run = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if run.returncode != 0:
        raise MigrationError(scrub(run.stderr or run.stdout, url))
    return run


def query(url: str, sql: str) -> list[list[str]]:
    out = psql(url, "-tA", "-F", "\t", "-c", sql).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def migrations() -> list[tuple[str, Path]]:
    found = [(f"{p}/{f.name}", f) for p in PROJECTS for f in (ROOT / p / "supabase" / "migrations").glob("*.sql")]
    for name, _ in found:
        if not NAME.match(name):
            raise MigrationError(f"unexpected migration file name: {name}")
    return sorted(found, key=lambda item: (item[1].name, item[0]))


def existing_tables(url: str, tables: list[str]) -> set[str]:
    names = ",".join("'" + t.replace("'", "") + "'" for t in tables)
    rows = query(url, f"select t from unnest(array[{names}]) as t where to_regclass(t) is not null")
    return {r[0] for r in rows}


def apply(url: str, name: str, path: Path, checksum: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as record:
        record.write(f"insert into ops.applied_migrations (name, checksum, how) values ('{name}', '{checksum}', 'applied');\n")
    try:
        psql(url, "--single-transaction", "-f", str(path), "-f", record.name)
    finally:
        os.unlink(record.name)


def run(url: str, log=print) -> dict:
    psql(url, sql=BOOTSTRAP)
    recorded = {name: checksum for name, checksum in query(url, "select name, checksum from ops.applied_migrations")}
    counts = {"applied": 0, "baseline": 0, "skipped": 0}
    for name, path in migrations():
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        if name in recorded:
            counts["skipped"] += 1
            if recorded[name] != checksum:
                log(f"::warning::{name} changed after it was applied; it is not run again")
            log(f"skip      {name} (already recorded)")
            continue
        tables = sorted(set(CREATE_TABLE.findall(sql)))
        present = existing_tables(url, tables) if tables else set()
        if tables and present == set(tables):
            psql(url, "-c", f"insert into ops.applied_migrations (name, checksum, how) values ('{name}', '{checksum}', 'baseline')")
            counts["baseline"] += 1
            log(f"baseline  {name} (tables already there: {', '.join(tables)})")
            continue
        if present:
            missing = sorted(set(tables) - present)
            raise MigrationError(f"{name}: some of its tables exist ({', '.join(sorted(present))}) and some do not "
                                 f"({', '.join(missing)}); fix the database by hand, then run again")
        apply(url, name, path, checksum)
        counts["applied"] += 1
        log(f"applied   {name}")
    notice = psql(url, sql=BUCKET).stderr
    counts["bucket"] = (re.search(r"bucket: ([a-z ]+)", notice) or [None, "unknown"])[1].strip()
    log(f"bucket    call-audio {counts['bucket']}")
    return counts


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    try:
        counts = run(url)
    except MigrationError as e:
        print(f"::error::migrations stopped: {e}")
        return 1
    print(f"migrations: {counts['applied']} applied, {counts['baseline']} marked as applied (already there), "
          f"{counts['skipped']} skipped (recorded earlier); call-audio bucket {counts['bucket']}")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.writelines(f"{k}={v}\n" for k, v in counts.items())
    return 0


if __name__ == "__main__":
    sys.exit(main())
