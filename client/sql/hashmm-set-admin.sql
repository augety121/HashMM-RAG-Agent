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
where email = 'admin@example.com';

-- 确认结果（应能看到 role: admin）
select email, raw_app_meta_data ->> 'role' as role
from auth.users
where email = 'admin@example.com';

-- ── 如需再设别的管理员，把邮箱换掉再跑一次即可 ──
-- update auth.users
-- set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
-- where email = '另一个邮箱@example.com';

-- ── 如需取消某账号的管理员 ──
-- update auth.users
-- set raw_app_meta_data = raw_app_meta_data - 'role'
-- where email = 'admin@example.com';

-- ============================================================
-- 完成。该账号重新登录后，客户端 / App 里即为管理员。
-- ============================================================
