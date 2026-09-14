create table if not exists usage_sources (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  account_label text not null default '',
  source_id text not null,
  surface text not null,
  status text not null,
  confidence text not null,
  path_hash text not null,
  file_count bigint not null default 0,
  latest_activity_at timestamptz,
  extractor text not null default '',
  anomalies jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, account_label, source_id)
);

create table if not exists usage_activity_daily (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  account_label text not null default '',
  day date not null,
  surface text not null,
  source_id text not null,
  sessions integer not null default 0,
  messages integer not null default 0,
  turns integer not null default 0,
  tool_calls integer not null default 0,
  reported_total bigint not null default 0,
  confidence text not null default 'evidence',
  updated_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, account_label, day, surface, source_id)
);

alter table usage_daily add column if not exists surface text not null default 'claude_code';
alter table usage_daily add column if not exists confidence text not null default 'exact';
alter table usage_sessions add column if not exists surface text not null default 'claude_code';
alter table usage_sessions add column if not exists confidence text not null default 'exact';

alter table usage_sources enable row level security;
alter table usage_activity_daily enable row level security;

create index if not exists usage_sources_seen_idx on usage_sources(org_id, latest_activity_at desc);
create index if not exists usage_activity_daily_org_day_idx on usage_activity_daily(org_id, day desc);

drop policy if exists "members can read sources" on usage_sources;
create policy "members can read sources"
on usage_sources for select
using (is_org_member(org_id));

drop policy if exists "members can read activity daily" on usage_activity_daily;
create policy "members can read activity daily"
on usage_activity_daily for select
using (is_org_member(org_id));

drop view if exists dashboard_user_project_usage;
drop view if exists dashboard_user_model_usage;
drop view if exists dashboard_sessions;

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
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(d.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(d.machine_id, 10)) as machine_label,
  coalesce(nullif(account_alias.display_name, ''), nullif(d.account_label, ''), 'Unlabeled account') as account_display_label,
  d.project,
  d.project_name,
  sum(d.requests)::bigint as requests,
  sum(d.reported_total)::bigint as reported_total,
  sum(d.input_tokens)::bigint as input_tokens,
  sum(d.output_tokens)::bigint as output_tokens,
  sum(d.cache_read_input_tokens + d.cache_creation_input_tokens)::bigint as cache_tokens,
  sum(d.tool_calls)::bigint as tool_calls
from usage_daily d
left join machine_users mu
  on mu.org_id = d.org_id and mu.user_id = d.user_id and mu.machine_id = d.machine_id
left join machines m
  on m.org_id = d.org_id and m.machine_id = d.machine_id
left join identity_aliases user_alias
  on user_alias.org_id = d.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = d.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = d.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = d.machine_id
left join identity_aliases account_alias
  on account_alias.org_id = d.org_id and account_alias.entity_type = 'account' and account_alias.entity_id = d.account_label
group by d.org_id, d.day, d.collector_id, d.machine_id, d.user_id, d.account_label, d.surface, d.confidence, person_label, machine_label, account_display_label, d.project, d.project_name;

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
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(d.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(d.machine_id, 10)) as machine_label,
  coalesce(nullif(account_alias.display_name, ''), nullif(d.account_label, ''), 'Unlabeled account') as account_display_label,
  case when nullif(d.model, '') is null or d.model = '0' then 'Unknown model' else d.model end as model_label,
  sum(d.requests)::bigint as requests,
  sum(d.reported_total)::bigint as reported_total,
  sum(d.input_tokens)::bigint as input_tokens,
  sum(d.output_tokens)::bigint as output_tokens,
  sum(d.cache_read_input_tokens + d.cache_creation_input_tokens)::bigint as cache_tokens,
  sum(d.web_search_requests)::bigint as web_search_requests,
  sum(d.web_fetch_requests)::bigint as web_fetch_requests
from usage_daily d
left join machine_users mu
  on mu.org_id = d.org_id and mu.user_id = d.user_id and mu.machine_id = d.machine_id
left join machines m
  on m.org_id = d.org_id and m.machine_id = d.machine_id
left join identity_aliases user_alias
  on user_alias.org_id = d.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = d.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = d.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = d.machine_id
left join identity_aliases account_alias
  on account_alias.org_id = d.org_id and account_alias.entity_type = 'account' and account_alias.entity_id = d.account_label
group by d.org_id, d.day, d.collector_id, d.machine_id, d.user_id, d.account_label, d.surface, d.confidence, person_label, machine_label, account_display_label, model_label;

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
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(s.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(s.machine_id, 10)) as machine_label,
  coalesce(nullif(account_alias.display_name, ''), nullif(s.account_label, ''), 'Unlabeled account') as account_display_label,
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
from usage_sessions s
left join machine_users mu
  on mu.org_id = s.org_id and mu.user_id = s.user_id and mu.machine_id = s.machine_id
left join machines m
  on m.org_id = s.org_id and m.machine_id = s.machine_id
left join identity_aliases user_alias
  on user_alias.org_id = s.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = s.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = s.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = s.machine_id
left join identity_aliases account_alias
  on account_alias.org_id = s.org_id and account_alias.entity_type = 'account' and account_alias.entity_id = s.account_label;

create or replace view dashboard_sources
with (security_invoker = true)
as
select
  s.org_id,
  s.collector_id,
  s.machine_id,
  s.user_id,
  s.account_label,
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(s.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(s.machine_id, 10)) as machine_label,
  coalesce(nullif(account_alias.display_name, ''), nullif(s.account_label, ''), 'Unlabeled account') as account_display_label,
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
from usage_sources s
left join machine_users mu
  on mu.org_id = s.org_id and mu.user_id = s.user_id and mu.machine_id = s.machine_id
left join machines m
  on m.org_id = s.org_id and m.machine_id = s.machine_id
left join identity_aliases user_alias
  on user_alias.org_id = s.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = s.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = s.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = s.machine_id
left join identity_aliases account_alias
  on account_alias.org_id = s.org_id and account_alias.entity_type = 'account' and account_alias.entity_id = s.account_label;

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
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(a.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(a.machine_id, 10)) as machine_label,
  coalesce(nullif(account_alias.display_name, ''), nullif(a.account_label, ''), 'Unlabeled account') as account_display_label,
  a.surface,
  a.source_id,
  sum(a.sessions)::bigint as sessions,
  sum(a.messages)::bigint as messages,
  sum(a.turns)::bigint as turns,
  sum(a.tool_calls)::bigint as tool_calls,
  sum(a.reported_total)::bigint as reported_total,
  a.confidence
from usage_activity_daily a
left join machine_users mu
  on mu.org_id = a.org_id and mu.user_id = a.user_id and mu.machine_id = a.machine_id
left join machines m
  on m.org_id = a.org_id and m.machine_id = a.machine_id
left join identity_aliases user_alias
  on user_alias.org_id = a.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = a.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = a.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = a.machine_id
left join identity_aliases account_alias
  on account_alias.org_id = a.org_id and account_alias.entity_type = 'account' and account_alias.entity_id = a.account_label
group by a.org_id, a.day, a.collector_id, a.machine_id, a.user_id, a.account_label, person_label, machine_label, account_display_label, a.surface, a.source_id, a.confidence;

revoke all on dashboard_user_project_usage from anon, authenticated;
revoke all on dashboard_user_model_usage from anon, authenticated;
revoke all on dashboard_sessions from anon, authenticated;
revoke all on dashboard_sources from anon, authenticated;
revoke all on dashboard_activity_daily from anon, authenticated;
grant select on dashboard_user_project_usage to authenticated;
grant select on dashboard_user_model_usage to authenticated;
grant select on dashboard_sessions to authenticated;
grant select on dashboard_sources to authenticated;
grant select on dashboard_activity_daily to authenticated;

revoke execute on function create_collector_token(text, text, text) from public, anon, authenticated;
grant execute on function create_collector_token(text, text, text) to service_role;

create table if not exists enrollment_secrets (
  secret_hash text primary key,
  org_id text not null references organizations(id) on delete cascade,
  label text,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);

alter table enrollment_secrets enable row level security;
revoke all on enrollment_secrets from anon, authenticated;
