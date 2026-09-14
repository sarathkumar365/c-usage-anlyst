-- Display labels: an admin alias wins, then local metadata, then a short ID.
create or replace function display_person(p_org_id text, p_user_id text, p_machine_id text)
returns text
language sql
stable
set search_path = public
as $$
  select coalesce(
    (select nullif(display_name, '') from identity_aliases where org_id = p_org_id and entity_type = 'user' and entity_id = p_user_id),
    (select nullif(os_username, '') from machine_users where org_id = p_org_id and user_id = p_user_id and machine_id = p_machine_id),
    left(p_user_id, 10)
  );
$$;

create or replace function display_machine(p_org_id text, p_machine_id text)
returns text
language sql
stable
set search_path = public
as $$
  select coalesce(
    (select nullif(display_name, '') from identity_aliases where org_id = p_org_id and entity_type = 'machine' and entity_id = p_machine_id),
    (select nullif(hostname, '') from machines where org_id = p_org_id and machine_id = p_machine_id),
    left(p_machine_id, 10)
  );
$$;

create or replace function display_collector(p_org_id text, p_collector_id text, p_machine_id text)
returns text
language sql
stable
set search_path = public
as $$
  select coalesce(
    (select nullif(display_name, '') from identity_aliases where org_id = p_org_id and entity_type = 'collector' and entity_id = p_collector_id),
    (select nullif(hostname, '') from machines where org_id = p_org_id and machine_id = p_machine_id),
    left(p_collector_id, 10)
  );
$$;

create or replace function display_account(p_org_id text, p_account_label text)
returns text
language sql
stable
set search_path = public
as $$
  select coalesce(
    (select nullif(display_name, '') from identity_aliases where org_id = p_org_id and entity_type = 'account' and entity_id = p_account_label),
    nullif(p_account_label, ''),
    'Unlabeled account'
  );
$$;

create or replace view dashboard_people_usage
with (security_invoker = true)
as
with usage_by_identity as (
  select
    org_id,
    collector_id,
    machine_id,
    user_id,
    account_label,
    min(day) as first_usage_day,
    max(day) as last_usage_day,
    sum(requests)::bigint as requests,
    sum(reported_total)::bigint as reported_total,
    sum(input_tokens)::bigint as input_tokens,
    sum(output_tokens)::bigint as output_tokens,
    sum(cache_read_input_tokens + cache_creation_input_tokens)::bigint as cache_tokens,
    sum(tool_calls)::bigint as tool_calls
  from usage_daily
  group by org_id, collector_id, machine_id, user_id, account_label
),
latest_runs as (
  select distinct on (org_id, collector_id, machine_id, user_id, account_label)
    org_id,
    collector_id,
    machine_id,
    user_id,
    account_label,
    collector_version,
    received_at as last_sync_at
  from collector_runs
  order by org_id, collector_id, machine_id, user_id, account_label, received_at desc
)
select
  u.org_id,
  u.collector_id,
  u.machine_id,
  u.user_id,
  u.account_label,
  display_person(u.org_id, u.user_id, u.machine_id) as person_label,
  display_machine(u.org_id, u.machine_id) as machine_label,
  display_collector(u.org_id, u.collector_id, u.machine_id) as collector_label,
  display_account(u.org_id, u.account_label) as account_display_label,
  mu.os_username,
  m.hostname,
  m.fqdn,
  m.platform,
  u.first_usage_day,
  u.last_usage_day,
  lr.last_sync_at,
  lr.collector_version,
  u.requests,
  u.reported_total,
  u.input_tokens,
  u.output_tokens,
  u.cache_tokens,
  case when u.reported_total > 0 then u.cache_tokens::double precision / u.reported_total else 0 end as cache_share,
  u.tool_calls,
  coalesce(top_project.project_name, 'No project') as top_project,
  coalesce(top_model.model_label, 'Unknown model') as top_model,
  coalesce(anomaly_counts.open_anomalies, 0)::bigint as open_anomalies,
  exists (
    select 1
    from usage_anomalies ua
    where ua.org_id = u.org_id
      and ua.collector_id = u.collector_id
      and ua.machine_id = u.machine_id
      and ua.user_id = u.user_id
      and ua.code = 'stats_cache_missing'
  ) as stats_cache_missing
from usage_by_identity u
left join machine_users mu
  on mu.org_id = u.org_id and mu.user_id = u.user_id and mu.machine_id = u.machine_id
left join machines m
  on m.org_id = u.org_id and m.machine_id = u.machine_id
left join latest_runs lr
  on lr.org_id = u.org_id and lr.collector_id = u.collector_id and lr.machine_id = u.machine_id and lr.user_id = u.user_id and lr.account_label = u.account_label
left join lateral (
  select project_name
  from usage_daily d
  where d.org_id = u.org_id and d.collector_id = u.collector_id and d.machine_id = u.machine_id and d.user_id = u.user_id and d.account_label = u.account_label
  group by project_name
  order by sum(reported_total) desc
  limit 1
) top_project on true
left join lateral (
  select case when nullif(model, '') is null or model = '0' then 'Unknown model' else model end as model_label
  from usage_daily d
  where d.org_id = u.org_id and d.collector_id = u.collector_id and d.machine_id = u.machine_id and d.user_id = u.user_id and d.account_label = u.account_label
  group by model_label
  order by sum(reported_total) desc
  limit 1
) top_model on true
left join lateral (
  select count(*) as open_anomalies
  from usage_anomalies ua
  where ua.org_id = u.org_id and ua.collector_id = u.collector_id and ua.machine_id = u.machine_id and ua.user_id = u.user_id
) anomaly_counts on true;

create or replace view dashboard_user_project_usage
with (security_invoker = true)
as
select
  d.org_id,
  d.day,
  d.collector_id,
  d.machine_id,
  d.user_id,
  d.account_label,
  d.surface,
  d.confidence,
  display_person(d.org_id, d.user_id, d.machine_id) as person_label,
  display_machine(d.org_id, d.machine_id) as machine_label,
  display_account(d.org_id, d.account_label) as account_display_label,
  d.project,
  d.project_name,
  sum(d.requests)::bigint as requests,
  sum(d.reported_total)::bigint as reported_total,
  sum(d.input_tokens)::bigint as input_tokens,
  sum(d.output_tokens)::bigint as output_tokens,
  sum(d.cache_read_input_tokens + d.cache_creation_input_tokens)::bigint as cache_tokens,
  sum(d.tool_calls)::bigint as tool_calls
from usage_daily d
group by d.org_id, d.day, d.collector_id, d.machine_id, d.user_id, d.account_label, d.surface, d.confidence, d.project, d.project_name;

create or replace view dashboard_user_model_usage
with (security_invoker = true)
as
select
  d.org_id,
  d.day,
  d.collector_id,
  d.machine_id,
  d.user_id,
  d.account_label,
  d.surface,
  d.confidence,
  display_person(d.org_id, d.user_id, d.machine_id) as person_label,
  display_machine(d.org_id, d.machine_id) as machine_label,
  display_account(d.org_id, d.account_label) as account_display_label,
  case when nullif(d.model, '') is null or d.model = '0' then 'Unknown model' else d.model end as model_label,
  sum(d.requests)::bigint as requests,
  sum(d.reported_total)::bigint as reported_total,
  sum(d.input_tokens)::bigint as input_tokens,
  sum(d.output_tokens)::bigint as output_tokens,
  sum(d.cache_read_input_tokens + d.cache_creation_input_tokens)::bigint as cache_tokens,
  sum(d.web_search_requests)::bigint as web_search_requests,
  sum(d.web_fetch_requests)::bigint as web_fetch_requests
from usage_daily d
group by d.org_id, d.day, d.collector_id, d.machine_id, d.user_id, d.account_label, d.surface, d.confidence, model_label;

create or replace view dashboard_sessions
with (security_invoker = true)
as
select
  s.org_id,
  s.collector_id,
  s.machine_id,
  s.user_id,
  s.account_label,
  s.surface,
  s.confidence,
  display_person(s.org_id, s.user_id, s.machine_id) as person_label,
  display_machine(s.org_id, s.machine_id) as machine_label,
  display_account(s.org_id, s.account_label) as account_display_label,
  s.session_id,
  s.project,
  s.project_name,
  s.is_subagent,
  s.agent_id,
  s.first_ts,
  s.last_ts,
  s.duration_seconds,
  s.tokens_per_hour,
  s.requests,
  s.models,
  s.reported_total,
  s.cache_read_input_tokens + s.cache_creation_input_tokens as cache_tokens,
  s.tool_calls
from usage_sessions s;

create or replace view dashboard_sources
with (security_invoker = true)
as
select
  s.org_id,
  s.collector_id,
  s.machine_id,
  s.user_id,
  s.account_label,
  display_person(s.org_id, s.user_id, s.machine_id) as person_label,
  display_machine(s.org_id, s.machine_id) as machine_label,
  display_account(s.org_id, s.account_label) as account_display_label,
  s.source_id,
  s.surface,
  s.status,
  s.confidence,
  s.path_hash,
  s.file_count,
  s.latest_activity_at,
  s.extractor,
  s.anomalies,
  s.updated_at
from usage_sources s;

create or replace view dashboard_activity_daily
with (security_invoker = true)
as
select
  a.org_id,
  a.day,
  a.collector_id,
  a.machine_id,
  a.user_id,
  a.account_label,
  display_person(a.org_id, a.user_id, a.machine_id) as person_label,
  display_machine(a.org_id, a.machine_id) as machine_label,
  display_account(a.org_id, a.account_label) as account_display_label,
  a.surface,
  a.source_id,
  sum(a.sessions)::bigint as sessions,
  sum(a.messages)::bigint as messages,
  sum(a.turns)::bigint as turns,
  sum(a.tool_calls)::bigint as tool_calls,
  sum(a.reported_total)::bigint as reported_total,
  a.confidence
from usage_activity_daily a
group by a.org_id, a.day, a.collector_id, a.machine_id, a.user_id, a.account_label, a.surface, a.source_id, a.confidence;

create or replace view dashboard_anomalies
with (security_invoker = true)
as
select
  a.org_id,
  a.collector_id,
  a.machine_id,
  a.user_id,
  display_person(a.org_id, a.user_id, a.machine_id) as person_label,
  display_machine(a.org_id, a.machine_id) as machine_label,
  a.code,
  a.severity,
  a.message,
  a.last_seen_at
from usage_anomalies a;

drop view if exists candidate_dashboard_people_usage, candidate_dashboard_user_project_usage, candidate_dashboard_user_model_usage, candidate_dashboard_sessions, candidate_dashboard_sources, candidate_dashboard_activity_daily, candidate_dashboard_anomalies;
