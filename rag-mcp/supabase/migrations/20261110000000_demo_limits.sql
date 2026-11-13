-- Public demo limits and answer cache in Postgres, so they hold across serverless instances
-- (one Vercel Function can run many instances). Used by the web page, /ask, /search and the public
-- MCP endpoint alike. Safe to run twice.

-- One row per counted question, kept for two days.
create table if not exists demo_requests (
  id         bigint generated always as identity primary key,
  client     text not null,
  asked_at   timestamptz not null default now()
);

create index if not exists demo_requests_client_idx on demo_requests (client, asked_at);
create index if not exists demo_requests_asked_at_idx on demo_requests (asked_at);

-- Answers to repeated questions, served without counting against the limits.
create table if not exists demo_answers (
  key         text primary key,
  answer      jsonb not null,
  created_at  timestamptz not null default now()
);

create index if not exists demo_answers_created_at_idx on demo_answers (created_at);

alter table demo_requests enable row level security;
alter table demo_answers  enable row level security;

-- Checks both limits and, when the question is allowed, counts it, in one step:
--   at most p_per_client questions per client within p_window_seconds,
--   at most p_daily_cap questions in total per UTC day.
-- retry_after is in seconds; reason is 'client' or 'daily' when refused; left_today is after this question.
create or replace function demo_take(p_client text, p_per_client integer, p_window_seconds integer, p_daily_cap integer)
returns table (allowed boolean, retry_after integer, reason text, left_today integer)
language plpgsql
set search_path = public
as $$
declare
  v_day_start timestamptz := date_trunc('day', now() at time zone 'utc') at time zone 'utc';
  v_today     integer;
  v_mine      integer;
  v_first     timestamptz;
begin
  -- One caller at a time (for this transaction only, so it works through a transaction pooler):
  -- two instances cannot both take the last question of the day.
  perform pg_advisory_xact_lock(hashtext('demo_take'));
  delete from demo_requests where asked_at < now() - interval '2 days';

  select count(*) into v_today from demo_requests where asked_at >= v_day_start;
  if v_today >= p_daily_cap then
    return query select false, floor(extract(epoch from v_day_start + interval '1 day' - now()))::integer + 1, 'daily'::text, 0;
    return;
  end if;

  select count(*), min(asked_at) into v_mine, v_first
    from demo_requests
   where client = p_client and asked_at > now() - make_interval(secs => p_window_seconds);
  if v_mine >= p_per_client then
    return query select false, floor(extract(epoch from v_first + make_interval(secs => p_window_seconds) - now()))::integer + 1,
                        'client'::text, p_daily_cap - v_today;
    return;
  end if;

  insert into demo_requests (client) values (p_client);
  return query select true, 0, ''::text, p_daily_cap - v_today - 1;
end;
$$;

create or replace function demo_left_today(p_daily_cap integer)
returns integer
language sql stable
set search_path = public
as $$
  select greatest(p_daily_cap - count(*)::integer, 0)
    from demo_requests
   where asked_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc';
$$;

-- The backend connects as the database owner. The public API roles get nothing: Supabase grants
-- execute on new functions to them by default, and demo_take would let anyone use up the daily cap.
revoke all on function demo_take(text, integer, integer, integer) from public;
revoke all on function demo_left_today(integer) from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on table demo_requests, demo_answers from anon, authenticated;
    revoke all on function demo_take(text, integer, integer, integer) from anon, authenticated;
    revoke all on function demo_left_today(integer) from anon, authenticated;
  end if;
end $$;
