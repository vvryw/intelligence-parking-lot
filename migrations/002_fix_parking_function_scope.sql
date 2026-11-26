begin;
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

notify pgrst,'reload schema';
commit;
