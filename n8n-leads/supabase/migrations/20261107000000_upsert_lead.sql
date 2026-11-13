-- Workflow A saves every lead through upsert_lead(): one call that dedupes by phone, then
-- email, updates or inserts, and writes the audit events, all in one transaction.
-- Two submissions racing for the same phone or email cannot create two leads: the loser
-- hits the unique index and retries as an update.

alter table lead_events drop constraint if exists lead_events_event_check;
alter table lead_events add constraint lead_events_event_check check (event in (
  'created', 'updated', 'classified', 'classification_failed',
  'alerted', 'alert_failed', 'auto_replied', 'followed_up'));

-- lead: {name, phone, email, source, message, meta, language,
--        classification: {status: classified|failed, intent, urgency, language, summary, provider, model, error}}
-- phone must already be E.164 and email lowercase (Workflow A normalizes both).
-- Returns {action: created|updated, lead: <row>}.
create or replace function upsert_lead(lead jsonb) returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_phone   text := nullif(lead->>'phone', '');
  v_email   text := nullif(lead->>'email', '');
  c         jsonb := lead->'classification';
  ok        boolean := coalesce(lead->'classification'->>'status', '') = 'classified';
  v_id      uuid;
  saved     leads;
  v_action  text;
begin
  if v_phone is null and v_email is null then
    raise exception 'phone or email is required' using errcode = '22023';
  end if;

  for attempt in 1..2 loop
    begin
      -- A phone match wins over an email match when they point at different leads.
      select id into v_id from leads
       where (v_phone is not null and phone = v_phone) or (v_email is not null and email = v_email)
       order by coalesce(phone = v_phone, false) desc, created_at
       limit 1
       for update;

      if v_id is not null then
        update leads l set
          name     = coalesce(nullif(lead->>'name', ''), l.name),
          -- Fill a missing phone or email, unless another lead already owns it.
          phone    = case when l.phone is null and v_phone is not null
                           and not exists (select 1 from leads o where o.phone = v_phone) then v_phone else l.phone end,
          email    = case when l.email is null and v_email is not null
                           and not exists (select 1 from leads o where o.email = v_email) then v_email else l.email end,
          message  = coalesce(nullif(lead->>'message', ''), l.message),
          meta     = l.meta || coalesce(lead->'meta', '{}'::jsonb),
          -- The newest message decides the classification. A failed classification keeps the old one.
          intent   = case when ok then c->>'intent' else l.intent end,
          urgency  = case when ok then c->>'urgency' else l.urgency end,
          summary  = case when ok then c->>'summary' else l.summary end,
          language = coalesce(case when ok then c->>'language' end, nullif(lead->>'language', ''), l.language)
        where l.id = v_id
        returning * into saved;
        v_action := 'updated';
      else
        insert into leads (name, phone, email, source, message, meta, intent, urgency, language, summary)
        values (
          nullif(lead->>'name', ''), v_phone, v_email, coalesce(nullif(lead->>'source', ''), 'web_form'),
          nullif(lead->>'message', ''), coalesce(lead->'meta', '{}'::jsonb),
          case when ok then c->>'intent' else 'unclassified' end,
          case when ok then c->>'urgency' end,
          coalesce(case when ok then c->>'language' end, nullif(lead->>'language', '')),
          case when ok then c->>'summary' end)
        returning * into saved;
        v_action := 'created';
      end if;
      exit;
    exception when unique_violation then
      if attempt = 2 then raise; end if;
      v_id := null;  -- another submission inserted this phone or email first: update it instead
    end;
  end loop;

  insert into lead_events (lead_id, event, payload)
  values (saved.id, v_action, jsonb_build_object('source', saved.source, 'message', lead->>'message'));

  if c is not null then
    insert into lead_events (lead_id, event, payload)
    values (saved.id, case when ok then 'classified' else 'classification_failed' end, c);
  end if;

  return jsonb_build_object('action', v_action, 'lead', to_jsonb(saved));
end;
$$;

-- Only the backend (service role) may call it; PostgREST would otherwise expose it to anon.
revoke all on function upsert_lead(jsonb) from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on function upsert_lead(jsonb) from anon, authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function upsert_lead(jsonb) to service_role';
  end if;
end;
$$;
