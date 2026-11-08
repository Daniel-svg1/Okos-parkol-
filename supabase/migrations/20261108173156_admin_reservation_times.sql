begin;

create or replace function public.smart_parking_admin_schedule(p_server_key text)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  instant timestamptz := clock_timestamp();
  local_day date := (instant at time zone 'Europe/Bucharest')::date;
  midnight timestamptz := (local_day + 1)::timestamp at time zone 'Europe/Bucharest';
  spaces jsonb;
  reservations jsonb;
begin
  if p_server_key is null or length(p_server_key) > 256 or not exists (
    select 1 from smart_parking_private.admin_config c
    where c.secret_hash = encode(sha256(convert_to(p_server_key, 'UTF8')), 'hex')
  ) then
    raise sqlstate 'PT403' using message = 'Admin server access required';
  end if;
  select coalesce(jsonb_agg(to_jsonb(s) order by s.map_y, s.map_x), '[]'::jsonb)
    into spaces from public.smart_parking_map() s;
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', r.id, 'parking_space_id', r.parking_space_id,
    'start_time', r.start_time, 'end_time', r.end_time
  ) order by r.start_time, r.id), '[]'::jsonb)
    into reservations from public.bookings r
    where r.status = 'confirmed' and r.end_time > instant and r.start_time < midnight;
  return jsonb_build_object('spaces', spaces, 'reservations', reservations,
    'schedule_date', local_day, 'server_time', instant, 'timezone', 'Europe/Bucharest');
end;
$$;

revoke all on function public.smart_parking_admin_schedule(text) from public;
grant execute on function public.smart_parking_admin_schedule(text) to anon, authenticated, service_role;

drop function if exists public.smart_parking_admin_action(text, integer, integer, integer, text);

create or replace function public.smart_parking_admin_action(
  p_action text, p_space_id integer, p_source_id integer default null,
  p_server_key text default null, p_day date default null,
  p_start text default null, p_end text default null, p_booking_id uuid default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  target public.parking_spaces%rowtype;
  source public.parking_spaces%rowtype;
  action_time timestamptz;
  local_day date;
  starts_at timestamptz;
  ends_at timestamptz;
  midnight timestamptz;
begin
  if p_server_key is null or length(p_server_key) > 256 or not exists (
    select 1 from smart_parking_private.admin_config c
    where c.secret_hash = encode(sha256(convert_to(p_server_key, 'UTF8')), 'hex')
  ) then
    raise sqlstate 'PT403' using message = 'Admin server access required';
  end if;
  if p_action is null or p_action not in ('occupy', 'free', 'reserve', 'move', 'cancel')
     or p_space_id is null or p_space_id < 1 then
    raise sqlstate 'PT400' using message = 'Invalid parking action';
  end if;
  if p_action = 'move' and
     (p_source_id is null or p_source_id < 1 or p_source_id = p_space_id) then
    raise sqlstate 'PT400' using message = 'Invalid source space';
  end if;
  perform s.id from public.parking_spaces s
    where s.id = p_space_id or (p_action = 'move' and s.id = p_source_id)
    order by s.id for update;

  select * into target from public.parking_spaces where id = p_space_id;
  if not found then
    raise sqlstate 'PT404' using message = 'Space not found';
  end if;
  action_time := clock_timestamp();

  if p_action = 'reserve' then
    local_day := (action_time at time zone 'Europe/Bucharest')::date;
    midnight := (local_day + 1)::timestamp at time zone 'Europe/Bucharest';
    if p_day is null or p_day <> local_day or p_start is null or p_end is null
       or p_start !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
       or p_end !~ '^(([01][0-9]|2[0-3]):[0-5][0-9]|24:00)$' then
      raise sqlstate 'PT400' using message = 'Choose times for today';
    end if;
    starts_at := (p_day + p_start::time) at time zone 'Europe/Bucharest';
    ends_at := case when p_end = '24:00' then midnight
      else (p_day + p_end::time) at time zone 'Europe/Bucharest' end;
    if starts_at < date_trunc('minute', action_time) or ends_at <= starts_at
       or ends_at > midnight or ends_at <= action_time then
      raise sqlstate 'PT400' using message = 'Choose times between now and midnight';
    end if;
    starts_at := greatest(starts_at, action_time);
    if (target.occupied and starts_at <= action_time) or exists (
      select 1 from public.bookings r
      where r.parking_space_id = p_space_id and r.status = 'confirmed'
        and r.start_time < ends_at and r.end_time > starts_at
    ) then
      raise sqlstate 'PT409' using message = 'Reservation overlaps or space is occupied';
    end if;
    insert into public.bookings (user_id, parking_space_id, start_time, end_time, status)
      values (null, p_space_id, starts_at, ends_at, 'confirmed');
  elsif p_action = 'cancel' then
    update public.bookings set status = 'cancelled'
      where id = p_booking_id and parking_space_id = p_space_id
        and status = 'confirmed' and end_time > action_time;
    if not found then
      raise sqlstate 'PT409' using message = 'Reservation no longer available';
    end if;
  elsif p_action = 'free' then
    update public.parking_spaces set occupied = false where id = p_space_id;
    update public.bookings set status = 'cancelled'
      where parking_space_id = p_space_id and status = 'confirmed'
        and start_time <= action_time and end_time > action_time;
  else
    if p_action = 'occupy' and target.occupied then
      return jsonb_build_object('ok', true);
    end if;
    if target.occupied or exists (
      select 1 from public.bookings r
      where r.parking_space_id = p_space_id and r.status = 'confirmed'
        and r.start_time <= action_time and r.end_time > action_time
    ) then
      raise sqlstate 'PT409' using message = 'Space unavailable';
    end if;
    if p_action = 'move' then
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

revoke all on function public.smart_parking_admin_action(text, integer, integer, text, date, text, text, uuid)
  from public;
grant execute on function public.smart_parking_admin_action(text, integer, integer, text, date, text, text, uuid)
  to anon, authenticated, service_role;

notify pgrst, 'reload schema';
commit;

