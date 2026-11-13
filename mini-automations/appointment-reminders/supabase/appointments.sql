-- Appointments table read by the reminder workflow.
create extension if not exists pgcrypto;

create table appointments (
  id                    uuid primary key default gen_random_uuid(),
  patient_name          text not null,
  phone                 text not null check (phone ~ '^\+[1-9][0-9]{6,14}$'),
  language              text not null default 'en' check (language in ('en', 'ar')),
  starts_at             timestamptz not null,
  doctor                text not null,
  location              text,
  status                text not null default 'booked'
                        check (status in ('booked', 'cancelled', 'completed', 'no_show')),
  reminder_24h_sent_at  timestamptz,
  reminder_2h_sent_at   timestamptz,
  created_at            timestamptz not null default now()
);

-- The workflow asks for booked appointments in the next 24 hours.
create index appointments_upcoming_idx on appointments (starts_at) where status = 'booked';

-- n8n uses the service role key; the anon key gets nothing.
alter table appointments enable row level security;
