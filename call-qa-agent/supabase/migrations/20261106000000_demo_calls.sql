-- Calls visitors may browse without the passcode when DEMO_MODE is on. Safe to run twice.
alter table calls add column if not exists is_demo boolean not null default false;

create index if not exists calls_demo_idx on calls (created_at desc) where is_demo;
