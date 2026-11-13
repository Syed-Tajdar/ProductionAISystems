-- n8n lead automation: leads and lead_events.
-- Apply with the Supabase CLI (`supabase db push`) or paste into the SQL editor.
-- n8n connects with the service role key, which bypasses RLS.

create extension if not exists pgcrypto;

create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table leads (
  id              uuid primary key default gen_random_uuid(),
  name            text,
  phone           text check (phone ~ '^\+[1-9][0-9]{6,14}$'),   -- E.164, normalized in n8n
  email           text check (email = lower(email) and email like '%_@_%'),
  source          text not null default 'web_form'
                  check (source in ('web_form', 'email', 'whatsapp', 'other')),
  message         text,
  -- Filled by the LLM step. 'unclassified' is the fallback when the LLM fails.
  intent          text not null default 'unclassified'
                  check (intent in ('buy', 'rent', 'info', 'spam', 'unclassified')),
  urgency         text check (urgency in ('hot', 'warm', 'cold')),
  language        text check (language in ('en', 'ar')),
  summary         text,
  status          text not null default 'new'
                  check (status in ('new', 'followed_up', 'contacted', 'won', 'lost')),
  meta            jsonb not null default '{}'::jsonb,              -- page URL, UTM tags
  followed_up_at  timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint leads_phone_or_email check (phone is not null or email is not null)
);

-- Dedupe keys: n8n looks up by phone or email and updates instead of inserting.
-- The unique indexes also stop duplicates if two submissions race.
create unique index leads_phone_key on leads (phone) where phone is not null;
create unique index leads_email_key on leads (email) where email is not null;

-- Workflow B: status = 'new' and older than 24 hours.
create index leads_new_created_idx on leads (created_at) where status = 'new';
create index leads_created_at_idx on leads (created_at desc);

create trigger leads_set_updated_at
  before update on leads
  for each row execute function set_updated_at();

-- Audit trail of everything the workflows did to a lead.
create table lead_events (
  id          bigserial primary key,
  lead_id     uuid not null references leads (id) on delete cascade,
  event       text not null
              check (event in ('created', 'updated', 'classified', 'alerted', 'auto_replied', 'followed_up')),
  payload     jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);

create index lead_events_lead_id_idx on lead_events (lead_id, created_at desc);

-- Numbers for the daily Telegram summary (Workflow B), last 24 hours.
create view lead_daily_summary
with (security_invoker = true) as
select
  count(*) filter (where created_at >= now() - interval '24 hours')                        as new_leads,
  count(*) filter (where created_at >= now() - interval '24 hours' and urgency = 'hot')    as hot_leads,
  count(*) filter (where status = 'won' and updated_at >= now() - interval '24 hours')     as won,
  count(*) filter (where status = 'new' and created_at < now() - interval '24 hours')      as awaiting_follow_up
from leads;

-- No policies: the anon key cannot read or write leads. Only n8n (service role) can.
alter table leads       enable row level security;
alter table lead_events enable row level security;
