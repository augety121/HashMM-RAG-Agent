-- ============================================================
--  HashMM — 增量同步支撑（每条记录有可靠的 updated_at）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  作用：让 App / 客户端能做「只拉变更」的增量同步——
--        本地记上次同步时间，每次只取 updated_at > 上次同步 的记录，按主键覆盖本地。
--  用服务端 now() 作为权威时间（触发器统一写），避免多端时钟偏差导致漏同步。
-- ============================================================

-- chat_messages 原本只有 created_at，补一个 updated_at 供增量同步
alter table public.chat_messages add column if not exists updated_at timestamptz default now();
-- user_memory 同理
alter table public.user_memory add column if not exists updated_at timestamptz default now();

-- 统一的 updated_at 触发器函数
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end; $$;

-- 给每张同步表挂触发器（insert/update 都把 updated_at 设为服务端 now()）
drop trigger if exists trg_set_updated_at on public.chat_conversations;
create trigger trg_set_updated_at before insert or update on public.chat_conversations
  for each row execute function public.set_updated_at();

drop trigger if exists trg_set_updated_at on public.chat_messages;
create trigger trg_set_updated_at before insert or update on public.chat_messages
  for each row execute function public.set_updated_at();

drop trigger if exists trg_set_updated_at on public.user_settings;
create trigger trg_set_updated_at before insert or update on public.user_settings
  for each row execute function public.set_updated_at();

drop trigger if exists trg_set_updated_at on public.user_memory;
create trigger trg_set_updated_at before insert or update on public.user_memory
  for each row execute function public.set_updated_at();

-- 增量查询用的索引（按 updated_at 拉变更）
create index if not exists idx_chatconv_updated on public.chat_conversations(user_id, updated_at);
create index if not exists idx_chatmsg_updated on public.chat_messages(user_id, updated_at);
create index if not exists idx_umem_updated on public.user_memory(user_id, updated_at);

-- ============================================================
-- 完成。客户端/App 增量同步：select ... where updated_at > {上次同步} order by updated_at；
--   本地按主键 upsert（变更覆盖、未变保留），并把本次最大 updated_at 记为新的「上次同步」。
-- ============================================================
