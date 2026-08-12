-- HashMM 零配置：全局 app_config 表，存后端公网地址等。
-- 后端启动时用 service key 写入 backend_url；App 登录后读取，实现零配置。
-- 幂等，可重复执行。

create table if not exists public.app_config (
  key        text primary key,
  value      text not null default '',
  updated_at timestamptz not null default now()
);

alter table public.app_config enable row level security;

-- 已登录用户只能读取非敏感的后端发现信息。
drop policy if exists "app_config_read" on public.app_config;
create policy "app_config_read" on public.app_config
  for select to authenticated using (
    key in ('backend_url', 'backend_protocol', 'backend_region', 'backend_updated_at')
  );

-- 清理旧版本可能写入的客户端共享模型凭据。
delete from public.app_config where key = 'direct_llm';

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
