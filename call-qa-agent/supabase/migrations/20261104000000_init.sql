-- Call QA Agent: initial schema.
-- Apply with the Supabase CLI (`supabase db push`) or paste into the SQL editor.

create extension if not exists pgcrypto;

-- Keeps updated_at current on any table that has the column.
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- One row per uploaded file or URL.
create table calls (
  id            uuid primary key default gen_random_uuid(),
  filename      text not null,
  source_url    text,
  duration_sec  numeric(8, 2) check (duration_sec >= 0),
  language      text,                       -- detected or chosen, e.g. 'en', 'ar', 'multi'
  status        text not null default 'queued'
                check (status in ('queued', 'transcribing', 'scoring', 'done', 'failed')),
  error         text,                       -- set when status = 'failed'
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index calls_created_at_idx on calls (created_at desc);
create index calls_status_idx on calls (status) where status not in ('done', 'failed');

create trigger calls_set_updated_at
  before update on calls
  for each row execute function set_updated_at();

-- Diarized transcript. utterances: [{speaker, start, end, text}], times in seconds.
create table transcripts (
  call_id     uuid primary key references calls (id) on delete cascade,
  utterances  jsonb not null default '[]'::jsonb
              check (jsonb_typeof(utterances) = 'array'),
  provider    text not null default 'deepgram',
  created_at  timestamptz not null default now()
);

-- Rubrics are loaded from rubrics/*.yaml. A changed rubric gets a new version
-- so old scores keep pointing at the rubric they were scored against.
create table rubrics (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null,                -- matches the YAML file id
  name        text not null,
  version     integer not null default 1 check (version > 0),
  items       jsonb not null check (jsonb_typeof(items) = 'array'),
  created_at  timestamptz not null default now(),
  unique (slug, version)
);

-- One row per scoring run. A call can be re-scored with another model or rubric version.
-- items: [{item_id, verdict, evidence_quote, start_time, reasoning}]
-- verdict is one of pass, fail, na, needs_review (quote not found in transcript).
create table scores (
  id             uuid primary key default gen_random_uuid(),
  call_id        uuid not null references calls (id) on delete cascade,
  rubric_id      uuid not null references rubrics (id),
  overall_score  numeric(5, 2) check (overall_score between 0 and 100),
  passed         boolean,                   -- threshold met and no critical item failed
  items          jsonb not null check (jsonb_typeof(items) = 'array'),
  model          text not null,
  input_tokens   integer check (input_tokens >= 0),
  output_tokens  integer check (output_tokens >= 0),
  latency_ms     integer check (latency_ms >= 0),
  cost_usd       numeric(10, 6) check (cost_usd >= 0),
  created_at     timestamptz not null default now()
);

create index scores_call_id_idx on scores (call_id, created_at desc);
create index scores_rubric_id_idx on scores (rubric_id);

-- Latest score per call, for the dashboard list.
create view call_summaries
with (security_invoker = true) as
select
  c.id,
  c.filename,
  c.duration_sec,
  c.language,
  c.status,
  c.created_at,
  s.overall_score,
  s.passed,
  r.name as rubric_name,
  r.version as rubric_version
from calls c
left join lateral (
  select * from scores s where s.call_id = c.id order by s.created_at desc limit 1
) s on true
left join rubrics r on r.id = s.rubric_id;

-- The backend uses the service role key, which bypasses RLS. With RLS on and
-- no policies, the public anon key cannot read or write any of these tables.
alter table calls       enable row level security;
alter table transcripts enable row level security;
alter table rubrics     enable row level security;
alter table scores      enable row level security;
