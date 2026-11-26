-- Add the five-space parking ledger to the existing project. No tables/data dropped.
begin;
create table if not exists public.parking_spaces (
  id text primary key, kind text not null check (kind in ('vip','normal'))
);
insert into public.parking_spaces values
 ('VIP-1','vip'),('VIP-2','vip'),('NORMAL-1','normal'),('NORMAL-2','normal'),('NORMAL-3','normal')
on conflict (id) do nothing;
create table if not exists public.parking_media (
  id uuid primary key, object_path text unique not null, bytes integer not null,
  created_at timestamptz not null default now()
);
create table if not exists public.parking_observations (
  id uuid primary key, camera text not null check(camera in ('entrance','exit')),
  fingerprint text not null, photo_id uuid not null references public.parking_media(id),
  crop_id uuid references public.parking_media(id), recognition jsonb not null,
  created_at timestamptz not null default now()
);
-- Preserve and extend the table already created in this Supabase project.
create table if not exists public.parking_sessions (
  session_id uuid primary key default gen_random_uuid(), vehicle_type text not null,
  license_plate text not null, status text not null default 'still_in',
  entered_at timestamptz not null, exited_at timestamptz, image_path text not null
);
alter table public.parking_sessions add column if not exists space_id text references public.parking_spaces(id);
alter table public.parking_sessions add column if not exists entrance_observation_id uuid references public.parking_observations(id);
alter table public.parking_sessions add column if not exists exit_observation_id uuid references public.parking_observations(id);
create unique index if not exists parking_one_active_plate on public.parking_sessions(license_plate) where status='still_in';
create unique index if not exists parking_one_active_space on public.parking_sessions(space_id) where status='still_in';
create table if not exists public.parking_events (
  id uuid primary key default gen_random_uuid(), observation_id uuid unique not null references public.parking_observations(id),
  request_id text unique not null, camera text not null, plate text, vehicle_type text,
  space_id text references public.parking_spaces(id), session_id uuid references public.parking_sessions(session_id),
  approved boolean not null, reason text not null, created_at timestamptz not null default now()
);
create table if not exists public.parking_gate_commands (
  id uuid primary key default gen_random_uuid(), event_id uuid unique not null references public.parking_events(id),
  camera text not null, command text not null check(command='OPEN'), mode text not null check(mode='simulation'),
  created_at timestamptz not null default now()
);
create table if not exists public.parking_space_sensors (
  space_id text primary key references public.parking_spaces(id), occupied boolean not null,
  source text not null, observed_at timestamptz not null
);

create or replace function public.parking_validate_space() returns trigger language plpgsql set search_path=public as $$
begin
  if new.space_id is not null and not exists (select 1 from public.parking_spaces b where b.id=new.space_id
    and ((b.kind='normal' and new.vehicle_type='car') or (b.kind='vip' and new.vehicle_type in ('sportcar','supercar'))))
  then raise exception 'Vehicle type cannot use this space'; end if;
  return new;
end $$;
create or replace trigger parking_validate_space before insert or update of space_id,vehicle_type
on public.parking_sessions for each row execute function public.parking_validate_space();

create or replace function public.parking_observation(p_id uuid) returns jsonb
language sql stable set search_path=public as $$
 select to_jsonb(o) || jsonb_build_object('photo_url','/parking/media/'||o.photo_id,
  'crop_url',case when o.crop_id is not null then '/parking/media/'||o.crop_id end)
 from public.parking_observations o where o.id=p_id
$$;

create or replace function public.parking_create_observation(
 p_id uuid,p_camera text,p_fingerprint text,p_recognition jsonb,p_media jsonb
) returns jsonb language plpgsql set search_path=public as $$
declare m jsonb; photo uuid; crop uuid;
begin
 if p_camera not in ('entrance','exit') then raise exception 'Unsupported camera'; end if;
 for m in select * from jsonb_array_elements(p_media) loop
  insert into public.parking_media(id,object_path,bytes) values ((m->>'id')::uuid,m->>'object_path',(m->>'bytes')::integer);
  if m->>'kind'='photo' then photo=(m->>'id')::uuid; else crop=(m->>'id')::uuid; end if;
 end loop;
 insert into public.parking_observations(id,camera,fingerprint,photo_id,crop_id,recognition)
 values(p_id,p_camera,p_fingerprint,photo,crop,p_recognition);
 return public.parking_observation(p_id);
end $$;

create or replace function public.parking_commit_event(p_observation_id uuid,p_request_id text,p_require_sensors boolean default false)
returns jsonb language plpgsql set search_path=public as $$
declare o public.parking_observations%rowtype; e public.parking_events%rowtype;
 s public.parking_sessions%rowtype; r jsonb; plate text; vehicle text; v_kind text;
 bay text; visit uuid; allowed boolean=false; reason text='recognition_needs_review'; replay boolean=false;
begin
 -- One transaction-wide lock protects the whole five-space allocation decision.
 perform pg_advisory_xact_lock(884321,1);
 select * into e from public.parking_events where observation_id=p_observation_id or request_id=p_request_id limit 1;
 if found then
  if e.observation_id<>p_observation_id then raise exception 'Request ID was already used for a different observation'; end if;
  replay=true;
 else
  select * into o from public.parking_observations where id=p_observation_id;
  if not found then raise exception 'Observation not found'; end if;
  r=o.recognition; plate=upper(nullif(r->>'license_plate','')); vehicle=r->>'vehicle_type';
  if r->>'eligible'='true' and plate is not null then
   select * into s from public.parking_sessions where license_plate=plate and status='still_in';
   if o.camera='exit' then
    if s.session_id is null then reason='no_active_session';
    else
     visit=s.session_id; bay=s.space_id; vehicle=s.vehicle_type;
     update public.parking_sessions set status='left',exited_at=now(),exit_observation_id=o.id where session_id=visit;
     allowed=true; reason='exit_registered';
    end if;
   elsif s.session_id is not null then
    visit=s.session_id; bay=s.space_id; reason='already_inside';
   else
    v_kind=case when vehicle='car' then 'normal' when vehicle in ('sportcar','supercar') then 'vip' end;
    if v_kind is null then reason='unsupported_vehicle_type';
    else
     select b.id into bay from public.parking_spaces b where b.kind=v_kind
      and not exists(select 1 from public.parking_sessions a where a.space_id=b.id and a.status='still_in')
      and not exists(select 1 from public.parking_space_sensors x where x.space_id=b.id and (x.occupied or x.observed_at<now()-interval '15 seconds'))
      and (not p_require_sensors or exists(select 1 from public.parking_space_sensors x where x.space_id=b.id and not x.occupied and x.observed_at>=now()-interval '15 seconds'))
      order by b.id limit 1;
     if bay is null then reason=v_kind||'_full';
     else
      visit=gen_random_uuid();
      insert into public.parking_sessions(session_id,vehicle_type,license_plate,status,entered_at,image_path,space_id,entrance_observation_id)
       select visit,vehicle,plate,'still_in',now(),m.object_path,bay,o.id from public.parking_media m where m.id=o.photo_id;
      allowed=true; reason='entry_registered';
     end if;
    end if;
   end if;
  end if;
  insert into public.parking_events(observation_id,request_id,camera,plate,vehicle_type,space_id,session_id,approved,reason)
   values(o.id,p_request_id,o.camera,plate,vehicle,bay,visit,allowed,reason) returning * into e;
  if allowed then insert into public.parking_gate_commands(event_id,camera,command,mode) values(e.id,o.camera,'OPEN','simulation'); end if;
 end if;
 return to_jsonb(e)||jsonb_build_object('replayed',replay,'gate',jsonb_build_object(
  'mode','simulation','action',case when e.approved then 'open' else 'keep_closed' end,
  'command_created',e.approved and not replay,'physical_gate_opened',false));
end $$;

create or replace function public.parking_current_state(p_require_sensors boolean default false) returns jsonb
language sql stable set search_path=public as $$
with bays as (
 select b.id,b.kind,s.session_id,s.license_plate plate,s.vehicle_type,s.entered_at,
  x.occupied sensor_occupied,x.observed_at sensor_observed_at,
  case when s.session_id is not null then 'occupied' when x.occupied then 'sensor_occupied'
    when x.observed_at<now()-interval '15 seconds' then 'sensor_stale'
    when p_require_sensors and x.space_id is null then 'sensor_unknown' else 'free' end status
 from public.parking_spaces b left join public.parking_sessions s on s.space_id=b.id and s.status='still_in'
 left join public.parking_space_sensors x on x.space_id=b.id
), counts as (
 select kind,jsonb_build_object('total',count(*),'occupied',count(*) filter(where status in ('occupied','sensor_occupied')),
  'available',count(*) filter(where status='free'),'unavailable',count(*) filter(where status in ('sensor_stale','sensor_unknown'))) value from bays group by kind
)
select jsonb_build_object('capacity',(select jsonb_object_agg(kind,value) from counts),
 'spaces',(select jsonb_agg(to_jsonb(bays) order by kind desc,id) from bays),
 'database','ready','storage','supabase','gate_mode','simulation','occupancy_source','registered_entries_and_exits_with_optional_sensors',
 'archived_observations',(select count(*) from public.parking_observations),'archived_images',(select count(*) from public.parking_media))
$$;

create or replace function public.parking_history(p_limit integer default 30) returns jsonb
language sql stable set search_path=public as $$
select jsonb_build_object(
 'events',coalesce((select jsonb_agg(v order by v->>'created_at' desc) from (
  select to_jsonb(e)||jsonb_build_object('photo_url','/parking/media/'||o.photo_id) v
  from public.parking_events e join public.parking_observations o on o.id=e.observation_id
  order by e.created_at desc limit least(100,greatest(1,p_limit))) a),'[]'::jsonb),
 'sessions',coalesce((select jsonb_agg(v order by v->>'entered_at' desc) from (
  select to_jsonb(s)||jsonb_build_object('id',s.session_id,'plate',s.license_plate,
   'entrance_photo_url','/parking/media/'||o.photo_id,'exit_photo_url',case when x.id is not null then '/parking/media/'||x.photo_id end) v
  from public.parking_sessions s left join public.parking_observations o on o.id=s.entrance_observation_id
  left join public.parking_observations x on x.id=s.exit_observation_id
  order by s.entered_at desc limit least(100,greatest(1,p_limit))) a),'[]'::jsonb))
$$;

create or replace function public.parking_record_sensors(p_updates jsonb,p_source text) returns jsonb
language plpgsql set search_path=public as $$
declare u jsonb; stamp timestamptz; n integer=0;
begin
 if length(p_source) not between 1 and 64 or jsonb_array_length(p_updates) not between 1 and 5 then raise exception 'Invalid sensor batch'; end if;
 for u in select * from jsonb_array_elements(p_updates) loop
  if not exists(select 1 from public.parking_spaces where id=u->>'space_id') or jsonb_typeof(u->'occupied')<>'boolean'
   then raise exception 'Invalid space or occupancy'; end if;
  stamp=(u->>'observed_at')::timestamptz;
  if stamp is null or stamp>now()+interval '30 seconds' then raise exception 'Invalid sensor timestamp'; end if;
  insert into public.parking_space_sensors values(u->>'space_id',(u->>'occupied')::boolean,p_source,stamp)
   on conflict(space_id) do update set occupied=excluded.occupied,source=excluded.source,observed_at=excluded.observed_at
   where excluded.observed_at>public.parking_space_sensors.observed_at;
  n=n+1;
 end loop;
 return jsonb_build_object('accepted',n);
end $$;

-- Backend-only Data API. No public browser key can read plates, photos or issue decisions.
alter table public.parking_spaces enable row level security;
alter table public.parking_media enable row level security;
alter table public.parking_observations enable row level security;
alter table public.parking_sessions enable row level security;
alter table public.parking_events enable row level security;
alter table public.parking_gate_commands enable row level security;
alter table public.parking_space_sensors enable row level security;
revoke all on public.parking_spaces,public.parking_media,public.parking_observations,public.parking_sessions,
 public.parking_events,public.parking_gate_commands,public.parking_space_sensors from anon,authenticated;
grant all on public.parking_spaces,public.parking_media,public.parking_observations,public.parking_sessions,
 public.parking_events,public.parking_gate_commands,public.parking_space_sensors to service_role;
revoke all on function public.parking_observation(uuid),public.parking_create_observation(uuid,text,text,jsonb,jsonb),
 public.parking_commit_event(uuid,text,boolean),public.parking_current_state(boolean),public.parking_history(integer),
 public.parking_record_sensors(jsonb,text) from public,anon,authenticated;
grant execute on function public.parking_observation(uuid),public.parking_create_observation(uuid,text,text,jsonb,jsonb),
 public.parking_commit_event(uuid,text,boolean),public.parking_current_state(boolean),public.parking_history(integer),
 public.parking_record_sensors(jsonb,text) to service_role;
insert into storage.buckets(id,name,public) values('intelligence-parking-lot','intelligence-parking-lot',false) on conflict(id) do nothing;
notify pgrst,'reload schema';
commit;
