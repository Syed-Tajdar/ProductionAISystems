-- A readable title and a short summary per call, for the dashboard. The demo seed fills them from the
-- sample scripts; other calls show their file name. Safe to run twice.
alter table calls add column if not exists title text;
alter table calls add column if not exists summary text;
