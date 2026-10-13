-- V344: deterministic Chat/RAG/Agent run provenance for existing Supabase projects.
-- Idempotent; run once in Supabase SQL Editor.  Local SQLite migrates itself.
alter table public.chat_messages
  add column if not exists run_manifest jsonb default '{}'::jsonb;

comment on column public.chat_messages.run_manifest is
  'Runtime config fingerprints, stage latency, stop reason and deterministic external verification checks.';
