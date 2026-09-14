-- Top tools per collector identity, from the most recent sync's drivers (the collector's sync window, not the dashboard range).
create or replace view dashboard_person_tools
with (security_invoker = true)
as
select
  r.org_id,
  r.collector_id,
  r.machine_id,
  r.user_id,
  r.account_label,
  t.tool,
  t.calls,
  r.period_start,
  r.period_end,
  r.received_at as reported_at
from (
  select distinct on (org_id, collector_id, machine_id, user_id, account_label)
    org_id, collector_id, machine_id, user_id, account_label, period_start, period_end, received_at, payload
  from collector_runs
  where payload ? 'drivers'
  order by org_id, collector_id, machine_id, user_id, account_label, received_at desc
) r
cross join lateral jsonb_to_recordset(coalesce(r.payload -> 'drivers' -> 'top_tools', '[]'::jsonb)) as t(tool text, calls bigint);

revoke all on dashboard_person_tools from anon, authenticated;
grant select on dashboard_person_tools to authenticated;
