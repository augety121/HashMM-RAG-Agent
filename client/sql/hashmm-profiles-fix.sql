-- ============================================================
-- HashMM · profiles 体检修复（V271，幂等，可重复执行）
-- 背景：你的 1.sql 是两份脚本拼接——第 16 行的老版 profiles（无 display_name，
--   有 bio/search_count，且策略 profiles_public_read 为全公开读）先执行建表；
--   第 118 行的新版（含 display_name）被 create table if not exists 跳过。
--   结果：库里没有 display_name 列 → App 昵称保存/读取、桌面端 getMyProfile
--   的 select display_name 都会失败；同时所有人档案（含 is_admin、头像）
--   对任意登录用户公开可读，权限面过宽。
-- 用法：Supabase → SQL Editor → 整段执行一次。
-- ============================================================

-- 1) 补齐双端代码实际使用/老段声明过的列（存在则跳过，零破坏）
alter table public.profiles add column if not exists display_name text default '';
alter table public.profiles add column if not exists bio          text;
alter table public.profiles add column if not exists search_count int default 0;
alter table public.profiles add column if not exists avatar_url   text;
alter table public.profiles add column if not exists is_admin     boolean default false;

-- 2) 收敛读取策略：去掉"全公开读"，改为仅本人可读（与 1.sql 第二段意图一致）。
--    如果你确有"公开个人主页"需求，请自行改回并只暴露 username/avatar_url 视图。
drop policy if exists "profiles_public_read" on public.profiles;
do $$ begin
  create policy "profiles_read_own" on public.profiles
    for select using (auth.uid() = id);
exception when duplicate_object then null; end $$;

-- 3) 写入策略补齐（只跑过第一段的库会缺 upsert/update 的 own 策略其一）
do $$ begin
  create policy "profiles_upsert_own" on public.profiles
    for insert with check (auth.uid() = id);
exception when duplicate_object then null; end $$;
do $$ begin
  create policy "profiles_update_own" on public.profiles
    for update using (auth.uid() = id);
exception when duplicate_object then null; end $$;

-- 4) 自检：执行后应返回 display_name / bio / search_count / avatar_url / is_admin 各一行
select column_name from information_schema.columns
where table_schema = 'public' and table_name = 'profiles'
  and column_name in ('display_name','bio','search_count','avatar_url','is_admin')
order by column_name;

-- 提醒（不在本脚本执行）：1.sql 末尾有一段把 2721985705@qq.com 密码重置为
-- 123456 的语句——那是你自己的运维便捷段，正式环境务必删除或改成强密码。
