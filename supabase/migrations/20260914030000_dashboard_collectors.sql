-- One row per collector identity from its latest sync, so collectors that sync but find no usage still show up.
create or replace view dashboard_collectors
with (security_invoker = true)
as
select
  r.org_id,
  r.collector_id,
  r.machine_id,
  r.user_id,
  r.account_label,
  display_person(r.org_id, r.user_id, r.machine_id) as person_label,
  display_machine(r.org_id, r.machine_id) as machine_label,
  mu.os_username,
  m.hostname,
  m.platform,
  r.collector_version,
  r.received_at as last_sync_at,
  coalesce((r.summary ->> 'requests')::bigint, 0) as last_requests,
  coalesce((r.summary ->> 'transcript_files')::bigint, 0) as last_transcript_files
from (
  select distinct on (org_id, collector_id, machine_id, user_id, account_label)
    org_id, collector_id, machine_id, user_id, account_label, collector_version, received_at, summary
  from collector_runs
  order by org_id, collector_id, machine_id, user_id, account_label, received_at desc
) r
left join machine_users mu
  on mu.org_id = r.org_id and mu.user_id = r.user_id and mu.machine_id = r.machine_id
left join machines m
  on m.org_id = r.org_id and m.machine_id = r.machine_id;

revoke all on dashboard_collectors from anon, authenticated;
grant select on dashboard_collectors to authenticated;
