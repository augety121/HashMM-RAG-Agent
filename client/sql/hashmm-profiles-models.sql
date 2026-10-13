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
