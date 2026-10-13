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
