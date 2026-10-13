-- HashMM administrator-role repair and hardening.
-- Run once in the project's Supabase SQL Editor.

create or replace function public.is_admin()
returns boolean
language sql
stable
as $$
  select coalesce((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin', false);
$$;

update auth.users as u
set raw_app_meta_data = coalesce(u.raw_app_meta_data, '{}'::jsonb) || '{"role":"admin"}'::jsonb
from public.profiles as p
where p.id = u.id
  and coalesce(p.is_admin, false)
  and coalesce(u.raw_app_meta_data ->> 'role', '') <> 'admin';

create or replace function public.guard_profile_admin_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.is_admin is distinct from old.is_admin
     and coalesce(auth.role(), '') <> 'service_role'
     and not public.is_admin() then
    raise exception 'forbidden: only an administrator may change is_admin'
      using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists trg_guard_profile_admin_change on public.profiles;
create trigger trg_guard_profile_admin_change
before update on public.profiles
for each row execute function public.guard_profile_admin_change();
