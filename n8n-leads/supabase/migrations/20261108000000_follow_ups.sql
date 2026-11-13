-- Workflow A auto-replies and Workflow B follow-ups.
-- Cloud Scheduler can deliver a job twice and two runs can overlap, so follow-ups are claimed
-- first (status set to followed_up under a row lock) and only then emailed. A failed email
-- puts the lead back to new for the next run; after 3 failures it is no longer picked.

alter table lead_events drop constraint if exists lead_events_event_check;
alter table lead_events add constraint lead_events_event_check check (event in (
  'created', 'updated', 'classified', 'classification_failed',
  'alerted', 'alert_failed', 'auto_replied', 'auto_reply_failed',
  'followed_up', 'follow_up_failed'));

create index if not exists lead_events_follow_up_failed_idx on lead_events (lead_id) where event = 'follow_up_failed';

-- Returns the claimed leads: status new, older than min_age, with an email, not spam,
-- fewer than 3 failed follow-ups. Rows locked by a concurrent run are skipped.
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

-- results: [{lead_id, ok, error?, message_id?}]. Logs followed_up or follow_up_failed and
-- returns failed leads to status new. Returns {followed_up, failed}.
create or replace function finish_follow_ups(results jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  ok_count int;
  failed_count int;
begin
  insert into lead_events (lead_id, event, payload)
  select (r->>'lead_id')::uuid,
         case when (r->>'ok')::boolean then 'followed_up' else 'follow_up_failed' end,
         jsonb_strip_nulls(jsonb_build_object('channel', 'email', 'error', r->>'error', 'message_id', r->>'message_id'))
    from jsonb_array_elements(results) r;

  update leads set status = 'new', followed_up_at = null
   where status = 'followed_up'
     and id in (select (r->>'lead_id')::uuid from jsonb_array_elements(results) r where not (r->>'ok')::boolean);

  select count(*) filter (where (r->>'ok')::boolean), count(*) filter (where not (r->>'ok')::boolean)
    into ok_count, failed_count
    from jsonb_array_elements(results) r;
  return jsonb_build_object('followed_up', ok_count, 'failed', failed_count);
end;
$$;

-- Daily summary numbers, last 24 hours, now with follow-ups sent.
create or replace view lead_daily_summary
with (security_invoker = true) as
select
  count(*) filter (where created_at >= now() - interval '24 hours')                        as new_leads,
  count(*) filter (where created_at >= now() - interval '24 hours' and urgency = 'hot')    as hot_leads,
  count(*) filter (where status = 'won' and updated_at >= now() - interval '24 hours')     as won,
  count(*) filter (where status = 'new' and created_at < now() - interval '24 hours')      as awaiting_follow_up,
  count(*) filter (where followed_up_at >= now() - interval '24 hours')                    as followed_up
from leads;

revoke all on function claim_follow_ups(int, interval) from public;
revoke all on function finish_follow_ups(jsonb) from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on function claim_follow_ups(int, interval) from anon, authenticated';
    execute 'revoke all on function finish_follow_ups(jsonb) from anon, authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function claim_follow_ups(int, interval) to service_role';
    execute 'grant execute on function finish_follow_ups(jsonb) to service_role';
    execute 'grant select on lead_daily_summary to service_role';
  end if;
end;
$$;
