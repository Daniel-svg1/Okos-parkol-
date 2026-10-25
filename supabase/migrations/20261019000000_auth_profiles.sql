-- A jelszavakat a Supabase Auth kezeli, nem a sajat tablank.
begin;

create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  name varchar(100) not null,
  email varchar(255) not null unique,
  role varchar(20) not null default 'user' check (role in ('user', 'admin'))
);

alter table public.users enable row level security;
revoke all on table public.users from anon, authenticated;
grant select on table public.users to authenticated;

drop policy if exists "Read own Smart Parking profile" on public.users;
create policy "Read own Smart Parking profile" on public.users
  for select to authenticated using ((select auth.uid()) = id);

create or replace function public.smart_parking_sync_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.users (id, name, email, role)
  values (
    new.id,
    left(coalesce(nullif(trim(new.raw_user_meta_data ->> 'full_name'), ''), 'User'), 100),
    new.email,
    'user'
  )
  on conflict (id) do update set name = excluded.name, email = excluded.email;
  return new;
end;
$$;

revoke all on function public.smart_parking_sync_user() from public, anon, authenticated;
drop trigger if exists smart_parking_user_profile on auth.users;
create trigger smart_parking_user_profile
  after insert or update of email, raw_user_meta_data on auth.users
  for each row execute function public.smart_parking_sync_user();

commit;
