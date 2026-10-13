-- HashMM App: order history by the newest real message.
-- Idempotent: safe to run again after deployment.

begin;

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

commit;
