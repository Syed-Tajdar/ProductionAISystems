-- One row per day and sales channel, loaded by your POS, shop or CRM export.
create table daily_sales (
  day          date not null,
  channel      text not null,                  -- e.g. website, whatsapp, walk_in
  orders       integer not null default 0 check (orders >= 0),
  revenue_aed  numeric(12, 2) not null default 0 check (revenue_aed >= 0),
  new_leads    integer not null default 0 check (new_leads >= 0),
  primary key (day, channel)
);

-- n8n reads with the service role key; the anon key gets nothing.
alter table daily_sales enable row level security;
