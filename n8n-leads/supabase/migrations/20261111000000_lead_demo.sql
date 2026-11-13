-- Public demo of Workflow A (LEADS_DEMO_MODE=true): a schema for n8n's own tables, caps for demo
-- submissions and demo Telegram alerts, and demo leads kept out of follow-up emails. Safe to run twice.

-- n8n keeps its workflows, credentials and executions here (DB_POSTGRESDB_SCHEMA=n8n), apart from the
-- lead tables. Not exposed through the Supabase API.
create schema if not exists n8n;
revoke all on schema n8n from public;

-- One row per demo submission ('lead') or demo alert sent ('alert'), kept for two days.
-- client is a hash of the visitor's address, never the address itself.
create table if not exists lead_demo_requests (
  id      bigint generated always as identity primary key,
  kind    text not null check (kind in ('lead', 'alert')),
  client  text not null default '',
  at      timestamptz not null default now()
);

create index if not exists lead_demo_requests_kind_at_idx on lead_demo_requests (kind, at);
create index if not exists lead_demo_requests_client_idx on lead_demo_requests (client, at) where kind = 'lead';
create index if not exists leads_demo_created_idx on leads (created_at) where meta->>'demo' = 'true';

alter table lead_demo_requests enable row level security;

-- Checks both limits for one demo submission and counts it when allowed:
--   at most p_per_client submissions per visitor within p_window_seconds,
--   at most p_daily_cap submissions in total per UTC day.
-- Also deletes demo leads older than 7 days. Returns {allowed, retry_after (seconds), reason: client|daily}.
create or replace function lead_demo_take(p_client text, p_per_client int, p_window_seconds int, p_daily_cap int)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_client    text := encode(sha256(convert_to('lead-demo:' || coalesce(p_client, ''), 'UTF8')), 'hex');
  v_day_start timestamptz := date_trunc('day', now() at time zone 'utc') at time zone 'utc';
  v_today     int;
  v_mine      int;
  v_first     timestamptz;
begin
  -- One caller at a time, so two submissions cannot both take the last slot of the day.
  perform pg_advisory_xact_lock(hashtext('lead_demo_take'));
  delete from lead_demo_requests where at < now() - interval '2 days';
  delete from leads where meta->>'demo' = 'true' and created_at < now() - interval '7 days';

  select count(*) into v_today from lead_demo_requests where kind = 'lead' and at >= v_day_start;
  if v_today >= p_daily_cap then
    return jsonb_build_object('allowed', false, 'reason', 'daily',
      'retry_after', floor(extract(epoch from v_day_start + interval '1 day' - now()))::int + 1);
  end if;

  select count(*), min(at) into v_mine, v_first
    from lead_demo_requests
   where kind = 'lead' and client = v_client and at > now() - make_interval(secs => p_window_seconds);
  if v_mine >= p_per_client then
    return jsonb_build_object('allowed', false, 'reason', 'client',
      'retry_after', floor(extract(epoch from v_first + make_interval(secs => p_window_seconds) - now()))::int + 1);
  end if;

  insert into lead_demo_requests (kind, client) values ('lead', v_client);
  return jsonb_build_object('allowed', true, 'reason', '', 'retry_after', 0);
end;
$$;

-- Takes one of the p_daily_cap demo Telegram alerts of this UTC day. Returns {allowed: false} when none are left.
create or replace function lead_demo_take_alert(p_daily_cap int)
returns jsonb
language plpgsql
set search_path = public
as $$
begin
  perform pg_advisory_xact_lock(hashtext('lead_demo_take_alert'));
  if (select count(*) from lead_demo_requests
       where kind = 'alert' and at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc') >= p_daily_cap then
    return jsonb_build_object('allowed', false);
  end if;
  insert into lead_demo_requests (kind) values ('alert');
  return jsonb_build_object('allowed', true);
end;
$$;

-- Demo leads never get a follow-up email: the address was typed into a public form.
create or replace function claim_follow_ups(max_leads int default 20, min_age interval default interval '24 hours')
returns setof leads
language sql
set search_path = public
as $$
  with picked as (
    select l.id from leads l
     where l.status = 'new'
       and l.email is not null
       and l.intent <> 'spam'
       and coalesce(l.meta->>'demo', '') <> 'true'
       and l.created_at < now() - min_age
       and (select count(*) from lead_events e where e.lead_id = l.id and e.event = 'follow_up_failed') < 3
     order by l.created_at
     limit max_leads
     for update skip locked
  )
  update leads l set status = 'followed_up', followed_up_at = now()
    from picked where l.id = picked.id
  returning l.*;
$$;

-- Demo leads are counted as new leads but never wait for a follow-up.
create or replace view lead_daily_summary
with (security_invoker = true) as
select
  count(*) filter (where created_at >= now() - interval '24 hours')                        as new_leads,
  count(*) filter (where created_at >= now() - interval '24 hours' and urgency = 'hot')    as hot_leads,
  count(*) filter (where status = 'won' and updated_at >= now() - interval '24 hours')     as won,
  count(*) filter (where status = 'new' and created_at < now() - interval '24 hours'
                     and coalesce(meta->>'demo', '') <> 'true')                             as awaiting_follow_up,
  count(*) filter (where followed_up_at >= now() - interval '24 hours')                    as followed_up
from leads;

-- Service role only: the public API roles could otherwise use up the daily caps.
revoke all on function lead_demo_take(text, int, int, int) from public;
revoke all on function lead_demo_take_alert(int) from public;
revoke all on function claim_follow_ups(int, interval) from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on schema n8n from anon, authenticated;
    revoke all on table lead_demo_requests from anon, authenticated;
    revoke all on function lead_demo_take(text, int, int, int) from anon, authenticated;
    revoke all on function lead_demo_take_alert(int) from anon, authenticated;
    revoke all on function claim_follow_ups(int, interval) from anon, authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function lead_demo_take(text, int, int, int) to service_role;
    grant execute on function lead_demo_take_alert(int) to service_role;
    grant execute on function claim_follow_ups(int, interval) to service_role;
    grant select on lead_daily_summary to service_role;
  end if;
end;
$$;
