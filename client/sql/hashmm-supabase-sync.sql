-- ============================================================
--  HashMM — Supabase 同步 schema（登录 + 用户数据云同步）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  设计依据：直接镜像客户端后端真实 SQLite 表（hashmm/api/database.py）：
--    conversations / messages / user_profiles / user_memory
--  作用：Supabase 作为「始终可达」的用户可见数据缓存（聊天列表、设置、记忆）；
--        RAG 大文件 / 向量索引不进 Supabase，留在 AutoDL 私有云，App 按需直连。
--
--  ⚠️ App 的 SUPABASE_URL 必须指向你创建登录用户的同一个项目。
-- ============================================================

create extension if not exists pgcrypto with schema extensions;

-- ============================================================
-- 1) profiles：用户档案（用户名 / 角色）。登录后 App 读它。
-- ============================================================
create table if not exists public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  username    text unique not null,
  display_name text default '',
  avatar_url  text,
  is_admin    boolean default false,
  created_at  timestamptz default now()
);
alter table public.profiles enable row level security;

do $$ begin create policy "profiles_read_own" on public.profiles for select using (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "profiles_upsert_own" on public.profiles for insert with check (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "profiles_update_own" on public.profiles for update using (auth.uid() = id);
exception when duplicate_object then null; end $$;

-- 新用户注册自动建档（服务端触发器）
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, username, is_admin)
  values (new.id, coalesce(new.raw_user_meta_data->>'username', split_part(new.email, '@', 1)), false)
  on conflict (id) do nothing;
  return new;
end; $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- 给已存在用户补档（第一个用户设为 admin）
insert into public.profiles (id, username, is_admin)
select u.id, split_part(u.email,'@',1),
       (u.id = (select id from auth.users order by created_at limit 1))
from auth.users u on conflict (id) do nothing;

-- ============================================================
-- 2) chat_conversations：会话列表（镜像 conversations 表）
--    「可见先同步」：App 登录后先拉这张表，立即展示会话列表。
-- ============================================================
create table if not exists public.chat_conversations (
  id          text primary key,                                   -- 与客户端会话 id 一致
  user_id     uuid not null references auth.users(id) on delete cascade,
  title       text default '新对话',
  pinned      boolean default false,
  metadata    jsonb default '{}'::jsonb,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
alter table public.chat_conversations enable row level security;
create index if not exists idx_chatconv_user on public.chat_conversations(user_id, updated_at desc);

do $$ begin create policy "chatconv_select_own" on public.chat_conversations for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "chatconv_insert_own" on public.chat_conversations for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "chatconv_update_own" on public.chat_conversations for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "chatconv_delete_own" on public.chat_conversations for delete using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 3) chat_messages：会话消息（镜像 messages 表）
--    「点进去再同步」：用户打开某会话时才按 conv_id 拉这张表。
--    user_id 反范式（便于 RLS 与查询）。
-- ============================================================
create table if not exists public.chat_messages (
  id          text primary key,
  conv_id     text not null references public.chat_conversations(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  role        text not null default 'user',
  content     text default '',
  thinking    text default '',
  tool_calls  jsonb default '[]'::jsonb,
  files       jsonb default '[]'::jsonb,
  sources     jsonb default '[]'::jsonb,
  suggestions jsonb default '[]'::jsonb,
  status      text default 'complete',
  tokens_in   int default 0,
  tokens_out  int default 0,
  created_at  timestamptz default now()
);
alter table public.chat_messages enable row level security;
create index if not exists idx_chatmsg_conv on public.chat_messages(conv_id, created_at);

do $$ begin create policy "chatmsg_select_own" on public.chat_messages for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "chatmsg_insert_own" on public.chat_messages for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "chatmsg_update_own" on public.chat_messages for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "chatmsg_delete_own" on public.chat_messages for delete using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 4) user_settings：用户设置（镜像 user_profiles 的 profile JSON 整块）
-- ============================================================
create table if not exists public.user_settings (
  user_id    uuid primary key references auth.users(id) on delete cascade,
  profile    jsonb not null default '{}'::jsonb,
  updated_at timestamptz default now()
);
alter table public.user_settings enable row level security;

do $$ begin create policy "usettings_select_own" on public.user_settings for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "usettings_insert_own" on public.user_settings for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "usettings_update_own" on public.user_settings for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 5) user_memory：用户记忆（镜像 user_memory 表）
-- ============================================================
create table if not exists public.user_memory (
  id          text primary key,
  user_id     uuid not null references auth.users(id) on delete cascade,
  category    text default '',
  key         text not null,
  value       text not null,
  confidence  real default 0.8,
  created_at  timestamptz default now(),
  last_used   timestamptz default now()
);
alter table public.user_memory enable row level security;
create index if not exists idx_umem_user on public.user_memory(user_id);

do $$ begin create policy "umem_select_own" on public.user_memory for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "umem_insert_own" on public.user_memory for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "umem_update_own" on public.user_memory for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "umem_delete_own" on public.user_memory for delete using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 6) 把 admin@example.com 的密码设为 123456
-- ============================================================
update auth.users
set encrypted_password = extensions.crypt('123456', extensions.gen_salt('bf'))
where email = 'admin@example.com';

-- ============================================================
-- 完成。
-- App 端：登录后先拉 chat_conversations（可见先同步）；打开会话再拉 chat_messages（点进去再同步）；
--         设置/记忆在后台拉。RAG 大文件走 AutoDL 直连，不经 Supabase。
-- 客户端侧写入 Supabase（让数据流进这些表）是下一步：客户端 user_id 形如 "sb_<uuid>"，
--         写入时去掉 "sb_" 前缀即为这里的 user_id(uuid)。
-- ============================================================
