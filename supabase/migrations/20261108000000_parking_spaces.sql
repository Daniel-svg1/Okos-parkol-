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

drop policy if exists "Signed in users read parking spaces" on public.parking_spaces;
create policy "Signed in users read parking spaces" on public.parking_spaces
  for select to authenticated using ((select auth.uid()) is not null);

insert into public.parking_spaces (id, label, occupied, hourly_rate, map_x, map_y)
values
  (1, 'A1', false, 5, 1, 1),
  (2, 'A2', true, 5, 2, 1),
  (3, 'A3', false, 5, 3, 1),
  (4, 'A4', false, 5, 4, 1),
  (5, 'A5', false, 5, 5, 1),
  (6, 'A6', true, 5, 6, 1),
  (7, 'B1', false, 5, 1, 2),
  (8, 'B2', true, 5, 2, 2),
  (9, 'B3', false, 5, 3, 2),
  (10, 'B4', false, 5, 4, 2),
  (11, 'B5', true, 5, 5, 2),
  (12, 'B6', false, 5, 6, 2)
on conflict do nothing;

commit;
