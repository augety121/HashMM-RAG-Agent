-- V341: claim-level grounding ledger for existing Supabase projects.
-- Idempotent; run once in Supabase SQL Editor. New installations also receive
-- this column from hashmm-supabase-sync.sql.
alter table public.chat_messages
  add column if not exists groundings jsonb default '{}'::jsonb;

comment on column public.chat_messages.groundings is
  'Claim spans, citation IDs and evidence locations; deterministic audit metadata, not a truth proof.';
