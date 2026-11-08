begin;

create index if not exists bookings_user_schedule on public.bookings (user_id, start_time);

create or replace function smart_parking_private.user_schedule()
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  caller uuid := auth.uid();
  instant timestamptz := clock_timestamp();
  today date := (instant at time zone 'Europe/Bucharest')::date;
  midnight timestamptz := (today + 1)::timestamp at time zone 'Europe/Bucharest';
  spaces jsonb;
  intervals jsonb;
  bookings jsonb;
begin
  if caller is null or not exists (select 1 from public.users where id = caller) then
    raise sqlstate 'PT401' using message = 'Sign in required';
  end if;
  select coalesce(jsonb_agg(to_jsonb(s) order by s.map_y, s.map_x), '[]'::jsonb)
    into spaces from public.smart_parking_map() s;
  select coalesce(jsonb_agg(jsonb_build_object(
    'parking_space_id', b.parking_space_id, 'start_time', b.start_time, 'end_time', b.end_time
  ) order by b.start_time), '[]'::jsonb) into intervals
    from public.bookings b where b.status = 'confirmed'
      and b.end_time > instant and b.start_time < midnight;
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', b.id, 'parking_space_id', b.parking_space_id,
    'start_time', b.start_time, 'end_time', b.end_time,
    'actual_start', b.actual_start, 'actual_end', b.actual_end,
    'status', b.status, 'hourly_rate', s.hourly_rate,
    'estimated_total', round(extract(epoch from (b.end_time - b.start_time)) / 3600 * s.hourly_rate, 2)
  ) order by b.start_time desc), '[]'::jsonb) into bookings
    from public.bookings b join public.parking_spaces s on s.id = b.parking_space_id
    where b.user_id = caller;
  return jsonb_build_object('spaces', spaces, 'intervals', intervals, 'bookings', bookings,
    'schedule_date', today, 'server_time', instant, 'timezone', 'Europe/Bucharest');
end;
$$;

create or replace function smart_parking_private.user_action(
  p_action text, p_booking_id uuid default null, p_space_id integer default null,
  p_day date default null, p_start text default null, p_end text default null
)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  caller uuid := auth.uid();
  target public.parking_spaces%rowtype;
  booking public.bookings%rowtype;
  target_id integer;
  identifier uuid;
  instant timestamptz;
  today date;
  midnight timestamptz;
  starts_at timestamptz;
  ends_at timestamptz;
begin
  if caller is null then
    raise sqlstate 'PT401' using message = 'Sign in required';
  end if;
  perform id from public.users where id = caller for update;
  if not found then
    raise sqlstate 'PT401' using message = 'Profile not found';
  end if;
  if p_action is null or p_action not in ('reserve', 'cancel', 'start', 'finish') then
    raise sqlstate 'PT400' using message = 'Invalid booking action';
  end if;

  if p_action = 'reserve' then
    if p_space_id is null or p_space_id < 1 then
      raise sqlstate 'PT400' using message = 'Choose a parking space';
    end if;
    target_id := p_space_id;
  else
    select * into booking from public.bookings where id = p_booking_id and user_id = caller;
    if not found then
      raise sqlstate 'PT404' using message = 'Booking not found';
    end if;
    target_id := booking.parking_space_id;
  end if;

  select * into target from public.parking_spaces where id = target_id for update;
  if not found then
    raise sqlstate 'PT404' using message = 'Space not found';
  end if;
  if p_action <> 'reserve' then
    select * into booking from public.bookings
      where id = p_booking_id and user_id = caller for update;
    if not found then
      raise sqlstate 'PT404' using message = 'Booking not found';
    end if;
    if booking.status <> 'confirmed' then
      raise sqlstate 'PT409' using message = 'Booking already closed';
    end if;
  end if;

  instant := clock_timestamp();
  if p_action = 'reserve' then
    today := (instant at time zone 'Europe/Bucharest')::date;
    midnight := (today + 1)::timestamp at time zone 'Europe/Bucharest';
    if p_day is null or p_day <> today or p_start is null or p_end is null
       or p_start !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
       or p_end !~ '^(([01][0-9]|2[0-3]):[0-5][0-9]|24:00)$' then
      raise sqlstate 'PT400' using message = 'Choose times for today';
    end if;
    starts_at := (p_day + p_start::time) at time zone 'Europe/Bucharest';
    ends_at := case when p_end = '24:00' then midnight
      else (p_day + p_end::time) at time zone 'Europe/Bucharest' end;
    if starts_at < date_trunc('minute', instant) or ends_at <= starts_at
       or ends_at > midnight or ends_at <= instant then
      raise sqlstate 'PT400' using message = 'Choose times between now and midnight';
    end if;
    starts_at := greatest(starts_at, instant);
    if (target.occupied and starts_at <= instant) or exists (
      select 1 from public.bookings b where b.parking_space_id = target_id and b.status = 'confirmed'
        and b.start_time < ends_at and b.end_time > starts_at
    ) then
      raise sqlstate 'PT409' using message = 'Space unavailable for this interval';
    end if;
    insert into public.bookings (user_id, parking_space_id, start_time, end_time, status)
      values (caller, target_id, starts_at, ends_at, 'confirmed') returning id into identifier;
  elsif p_action = 'cancel' then
    if booking.actual_start is not null or booking.end_time <= instant then
      raise sqlstate 'PT409' using message = 'Booking cannot be cancelled';
    end if;
    update public.bookings set status = 'cancelled' where id = booking.id;
    identifier := booking.id;
  elsif p_action = 'start' then
    if booking.actual_start is not null and booking.actual_end is null then
      return jsonb_build_object('ok', true, 'booking_id', booking.id);
    end if;
    if booking.start_time > instant or booking.end_time <= instant or target.occupied
       or booking.actual_end is not null or exists (
         select 1 from public.bookings b where b.user_id = caller and b.status = 'confirmed'
           and b.actual_start is not null and b.actual_end is null
       ) or exists (
         select 1 from public.bookings b where b.parking_space_id = target_id
           and b.id <> booking.id and b.status = 'confirmed'
           and b.start_time <= instant and b.end_time > instant
       ) then
      raise sqlstate 'PT409' using message = 'Parking cannot be started';
    end if;
    update public.bookings set actual_start = instant where id = booking.id;
    update public.parking_spaces set occupied = true where id = target_id;
    identifier := booking.id;
  else
    if booking.actual_start is null or booking.actual_end is not null then
      raise sqlstate 'PT409' using message = 'No active parking to finish';
    end if;
    update public.bookings set actual_end = instant, status = 'completed' where id = booking.id;
    update public.parking_spaces set occupied = false where id = target_id;
    identifier := booking.id;
  end if;
  return jsonb_build_object('ok', true, 'booking_id', identifier);
end;
$$;

revoke all on function smart_parking_private.user_schedule() from public, anon;
revoke all on function smart_parking_private.user_action(text, uuid, integer, date, text, text) from public, anon;
grant usage on schema smart_parking_private to authenticated;
grant execute on function smart_parking_private.user_schedule() to authenticated;
grant execute on function smart_parking_private.user_action(text, uuid, integer, date, text, text) to authenticated;

create or replace function public.smart_parking_user_schedule()
returns jsonb
language sql security invoker set search_path = ''
as $$ select smart_parking_private.user_schedule(); $$;

create or replace function public.smart_parking_user_action(
  p_action text, p_booking_id uuid default null, p_space_id integer default null,
  p_day date default null, p_start text default null, p_end text default null
)
returns jsonb
language sql security invoker set search_path = ''
as $$ select smart_parking_private.user_action(p_action, p_booking_id, p_space_id, p_day, p_start, p_end); $$;

revoke all on function public.smart_parking_user_schedule() from public, anon;
revoke all on function public.smart_parking_user_action(text, uuid, integer, date, text, text) from public, anon;
grant execute on function public.smart_parking_user_schedule() to authenticated;
grant execute on function public.smart_parking_user_action(text, uuid, integer, date, text, text) to authenticated;

notify pgrst, 'reload schema';
commit;

