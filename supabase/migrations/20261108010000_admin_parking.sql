begin;

create table if not exists public.parking_spaces (
  id integer primary key check (id > 0),
  label varchar(10) not null unique check (length(trim(label)) > 0),
  occupied boolean not null default false,
  hourly_rate numeric(8,2) not null default 5 check (hourly_rate >= 0),
  map_x integer not null check (map_x between 1 and 6),
  map_y integer not null check (map_y between 1 and 99),
  unique (map_x, map_y)
);

alter table public.parking_spaces enable row level security;
revoke all on table public.parking_spaces from anon, authenticated;
grant select on table public.parking_spaces to authenticated;
grant select, update on table public.parking_spaces to service_role;

drop policy if exists "Signed in users read parking spaces" on public.parking_spaces;
create policy "Signed in users read parking spaces" on public.parking_spaces
  for select to authenticated using ((select auth.uid()) is not null);

insert into public.parking_spaces (id, label, occupied, hourly_rate, map_x, map_y)
values
  (1, 'A1', false, 5, 1, 1), (2, 'A2', true, 5, 2, 1),
  (3, 'A3', false, 5, 3, 1), (4, 'A4', false, 5, 4, 1),
  (5, 'A5', false, 5, 5, 1), (6, 'A6', true, 5, 6, 1),
  (7, 'B1', false, 5, 1, 2), (8, 'B2', true, 5, 2, 2),
  (9, 'B3', false, 5, 3, 2), (10, 'B4', false, 5, 4, 2),
  (11, 'B5', true, 5, 5, 2), (12, 'B6', false, 5, 6, 2)
on conflict do nothing;

create table if not exists public.bookings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete set null,
  parking_space_id integer not null references public.parking_spaces(id),
  start_time timestamptz not null,
  end_time timestamptz not null,
  actual_start timestamptz,
  actual_end timestamptz,
  status varchar(20) not null default 'confirmed'
    check (status in ('confirmed', 'completed', 'cancelled')),
  check (end_time > start_time)
);

create index if not exists bookings_active_space
  on public.bookings (parking_space_id, start_time, end_time)
  where status = 'confirmed';

alter table public.bookings enable row level security;
revoke all on table public.bookings from anon, authenticated;
grant select on table public.bookings to authenticated;
grant select, insert, update on table public.bookings to service_role;

drop policy if exists "Users read own bookings" on public.bookings;
create policy "Users read own bookings" on public.bookings
  for select to authenticated using ((select auth.uid()) = user_id);

create or replace function public.smart_parking_map()
returns table (
  id integer, label varchar, occupied boolean, hourly_rate numeric,
  map_x integer, map_y integer, reserved boolean, reserved_until timestamptz
)
language sql volatile security definer set search_path = ''
as $$
  with moment as materialized (select clock_timestamp() as at)
  select s.id, s.label, s.occupied, s.hourly_rate, s.map_x, s.map_y,
         b.end_time is not null, b.end_time
  from public.parking_spaces s cross join moment t
  left join lateral (
    select r.end_time from public.bookings r
    where r.parking_space_id = s.id and r.status = 'confirmed'
      and r.start_time <= t.at and r.end_time > t.at
    order by r.end_time limit 1
  ) b on true
  order by s.map_y, s.map_x;
$$;

revoke all on function public.smart_parking_map() from public, anon;
grant execute on function public.smart_parking_map() to authenticated, service_role;

create schema if not exists smart_parking_private;
revoke all on schema smart_parking_private from public, anon, authenticated;
create table if not exists smart_parking_private.admin_config (
  singleton boolean primary key default true check (singleton),
  secret_hash text not null check (length(secret_hash) = 64)
);
alter table smart_parking_private.admin_config enable row level security;
revoke all on table smart_parking_private.admin_config from public, anon, authenticated;

create or replace function public.smart_parking_admin_map(p_server_key text)
returns table (
  id integer, label varchar, occupied boolean, hourly_rate numeric,
  map_x integer, map_y integer, reserved boolean, reserved_until timestamptz
)
language plpgsql volatile security definer set search_path = ''
as $$
begin
  if p_server_key is null or length(p_server_key) > 256 or not exists (
    select 1 from smart_parking_private.admin_config c
    where c.secret_hash = encode(sha256(convert_to(p_server_key, 'UTF8')), 'hex')
  ) then
    raise sqlstate 'PT403' using message = 'Admin server access required';
  end if;
  return query select * from public.smart_parking_map();
end;
$$;

revoke all on function public.smart_parking_admin_map(text) from public;
grant execute on function public.smart_parking_admin_map(text) to anon, authenticated, service_role;

create or replace function public.smart_parking_admin_action(
  p_action text, p_space_id integer, p_source_id integer default null,
  p_minutes integer default 60, p_server_key text default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  target public.parking_spaces%rowtype;
  source public.parking_spaces%rowtype;
  action_time timestamptz;
begin
  if p_server_key is null or length(p_server_key) > 256 or not exists (
    select 1 from smart_parking_private.admin_config c
    where c.secret_hash = encode(sha256(convert_to(p_server_key, 'UTF8')), 'hex')
  ) then
    raise sqlstate 'PT403' using message = 'Admin server access required';
  end if;
  if p_action is null or p_action not in ('occupy', 'free', 'reserve', 'move')
     or p_space_id is null or p_space_id < 1 then
    raise sqlstate '22023' using message = 'Invalid parking action';
  end if;
  if p_action = 'reserve' and (p_minutes is null or p_minutes < 1 or p_minutes > 1440) then
    raise sqlstate '22023' using message = 'Invalid reservation duration';
  end if;
  if p_action = 'move' and
     (p_source_id is null or p_source_id < 1 or p_source_id = p_space_id) then
    raise sqlstate '22023' using message = 'Invalid source space';
  end if;

  perform s.id from public.parking_spaces s
    where s.id = p_space_id or (p_action = 'move' and s.id = p_source_id)
    order by s.id for update;

  select * into target from public.parking_spaces where id = p_space_id;
  if not found then
    raise sqlstate 'PT404' using message = 'Space not found';
  end if;
  action_time := clock_timestamp();

  if p_action = 'free' then
    update public.parking_spaces set occupied = false where id = p_space_id;
    update public.bookings set status = 'cancelled'
      where parking_space_id = p_space_id and status = 'confirmed'
        and start_time <= action_time and end_time > action_time;
  else
    if target.occupied or exists (
      select 1 from public.bookings r
      where r.parking_space_id = p_space_id and r.status = 'confirmed'
        and r.start_time < case when p_action = 'reserve'
          then action_time + make_interval(mins => p_minutes) else action_time end
        and r.end_time > action_time
    ) then
      if p_action = 'occupy' and target.occupied then
        return jsonb_build_object('ok', true);
      end if;
      raise sqlstate 'PT409' using message = 'Space unavailable';
    end if;
    if p_action = 'reserve' then
      insert into public.bookings (user_id, parking_space_id, start_time, end_time, status)
      values (null, p_space_id, action_time,
              action_time + make_interval(mins => p_minutes), 'confirmed');
    elsif p_action = 'move' then
      select * into source from public.parking_spaces where id = p_source_id;
      if not found or not source.occupied then
        raise sqlstate 'PT409' using message = 'Source car no longer available';
      end if;
      update public.parking_spaces set occupied = (id = p_space_id)
        where id in (p_space_id, p_source_id);
    else
      update public.parking_spaces set occupied = true where id = p_space_id;
    end if;
  end if;
  return jsonb_build_object('ok', true);
end;
$$;

revoke all on function public.smart_parking_admin_action(text, integer, integer, integer, text)
  from public, anon, authenticated;
grant execute on function public.smart_parking_admin_action(text, integer, integer, integer, text)
  to anon, authenticated, service_role;

notify pgrst, 'reload schema';
commit;

