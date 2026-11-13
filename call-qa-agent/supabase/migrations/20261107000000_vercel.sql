-- Serverless hosting (Vercel): audio goes straight from the browser to Supabase Storage, and the
-- login rate limit lives in Postgres so it holds across function instances. Safe to run twice.

-- Calls uploaded to Storage keep the object path; the signed URL given to Deepgram is never stored.
alter table calls add column if not exists storage_path text;

-- Failed login attempts (wrong passcode in the form or the X-Passcode header).
create table if not exists login_failures (
  id         bigint generated always as identity primary key,
  client     text not null,
  failed_at  timestamptz not null default now()
);

create index if not exists login_failures_client_idx on login_failures (client, failed_at);
create index if not exists login_failures_failed_at_idx on login_failures (failed_at);

alter table login_failures enable row level security;

-- Seconds until the client may try again, 0 if allowed: at most p_per_client failures per client and
-- p_overall failures in total within the window, so rotating addresses does not get around it.
create or replace function login_wait(p_client text, p_per_client integer default 5, p_overall integer default 50,
                                      p_window_seconds integer default 900)
returns integer
language sql stable
set search_path = public
as $$
  with since as (select now() - make_interval(secs => p_window_seconds) as t),
  mine as (select count(*) as n, min(failed_at) as first from login_failures, since where client = p_client and failed_at > since.t),
  everyone as (select count(*) as n, min(failed_at) as first from login_failures, since where failed_at > since.t)
  select greatest(
    case when mine.n >= p_per_client
         then floor(extract(epoch from mine.first + make_interval(secs => p_window_seconds) - now()))::integer + 1 else 0 end,
    case when everyone.n >= p_overall
         then floor(extract(epoch from everyone.first + make_interval(secs => p_window_seconds) - now()))::integer + 1 else 0 end)
  from mine, everyone;
$$;

-- Records a failure and drops rows older than a day, so the table stays small.
create or replace function login_fail(p_client text)
returns void
language sql
set search_path = public
as $$
  delete from login_failures where failed_at < now() - interval '1 day';
  insert into login_failures (client) values (p_client);
$$;

-- A successful login clears that client's failures.
create or replace function login_reset(p_client text)
returns void
language sql
set search_path = public
as $$
  delete from login_failures where client = p_client;
$$;

-- Only the backend (service role) may use these; Supabase grants execute to anon by default.
revoke all on function login_wait(text, integer, integer, integer) from public;
revoke all on function login_fail(text) from public;
revoke all on function login_reset(text) from public;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on table login_failures from anon, authenticated;
    revoke all on function login_wait(text, integer, integer, integer) from anon, authenticated;
    revoke all on function login_fail(text) from anon, authenticated;
    revoke all on function login_reset(text) from anon, authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant select, insert, delete on table login_failures to service_role;
    grant execute on function login_wait(text, integer, integer, integer) to service_role;
    grant execute on function login_fail(text) to service_role;
    grant execute on function login_reset(text) to service_role;
  end if;
end $$;

-- Private bucket for uploaded call audio: 50 MB per file (the Supabase free plan limit), audio only.
-- No storage policies: objects are written and read only through signed URLs made with the secret key.
do $$
begin
  if to_regclass('storage.buckets') is not null then
    insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
    values ('call-audio', 'call-audio', false, 52428800, array['audio/*'])
    on conflict (id) do nothing;
  end if;
end $$;
