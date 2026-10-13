-- HashMM V1600 additive schema contract. The authoritative copy is also
-- shipped with the server under sql/20260810_v1600_cross_device_kernel.sql.
begin;
create table if not exists public.hashmm_schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);
alter table public.chat_conversations
  add column if not exists archived boolean not null default false,
  add column if not exists project_id text,
  add column if not exists revision bigint not null default 1,
  add column if not exists sync_state text not null default 'synced',
  add column if not exists last_message_at timestamptz;
alter table public.chat_messages
  add column if not exists updated_at timestamptz not null default now(),
  add column if not exists groundings jsonb not null default '{}'::jsonb,
  add column if not exists run_manifest jsonb not null default '{}'::jsonb;
create index if not exists idx_chatconv_v1600_owner_page
  on public.chat_conversations
  (user_id, archived, project_id, pinned desc, updated_at desc, id desc);
create index if not exists idx_chatmsg_v1600_owner_page
  on public.chat_messages(user_id, conv_id, created_at, id);
alter table public.chat_conversations enable row level security;
alter table public.chat_messages enable row level security;
insert into public.hashmm_schema_migrations(version)
values ('20260810_v1600_cross_device_kernel')
on conflict (version) do nothing;
commit;
