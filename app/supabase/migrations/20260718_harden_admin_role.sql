-- HashMM administrator-role repair and hardening.
--
-- 1. Existing projects often marked the first account with profiles.is_admin
--    but did not copy that role into auth.users.raw_app_meta_data. HashMM's
--    backend authorizes from server-owned app_metadata, so backfill it here.
-- 2. profiles_self_update in early installs allowed updating every column,
--    including is_admin. Block non-admin clients from changing that column.

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

-- The App/backend reads authorization from app_metadata. Existing signed-in
-- clients do not need to be deleted: HashMM now refreshes current metadata from
-- /auth/v1/user when an otherwise-valid local JWT still says "user".
