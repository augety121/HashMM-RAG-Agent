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
