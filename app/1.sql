-- ============================================================
--  HashMM App — Supabase 配置 SQL（登录 + 用户档案 + 改密码）
--  在你的 Supabase 项目 → SQL Editor → 整段粘贴运行一次即可。
--
--  ⚠️ 先确认：App 的 local.properties 里 SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY
--     必须指向「你创建用户(2721985705@qq.com)的同一个项目」。否则用户不在 App 查询的
--     项目里，怎么都登不进。项目 URL 见 Supabase → Project Settings → API。
-- ============================================================

-- 0) 密码加密扩展（Supabase 一般已启用；没有则这句会装上）
create extension if not exists pgcrypto with schema extensions;

-- ============================================================
-- 1) profiles 表：扩展 auth.users 的业务字段（登录后 App 要读/写这张表）
-- ============================================================
create table if not exists public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  username    text unique not null,
  avatar_url  text,
  bio         text,
  search_count int default 0,
  is_admin    boolean default false,
  created_at  timestamptz default now()
);

alter table public.profiles enable row level security;

-- RLS 策略（用 do 块包裹，重复运行不报错）
do $$ begin
  create policy "profiles_public_read" on public.profiles for select using (true);
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "profiles_self_update" on public.profiles for update using (auth.uid() = id);
exception when duplicate_object then null; end $$;

do $$ begin
  create policy "profiles_self_insert" on public.profiles for insert with check (auth.uid() = id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 2) 新用户注册时自动建 profile（服务端触发器，比客户端插入更稳，避免邮箱确认/并发等情况漏建）
-- ============================================================
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, username, is_admin)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'username', split_part(new.email, '@', 1)),
    false
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ============================================================
-- 3) 给「已经存在」的用户补建 profile（你现在的 2721985705@qq.com 是在加触发器之前注册的，
--    所以没有 profile 行；这句把所有现存用户补上，第一个用户设为 admin）
-- ============================================================
insert into public.profiles (id, username, is_admin)
select
  u.id,
  split_part(u.email, '@', 1),
  (u.id = (select id from auth.users order by created_at limit 1))  -- 第一个用户 = admin
from auth.users u
on conflict (id) do nothing;

-- ============================================================
-- 4) 密码重置请使用 Supabase Auth 控制台或受保护的 Admin API
--    （Supabase 控制台不能直接设密码，只能用 SQL 改 auth.users 的 bcrypt 哈希）
-- ============================================================
-- Do not hard-code or directly mutate an Auth password in a migration.
-- Use Supabase Auth password recovery or a protected server-side Admin API.

-- 如果上一句报 "function extensions.crypt does not exist"，改用不带 schema 前缀的版本：

-- ============================================================
-- 完成。回 App 使用 Supabase Auth 中已配置的账号登录即可。
--
-- 说明：这是「HashMM 工作台 + 远程控制」所需的最小配置（登录 + 用户档案）。
-- 原 App 的社交页（发现/社区等）还引用了 discover_cards 等表——你既然要把 App 改成 HashMM、
-- 不要社交功能，这些表就不必建；用不到的社交页留空或后续移除即可，不影响登录与 HashMM 功能。
-- ============================================================


-- ============================================================
--  HashMM — Supabase 同步 schema（登录 + 用户数据云同步）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  设计依据：直接镜像客户端后端真实 SQLite 表（hashmm/api/database.py）：
--    conversations / messages / user_profiles / user_memory
--  作用：Supabase 作为「始终可达」的用户可见数据缓存（聊天列表、设置、记忆）；
--        RAG 大文件 / 向量索引不进 Supabase，留在 AutoDL 私有云，App 按需直连。
--
--  ?? App 的 SUPABASE_URL 必须指向你创建登录用户的同一个项目。
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
  archived    boolean not null default false,
  project_id  text,
  revision    bigint not null default 1,
  sync_state  text not null default 'synced',
  last_message_at timestamptz,
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
  groundings  jsonb default '{}'::jsonb,
  run_manifest jsonb default '{}'::jsonb,
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
-- 6) 密码由 Supabase Auth 管理，不在 SQL 迁移中写入
-- ============================================================
-- Password reset belongs to Supabase Auth; never store it in this migration.

-- ============================================================
-- 完成。
-- App 端：登录后先拉 chat_conversations（可见先同步）；打开会话再拉 chat_messages（点进去再同步）；
--         设置/记忆在后台拉。RAG 大文件走 AutoDL 直连，不经 Supabase。
-- 客户端侧写入 Supabase（让数据流进这些表）是下一步：客户端 user_id 形如 "sb_<uuid>"，
--         写入时去掉 "sb_" 前缀即为这里的 user_id(uuid)。
-- ============================================================


-- ============================================================
--  HashMM — 实时任务进度表 client_activity（Supabase Realtime）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  作用：客户端把「正在进行的任务」（对话生成中 / RAG 解析中）实时写入这张表；
--        App 通过 Supabase Realtime 订阅本表变更，无需轮询即可实时显示任务进度并一键接管。
-- ============================================================

create table if not exists public.client_activity (
  id          text primary key,                                   -- chat: 用 conv_id；job: 用 doc_id
  user_id     uuid not null references auth.users(id) on delete cascade,
  kind        text not null default 'chat',                       -- 'chat' | 'job'
  title       text default '',                                    -- 会话标题 / 任务描述
  status      text default 'active',                              -- chat: 'active'；job: 阶段名(extracting/embedding/...)
  done        int default 0,
  total       int default 0,
  updated_at  timestamptz default now()
);
alter table public.client_activity enable row level security;
create index if not exists idx_cactivity_user on public.client_activity(user_id, updated_at desc);

do $$ begin create policy "cactivity_select_own" on public.client_activity for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "cactivity_insert_own" on public.client_activity for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "cactivity_update_own" on public.client_activity for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "cactivity_delete_own" on public.client_activity for delete using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- 开启 Realtime（把表加入 supabase_realtime 发布；已加入则跳过）
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'client_activity'
  ) then
    alter publication supabase_realtime add table public.client_activity;
  end if;
end $$;

-- ============================================================
-- 完成。App 订阅 public.client_activity（按 user_id 过滤）即可实时收到任务进度。
-- 后端：对话开始生成→写入(kind=chat)，完成→删除；RAG 解析→按阶段写入(kind=job)，完成→删除。
-- ============================================================


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


-- ============================================================
--  HashMM — 开启会话/消息表的 Realtime（实时联动）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  作用：让 App 能实时收到 chat_conversations / chat_messages 的变更——
--        客户端一改（新建会话、改名、置顶、新消息），App 列表/详情立刻更新，无需轮询。
--  （RLS 仍然生效：Realtime 也只推送当前用户自己的行。）
-- ============================================================

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'chat_conversations'
  ) then
    alter publication supabase_realtime add table public.chat_conversations;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'chat_messages'
  ) then
    alter publication supabase_realtime add table public.chat_messages;
  end if;
end $$;

-- ============================================================
-- 完成。App 订阅这两张表（按 user_id / conv_id 过滤）即可实时联动。
-- ============================================================


-- ============================================================
--  HashMM — 把指定 Supabase 账号设为管理员
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  原理：把 role=admin 写入用户的 app_metadata（auth.users.raw_app_meta_data）。
--        Supabase 会把它放进登录令牌（JWT）的 app_metadata 里，后端据此判管理员。
--  ⚠️ 改完后该账号需要「重新登录」一次，新令牌才会带上 admin。
-- ============================================================

update auth.users
set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
where email = '2721985705@qq.com';

-- 确认结果（应能看到 role: admin）
select email, raw_app_meta_data ->> 'role' as role
from auth.users
where email = '2721985705@qq.com';

-- ── 如需再设别的管理员，把邮箱换掉再跑一次即可 ──
-- update auth.users
-- set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
-- where email = '另一个邮箱@example.com';

-- ── 如需取消某账号的管理员 ──
-- update auth.users
-- set raw_app_meta_data = raw_app_meta_data - 'role'
-- where email = '2721985705@qq.com';

-- ============================================================
-- 完成。该账号重新登录后，客户端 / App 里即为管理员。
-- ============================================================


-- HashMM 零配置：全局 app_config 表，存后端公网地址等。
-- 后端启动时用 service key 写入 backend_url；App 登录后读取，实现零配置。
-- 幂等，可重复执行。

create table if not exists public.app_config (
  key        text primary key,
  value      text not null default '',
  updated_at timestamptz not null default now()
);

alter table public.app_config enable row level security;

-- 所有已登录用户可读（App 读 backend_url）
drop policy if exists "app_config_read" on public.app_config;
create policy "app_config_read" on public.app_config
  for select to authenticated using (true);

-- 写入由后端用 service_role key 完成（service role 绕过 RLS，无需额外 policy）。

-- updated_at 自动维护（复用/新建触发器函数）
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end; $$;

drop trigger if exists trg_app_config_updated on public.app_config;
create trigger trg_app_config_updated before update on public.app_config
  for each row execute function public.set_updated_at();

-- 可选：放进 realtime 发布，便于将来地址变更实时下发
do $$ begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname='supabase_realtime' and schemaname='public' and tablename='app_config'
  ) then
    alter publication supabase_realtime add table public.app_config;
  end if;
end $$;


-- ============================================================
-- HashMM 远程控制 · Supabase 信令（P2P，无需隧道）
-- ------------------------------------------------------------
-- 让被控端(Electron host)与控制端(App viewer)在「同一账号 + 联网」下，
-- 通过 Supabase 交换 WebRTC 建连信令(offer/answer/ICE)，屏幕视频走 P2P 直连。
-- room 一律 = 当前登录用户的 Supabase uid（auth.uid()），保证同账号隔离、跨账号不可见。
-- 幂等，可重复执行。
-- ============================================================

-- ── 在线被控端（host presence）──────────────────────────────
create table if not exists public.remote_presence (
    room        text not null,                 -- = auth.uid()::text
    host_id     text not null,                  -- 被控端自生成的唯一 id
    name        text,
    platform    text,
    updated_at  timestamptz not null default now(),
    primary key (room, host_id)
);

alter table public.remote_presence enable row level security;

drop policy if exists "remote_presence_rw_own" on public.remote_presence;
create policy "remote_presence_rw_own" on public.remote_presence
    for all
    using (auth.uid()::text = room)
    with check (auth.uid()::text = room);

-- ── 信令消息（offer / answer / ice / connect / input / bye）──
create table if not exists public.remote_signals (
    id          bigint generated always as identity primary key,
    room        text not null,                  -- = auth.uid()::text
    recipient   text not null,                  -- 'host:<id>' 或 'viewer:<id>'
    sender      text not null,                  -- 'host:<id>' 或 'viewer:<id>'
    kind        text not null,                  -- offer|answer|ice|connect|input|bye
    payload     jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists remote_signals_room_recipient_idx
    on public.remote_signals (room, recipient, id);

alter table public.remote_signals enable row level security;

drop policy if exists "remote_signals_rw_own" on public.remote_signals;
create policy "remote_signals_rw_own" on public.remote_signals
    for all
    using (auth.uid()::text = room)
    with check (auth.uid()::text = room);

-- ── 实时（App viewer 通过 Realtime 订阅收信令）──────────────
do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'remote_signals'
    ) then
        alter publication supabase_realtime add table public.remote_signals;
    end if;
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'remote_presence'
    ) then
        alter publication supabase_realtime add table public.remote_presence;
    end if;
end $$;

-- ── 自动清理：删除 2 分钟前的旧信令 + 1 分钟未更新的离线 host ──
-- （可选；没有 pg_cron 也不影响，行数很小。App/host 也会自行忽略过期数据。）



-- ============================================================
--  HashMM — 用户档案 / 头像 / 模型配置 的云端持久化（三端共用 Supabase）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  解决的问题（甲方反馈）：
--   1) 头像「过一会变默认」「换头像不行」「跨端不同步」——
--      根因：头像只存在 AutoDL 后端磁盘（/api/profile/avatar），后端重启/旧版/换设备即丢。
--      改法：把头像作为「始终可达」的用户数据存进 Supabase profiles.avatar_url
--            （小图 data URL 字符串），App / 客户端登录后直接从 Supabase 读，不依赖 AutoDL。
--   2) 用户管理只显示后端本地用户——
--      改法：提供 list_all_profiles() RPC（仅管理员可调），返回 Supabase 里所有用户。
--   3) 模型管理跨端不持久——
--      改法：user_models 表存每个用户的模型配置，登录后各端读取。
--
--  依赖：需先跑过 sql/hashmm-supabase-sync.sql（建了 profiles 等表）与 hashmm-set-admin.sql。
-- ============================================================

-- ── 0) 复用 updated_at 触发器函数（若已存在则覆盖，行为一致）──
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end; $$;

-- ============================================================
-- 1) profiles.avatar_url：确保存在且为 text（存小图 data URL，约 20–40KB）
--    （hashmm-supabase-sync.sql 已建该列；这里幂等兜底，单独跑本文件也安全）
-- ============================================================
create table if not exists public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  username     text unique,
  display_name text default '',
  avatar_url   text,
  is_admin     boolean default false,
  created_at   timestamptz default now()
);
alter table public.profiles add column if not exists avatar_url   text;
alter table public.profiles add column if not exists display_name text default '';
alter table public.profiles add column if not exists updated_at   timestamptz default now();
alter table public.profiles enable row level security;

drop trigger if exists trg_profiles_updated on public.profiles;
create trigger trg_profiles_updated before update on public.profiles
  for each row execute function public.set_updated_at();

-- 本人可读 / 可写自己的档案（含 avatar_url）。幂等创建。
do $$ begin create policy "profiles_read_own"   on public.profiles for select using (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "profiles_insert_own" on public.profiles for insert with check (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "profiles_update_own" on public.profiles for update using (auth.uid() = id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 2) 管理员判定：从登录令牌(JWT) 的 app_metadata.role 读 'admin'
--    （与 hashmm-set-admin.sql 写入的位置一致）
-- ============================================================
create or replace function public.is_admin()
returns boolean language sql stable as $$
  select coalesce((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', false);
$$;

-- 管理员可读「所有」profiles（用户管理面板按需走 RPC，这条策略亦便于直接 select）
do $$ begin create policy "profiles_read_all_admin" on public.profiles for select using (public.is_admin());
exception when duplicate_object then null; end $$;

-- ============================================================
-- 3) list_all_profiles()：管理员拉「Supabase 里全部用户」
--    SECURITY DEFINER 以便联表 auth.users 取 email / 最后登录时间；函数内自校验管理员。
-- ============================================================
create or replace function public.list_all_profiles()
returns table (
  id              uuid,
  username        text,
  display_name    text,
  avatar_url      text,
  is_admin        boolean,
  email           text,
  created_at      timestamptz,
  last_sign_in_at timestamptz
)
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'forbidden: admin only' using errcode = '42501';
  end if;
  return query
    select p.id,
           coalesce(p.username, split_part(u.email, '@', 1)) as username,
           coalesce(p.display_name, '')                      as display_name,
           p.avatar_url,
           coalesce(p.is_admin, false)                       as is_admin,
           u.email::text                                     as email,
           coalesce(p.created_at, u.created_at)              as created_at,
           u.last_sign_in_at
    from auth.users u
    left join public.profiles p on p.id = u.id
    order by u.created_at desc nulls last;
end; $$;

revoke all on function public.list_all_profiles() from public;
grant execute on function public.list_all_profiles() to authenticated;

-- 兜底：给所有已存在 auth.users 补一条 profiles（避免老用户没有档案行）
insert into public.profiles (id, username, is_admin)
select u.id, split_part(u.email, '@', 1), false
from auth.users u
on conflict (id) do nothing;

-- ============================================================
-- 4) user_models：每个用户的模型配置（登录后各端读取）
--    ⚠️ 安全：api_key 以明文存放，但 RLS 仅本人可读自己的行（他人/匿名读不到）。
--       若要更强保护可改用 Supabase Vault；当前按「跨端可用」优先。
-- ============================================================
create table if not exists public.user_models (
  id          text primary key,                                   -- 客户端生成的稳定 id
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null default '',                           -- 显示名（如 "DeepSeek V3"）
  provider    text default '',                                    -- openai / deepseek / anthropic / qwen ...
  base_url    text default '',
  model_name  text default '',
  api_key     text default '',
  temperature real default 0.1,
  max_tokens  int default 16384,
  is_default  boolean default false,
  enabled     boolean default true,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
alter table public.user_models enable row level security;
create index if not exists idx_user_models_user on public.user_models(user_id, updated_at desc);

drop trigger if exists trg_user_models_updated on public.user_models;
create trigger trg_user_models_updated before insert or update on public.user_models
  for each row execute function public.set_updated_at();

do $$ begin create policy "user_models_select_own" on public.user_models for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "user_models_insert_own" on public.user_models for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "user_models_update_own" on public.user_models for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "user_models_delete_own" on public.user_models for delete using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- 放进 realtime 发布，便于一端改模型另一端实时刷新（可选）
do $$ begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname='supabase_realtime' and schemaname='public' and tablename='user_models'
  ) then
    alter publication supabase_realtime add table public.user_models;
  end if;
end $$;

-- ============================================================
-- 完成。
--  · 头像：各端把 ≤256px 的 JPEG 压成 data URL 写入 profiles.avatar_url；登录后读它显示。
--  · 用户管理：前端调用 rpc('list_all_profiles') 拿全部用户（管理员）。
--  · 模型配置：各端读写 user_models（RLS 仅本人）。
-- ============================================================


-- ============================================================
--  HashMM — 用户档案 / 头像 / 模型配置 的云端持久化（三端共用 Supabase）
--  在 Supabase → SQL Editor 整段运行一次。可重复运行（幂等）。
--
--  解决的问题（甲方反馈）：
--   1) 头像「过一会变默认」「换头像不行」「跨端不同步」——
--      根因：头像只存在 AutoDL 后端磁盘（/api/profile/avatar），后端重启/旧版/换设备即丢。
--      改法：把头像作为「始终可达」的用户数据存进 Supabase profiles.avatar_url
--            （小图 data URL 字符串），App / 客户端登录后直接从 Supabase 读，不依赖 AutoDL。
--   2) 用户管理只显示后端本地用户——
--      改法：提供 list_all_profiles() RPC（仅管理员可调），返回 Supabase 里所有用户。
--   3) 模型管理跨端不持久——
--      改法：user_models 表存每个用户的模型配置，登录后各端读取。
--
--  依赖：需先跑过 sql/hashmm-supabase-sync.sql（建了 profiles 等表）与 hashmm-set-admin.sql。
-- ============================================================

-- ── 0) 复用 updated_at 触发器函数（若已存在则覆盖，行为一致）──
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end; $$;

-- ============================================================
-- 1) profiles.avatar_url：确保存在且为 text（存小图 data URL，约 20–40KB）
--    （hashmm-supabase-sync.sql 已建该列；这里幂等兜底，单独跑本文件也安全）
-- ============================================================
create table if not exists public.profiles (
  id           uuid primary key references auth.users(id) on delete cascade,
  username     text unique,
  display_name text default '',
  avatar_url   text,
  is_admin     boolean default false,
  created_at   timestamptz default now()
);
alter table public.profiles add column if not exists avatar_url   text;
alter table public.profiles add column if not exists display_name text default '';
alter table public.profiles add column if not exists updated_at   timestamptz default now();
alter table public.profiles enable row level security;

drop trigger if exists trg_profiles_updated on public.profiles;
create trigger trg_profiles_updated before update on public.profiles
  for each row execute function public.set_updated_at();

-- 本人可读 / 可写自己的档案（含 avatar_url）。幂等创建。
do $$ begin create policy "profiles_read_own"   on public.profiles for select using (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "profiles_insert_own" on public.profiles for insert with check (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "profiles_update_own" on public.profiles for update using (auth.uid() = id);
exception when duplicate_object then null; end $$;

-- ============================================================
-- 2) 管理员判定：从登录令牌(JWT) 的 app_metadata.role 读 'admin'
--    （与 hashmm-set-admin.sql 写入的位置一致）
-- ============================================================
create or replace function public.is_admin()
returns boolean language sql stable as $$
  select coalesce((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', false);
$$;

-- 管理员可读「所有」profiles（用户管理面板按需走 RPC，这条策略亦便于直接 select）
do $$ begin create policy "profiles_read_all_admin" on public.profiles for select using (public.is_admin());
exception when duplicate_object then null; end $$;

-- ============================================================
-- 3) list_all_profiles()：管理员拉「Supabase 里全部用户」
--    SECURITY DEFINER 以便联表 auth.users 取 email / 最后登录时间；函数内自校验管理员。
-- ============================================================
create or replace function public.list_all_profiles()
returns table (
  id              uuid,
  username        text,
  display_name    text,
  avatar_url      text,
  is_admin        boolean,
  email           text,
  created_at      timestamptz,
  last_sign_in_at timestamptz
)
language plpgsql security definer set search_path = public as $$
begin
  if not public.is_admin() then
    raise exception 'forbidden: admin only' using errcode = '42501';
  end if;
  return query
    select p.id,
           coalesce(p.username, split_part(u.email, '@', 1)) as username,
           coalesce(p.display_name, '')                      as display_name,
           p.avatar_url,
           coalesce(p.is_admin, false)                       as is_admin,
           u.email::text                                     as email,
           coalesce(p.created_at, u.created_at)              as created_at,
           u.last_sign_in_at
    from auth.users u
    left join public.profiles p on p.id = u.id
    order by u.created_at desc nulls last;
end; $$;

revoke all on function public.list_all_profiles() from public;
grant execute on function public.list_all_profiles() to authenticated;

-- 兜底：给所有已存在 auth.users 补一条 profiles（避免老用户没有档案行）
insert into public.profiles (id, username, is_admin)
select u.id, split_part(u.email, '@', 1), false
from auth.users u
on conflict (id) do nothing;

-- ============================================================
-- 4) user_models：每个用户的模型配置（登录后各端读取）
--    ⚠️ 安全：api_key 以明文存放，但 RLS 仅本人可读自己的行（他人/匿名读不到）。
--       若要更强保护可改用 Supabase Vault；当前按「跨端可用」优先。
-- ============================================================
create table if not exists public.user_models (
  id          text primary key,                                   -- 客户端生成的稳定 id
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null default '',                           -- 显示名（如 "DeepSeek V3"）
  provider    text default '',                                    -- openai / deepseek / anthropic / qwen ...
  base_url    text default '',
  model_name  text default '',
  api_key     text default '',
  temperature real default 0.1,
  max_tokens  int default 16384,
  is_default  boolean default false,
  enabled     boolean default true,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
alter table public.user_models enable row level security;
create index if not exists idx_user_models_user on public.user_models(user_id, updated_at desc);

drop trigger if exists trg_user_models_updated on public.user_models;
create trigger trg_user_models_updated before insert or update on public.user_models
  for each row execute function public.set_updated_at();

do $$ begin create policy "user_models_select_own" on public.user_models for select using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "user_models_insert_own" on public.user_models for insert with check (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "user_models_update_own" on public.user_models for update using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;
do $$ begin create policy "user_models_delete_own" on public.user_models for delete using (auth.uid() = user_id);
exception when duplicate_object then null; end $$;

-- 放进 realtime 发布，便于一端改模型另一端实时刷新（可选）
do $$ begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname='supabase_realtime' and schemaname='public' and tablename='user_models'
  ) then
    alter publication supabase_realtime add table public.user_models;
  end if;
end $$;

-- ============================================================
-- 完成。
--  · 头像：各端把 ≤256px 的 JPEG 压成 data URL 写入 profiles.avatar_url；登录后读它显示。
--  · 用户管理：前端调用 rpc('list_all_profiles') 拿全部用户（管理员）。
--  · 模型配置：各端读写 user_models（RLS 仅本人）。
-- ============================================================


-- HashMM 桌面文件投送（桌面→App，走云端 Supabase）所需的表。
-- 在 Supabase 控制台 → SQL Editor 里整段执行一次即可。
-- 作用：App 聊天里说"把电脑/桌面的某文件发我" → 后端写一条 pending 请求到这张表
--       → 桌面客户端常驻轮询消费 → 找到文件上传到对话 → 回写助手消息（带下载链接）。

create table if not exists public.file_requests (
  id          uuid primary key default gen_random_uuid(),
  user_id     text not null,                 -- Supabase 用户 uid（与对话归属一致）
  conv_id     text not null,                 -- 目标对话 id
  query       text default '',               -- 用户原话（桌面端据此按本地实际文件名匹配）
  status      text not null default 'pending', -- pending / processing / done / not_found / error
  result      text default '',
  created_at  timestamptz not null default now()
);

create index if not exists idx_file_requests_user_status
  on public.file_requests (user_id, status, created_at);

-- 行级安全：用户只能读写自己的请求（桌面端用"用户 token"轮询/更新都受这条约束）。
-- 后端写入用的是 service_role key，自动绕过 RLS，不受影响。
alter table public.file_requests enable row level security;

drop policy if exists "file_requests own rows" on public.file_requests;
create policy "file_requests own rows" on public.file_requests
  for all
  using (auth.uid()::text = user_id)
  with check (auth.uid()::text = user_id);

-- （可选）开启 Realtime。当前桌面端用的是轮询（每 4s），不开也能用；要更实时可解开下一行。
-- alter publication supabase_realtime add table public.file_requests;


-- 手机→电脑 反向取文件（"电脑端问手机要照片"）所需的最小库改动。
-- 给现有 file_requests 表加一个 target 列，用来区分这条请求该由谁来执行：
--   target='desktop'（默认）→ 桌面客户端常驻轮询消费（现状，把电脑文件发到会话）
--   target='phone'          → 手机 App 消费（把手机相册的照片上传到会话）
-- 安全、向后兼容：默认 'desktop'，并把历史行回填为 'desktop'，桌面端逻辑不受影响。

alter table if exists public.file_requests
  add column if not exists target text not null default 'desktop';

-- 回填历史行（防止存在 NULL）
update public.file_requests set target = 'desktop' where target is null;

-- 让 App 端按 (user_id, status, target) 拉取待办更快
create index if not exists idx_file_requests_target
  on public.file_requests (user_id, status, target);


-- ============================================================================
-- HashMM 增量同步 / 本地加密缓存 —— Supabase 部署 SQL（在 Supabase SQL Editor 跑）
-- 作用：让 App 已内置的「增量同步」真正生效 + 加好安全(RLS)。可重复执行（幂等）。
--
-- 背景：App 端 SyncRepository 是按 updated_at 做增量的
--   （select chat_messages where updated_at > 上次同步），
--   但此前后端只写 created_at、表也可能没有 updated_at / 没有索引 / 没有 RLS。
--   这份 SQL 补齐：updated_at 列 + 自动更新触发器 + 增量查询索引 + 行级安全 + 实时订阅。
-- ============================================================================

-- ---------- 0) 通用：自动维护 updated_at 的触发器函数 ----------
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end; $$;

-- ============================================================================
-- 1) chat_conversations
-- ============================================================================
-- 1a) 补 updated_at 列（没有才加），默认 now()
alter table public.chat_conversations
  add column if not exists updated_at timestamptz not null default now();
-- 1b) 给历史行一个合理的 updated_at（用 created_at 回填，仅一次性、对老数据）
update public.chat_conversations
  set updated_at = coalesce(created_at, now())
  where updated_at is null;
-- 1c) 每次 UPDATE 自动把 updated_at 刷成 now()（改标题/置顶等都会被 App 增量拉到）
drop trigger if exists trg_conv_updated_at on public.chat_conversations;
create trigger trg_conv_updated_at
  before update on public.chat_conversations
  for each row execute function public.set_updated_at();
-- 1d) 增量查询索引：按 user_id + updated_at（App 拉「我的、变化过的」会话）
create index if not exists idx_conv_user_updated
  on public.chat_conversations (user_id, updated_at desc);

-- 1e) 行级安全：每个登录用户只能看/改自己的会话
alter table public.chat_conversations enable row level security;
drop policy if exists conv_select_own on public.chat_conversations;
drop policy if exists conv_insert_own on public.chat_conversations;
drop policy if exists conv_update_own on public.chat_conversations;
drop policy if exists conv_delete_own on public.chat_conversations;
create policy conv_select_own on public.chat_conversations
  for select using (user_id::text = (auth.uid())::text);
create policy conv_insert_own on public.chat_conversations
  for insert with check (user_id::text = (auth.uid())::text);
create policy conv_update_own on public.chat_conversations
  for update using (user_id::text = (auth.uid())::text)
            with check (user_id::text = (auth.uid())::text);
create policy conv_delete_own on public.chat_conversations
  for delete using (user_id::text = (auth.uid())::text);

-- ============================================================================
-- 2) chat_messages
-- ============================================================================
alter table public.chat_messages
  add column if not exists updated_at timestamptz not null default now();
update public.chat_messages
  set updated_at = coalesce(created_at, now())
  where updated_at is null;
drop trigger if exists trg_msg_updated_at on public.chat_messages;
create trigger trg_msg_updated_at
  before update on public.chat_messages
  for each row execute function public.set_updated_at();
-- 增量查询索引：App 按 conv_id + updated_at 拉某会话的新消息
create index if not exists idx_msg_conv_updated
  on public.chat_messages (conv_id, updated_at);
create index if not exists idx_msg_user_updated
  on public.chat_messages (user_id, updated_at);

alter table public.chat_messages enable row level security;
drop policy if exists msg_select_own on public.chat_messages;
drop policy if exists msg_insert_own on public.chat_messages;
drop policy if exists msg_update_own on public.chat_messages;
drop policy if exists msg_delete_own on public.chat_messages;
create policy msg_select_own on public.chat_messages
  for select using (user_id::text = (auth.uid())::text);
create policy msg_insert_own on public.chat_messages
  for insert with check (user_id::text = (auth.uid())::text);
create policy msg_update_own on public.chat_messages
  for update using (user_id::text = (auth.uid())::text)
            with check (user_id::text = (auth.uid())::text);
create policy msg_delete_own on public.chat_messages
  for delete using (user_id::text = (auth.uid())::text);

-- ============================================================================
-- 3) user_memory（App syncMemories 用；量小，RLS 即可）
-- ============================================================================
alter table if exists public.user_memory enable row level security;
drop policy if exists mem_all_own on public.user_memory;
create policy mem_all_own on public.user_memory
  for all using (user_id::text = (auth.uid())::text)
          with check (user_id::text = (auth.uid())::text);

-- ============================================================================
-- 4) 实时订阅（App 的 Realtime：对端一改本端立刻刷新）
--    把两张表加入 supabase_realtime 发布。已在发布里则忽略报错即可。
-- ============================================================================
do $$ begin
  alter publication supabase_realtime add table public.chat_conversations;
exception when duplicate_object then null; when others then null; end $$;
do $$ begin
  alter publication supabase_realtime add table public.chat_messages;
exception when duplicate_object then null; when others then null; end $$;

-- ============================================================================
-- 完成。验证：
--   select column_name from information_schema.columns
--     where table_name='chat_messages' and column_name='updated_at';   -- 应有一行
--   select indexname from pg_indexes where tablename='chat_messages';  -- 应含 idx_msg_conv_updated
-- ============================================================================


-- 删除 5 小时前的历史信令（按你的接力时间窗对齐；可配 pg_cron 定时）
delete from public.remote_signals where created_at < now() - interval '5 hours';


-- ============================================================
--  HashMM Supabase 补丁：给 chat_conversations 加 archived 列
--  这是"归档后还原、再打开仍是全部历史"的根治补丁。
--
--  【为什么需要它】
--  你的 chat_conversations 表原本没有 archived 列。App/桌面归档一个对话时，
--  本地 SQLite 记住了 archived=1，但推送到 Supabase 时这个状态无处存储，被丢弃。
--  下次打开，客户端从 Supabase 拉会话列表（云端全是"未归档"），归档状态被覆盖，
--  于是归档/还原都不生效。加上这一列后，归档状态能在云端持久，多端一致。
--
--  【怎么用】
--  打开 Supabase 控制台 → 左侧 SQL Editor → New query → 粘贴本文件全部内容 → Run。
--  安全可重复执行（if not exists / 幂等），不会影响现有数据。
-- ============================================================

-- 1) 加 archived 列（布尔，默认 false=未归档）
alter table public.chat_conversations
  add column if not exists archived boolean default false;

-- 2) 把历史 NULL 值补成 false（老数据没有这列，防止 NULL 影响过滤）
update public.chat_conversations
  set archived = false
  where archived is null;

-- 3) 建一个部分索引：只索引未归档的会话（主列表查询最频繁，加速）
create index if not exists idx_chatconv_active
  on public.chat_conversations(user_id, updated_at desc)
  where archived = false;

-- ============================================================================
-- Final authority: strict cross-device ownership and deterministic chat order
-- ============================================================================
-- PostgreSQL combines permissive policies with OR. Earlier, legacy policy
-- names therefore must be removed before installing the final owner checks.
drop policy if exists profiles_public_read on public.profiles;

drop policy if exists chatmsg_select_own on public.chat_messages;
drop policy if exists chatmsg_insert_own on public.chat_messages;
drop policy if exists chatmsg_update_own on public.chat_messages;
drop policy if exists chatmsg_delete_own on public.chat_messages;
drop policy if exists msg_select_own on public.chat_messages;
drop policy if exists msg_insert_own on public.chat_messages;
drop policy if exists msg_update_own on public.chat_messages;
drop policy if exists msg_delete_own on public.chat_messages;
drop policy if exists hashmm_messages_select_own on public.chat_messages;
drop policy if exists hashmm_messages_insert_own on public.chat_messages;
drop policy if exists hashmm_messages_update_own on public.chat_messages;
drop policy if exists hashmm_messages_delete_own on public.chat_messages;

create policy hashmm_messages_select_own on public.chat_messages
  for select using (
    user_id = auth.uid()
    and exists (
      select 1 from public.chat_conversations c
      where c.id = chat_messages.conv_id and c.user_id = auth.uid()
    )
  );
create policy hashmm_messages_insert_own on public.chat_messages
  for insert with check (
    user_id = auth.uid()
    and exists (
      select 1 from public.chat_conversations c
      where c.id = chat_messages.conv_id and c.user_id = auth.uid()
    )
  );
create policy hashmm_messages_update_own on public.chat_messages
  for update using (
    user_id = auth.uid()
    and exists (
      select 1 from public.chat_conversations c
      where c.id = chat_messages.conv_id and c.user_id = auth.uid()
    )
  ) with check (
    user_id = auth.uid()
    and exists (
      select 1 from public.chat_conversations c
      where c.id = chat_messages.conv_id and c.user_id = auth.uid()
    )
  );
create policy hashmm_messages_delete_own on public.chat_messages
  for delete using (
    user_id = auth.uid()
    and exists (
      select 1 from public.chat_conversations c
      where c.id = chat_messages.conv_id and c.user_id = auth.uid()
    )
  );

-- Stable tie-breaker for messages created in the same timestamp tick.
create index if not exists idx_chatmsg_owner_order
  on public.chat_messages(user_id, conv_id, created_at, id);

-- ============================================================================
-- Conversation history order: use the newest real message, not metadata edits
-- ============================================================================
alter table public.chat_conversations
  add column if not exists last_message_at timestamptz;

update public.chat_conversations c
set last_message_at = activity.last_message_at
from (
  select user_id, conv_id, max(created_at) as last_message_at
  from public.chat_messages
  group by user_id, conv_id
) activity
where c.id = activity.conv_id
  and c.user_id = activity.user_id
  and c.last_message_at is distinct from activity.last_message_at;

create or replace function public.refresh_chat_conversation_activity()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  target_conv text;
  target_user uuid;
begin
  target_conv := case when tg_op = 'DELETE' then old.conv_id else new.conv_id end;
  target_user := case when tg_op = 'DELETE' then old.user_id else new.user_id end;

  update public.chat_conversations c
  set last_message_at = (
    select max(m.created_at)
    from public.chat_messages m
    where m.conv_id = target_conv and m.user_id = target_user
  )
  where c.id = target_conv and c.user_id = target_user;

  if tg_op = 'UPDATE' and old.conv_id is distinct from new.conv_id then
    update public.chat_conversations c
    set last_message_at = (
      select max(m.created_at)
      from public.chat_messages m
      where m.conv_id = old.conv_id and m.user_id = old.user_id
    )
    where c.id = old.conv_id and c.user_id = old.user_id;
  end if;

  if tg_op = 'DELETE' then return old; else return new; end if;
end;
$$;

drop trigger if exists trg_refresh_chat_conversation_activity on public.chat_messages;
create trigger trg_refresh_chat_conversation_activity
  after insert or update or delete on public.chat_messages
  for each row execute function public.refresh_chat_conversation_activity();

create index if not exists idx_chatconv_last_message
  on public.chat_conversations(user_id, last_message_at desc, updated_at desc);

-- 完成。此后客户端推送/拉取会话时会带上 archived 字段，归档与还原将正确持久化。
