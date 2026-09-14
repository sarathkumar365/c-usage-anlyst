-- Canonical schema for the Claude usage collector. Safe to re-run on an existing database.
-- Incremental changes for already-deployed databases live in supabase/migrations/.

-- ============================================================================
-- Tables
-- ============================================================================

create extension if not exists pgcrypto;

create table if not exists organizations (
  id text primary key,
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists org_members (
  org_id text not null references organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'viewer',
  created_at timestamptz not null default now(),
  primary key (org_id, user_id)
);

create table if not exists collector_tokens (
  token_hash text primary key,
  org_id text not null references organizations(id) on delete cascade,
  label text,
  collector_id text,
  machine_id text,
  user_id text,
  enrolled_at timestamptz not null default now(),
  last_used_at timestamptz,
  enrollment_ip_hash text,
  revoked_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists enrollment_secrets (
  secret_hash text primary key,
  org_id text not null references organizations(id) on delete cascade,
  label text,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);

create table if not exists machines (
  org_id text not null references organizations(id) on delete cascade,
  machine_id text not null,
  hostname text,
  fqdn text,
  platform jsonb not null default '{}'::jsonb,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  primary key (org_id, machine_id)
);

create table if not exists machine_users (
  org_id text not null references organizations(id) on delete cascade,
  user_id text not null,
  machine_id text not null,
  os_username text,
  home_path_hash text,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  primary key (org_id, user_id, machine_id)
);

create table if not exists collector_runs (
  org_id text not null references organizations(id) on delete cascade,
  idempotency_key text primary key,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  account_label text not null default '',
  collector_version text,
  period_start timestamptz,
  period_end timestamptz,
  transcript_digest text,
  summary jsonb not null default '{}'::jsonb,
  payload jsonb not null,
  received_at timestamptz not null default now()
);

create table if not exists usage_daily (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  account_label text not null default '',
  day date not null,
  surface text not null default 'claude_code',
  confidence text not null default 'exact',
  project text not null,
  project_name text not null,
  model text not null,
  requests integer not null default 0,
  finalized_requests integer not null default 0,
  incomplete_requests integer not null default 0,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  cache_read_input_tokens bigint not null default 0,
  cache_creation_input_tokens bigint not null default 0,
  cache_5m_tokens bigint not null default 0,
  cache_1h_tokens bigint not null default 0,
  reported_total bigint not null default 0,
  tool_calls integer not null default 0,
  web_search_requests integer not null default 0,
  web_fetch_requests integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, account_label, day, project, model)
);

create table if not exists usage_sessions (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  account_label text not null default '',
  session_id text not null,
  surface text not null default 'claude_code',
  confidence text not null default 'exact',
  project text not null,
  project_name text not null,
  is_subagent boolean not null default false,
  agent_id text,
  first_ts timestamptz,
  last_ts timestamptz,
  duration_seconds double precision not null default 0,
  tokens_per_hour double precision not null default 0,
  requests integer not null default 0,
  finalized_requests integer not null default 0,
  incomplete_requests integer not null default 0,
  models jsonb not null default '{}'::jsonb,
  input_tokens bigint not null default 0,
  output_tokens bigint not null default 0,
  cache_read_input_tokens bigint not null default 0,
  cache_creation_input_tokens bigint not null default 0,
  cache_5m_tokens bigint not null default 0,
  cache_1h_tokens bigint not null default 0,
  reported_total bigint not null default 0,
  tool_calls integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, account_label, session_id)
);

create table if not exists usage_anomalies (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  code text not null,
  severity text not null,
  message text not null,
  last_seen_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, code)
);

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
alter table usage_sessions add column if not exists git_branch text;
alter table usage_sessions add column if not exists entrypoints text[] not null default '{}';
alter table usage_sessions add column if not exists claude_code_version text;
alter table usage_sessions add column if not exists desktop_surface text;
alter table usage_sessions add column if not exists desktop_effort text;
alter table usage_sessions add column if not exists completed_turns integer;

create table if not exists identity_aliases (
  org_id text not null references organizations(id) on delete cascade,
  entity_type text not null check (entity_type in ('user', 'machine', 'collector', 'account')),
  entity_id text not null,
  display_name text not null,
  notes text,
  updated_at timestamptz not null default now(),
  primary key (org_id, entity_type, entity_id)
);

create table if not exists claude_accounts (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  account_uuid text not null,
  organization_uuid text,
  organization_name text,
  email_hash text,
  billing_type text,
  seat_tier text,
  user_rate_limit_tier text,
  organization_rate_limit_tier text,
  has_extra_usage boolean,
  source text not null default '',
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, account_uuid)
);

create table if not exists collector_features (
  org_id text not null references organizations(id) on delete cascade,
  collector_id text not null,
  machine_id text not null,
  user_id text not null,
  kind text not null,
  name text not null,
  count integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (org_id, collector_id, machine_id, user_id, kind, name)
);

create table if not exists plan_usage_samples (
  org_id text not null references organizations(id) on delete cascade,
  organization_uuid text not null,
  collector_id text not null,
  source text not null,
  sampled_at timestamptz not null,
  machine_id text not null,
  user_id text not null,
  five_hour_pct numeric,
  seven_day_pct numeric,
  extra_usage numeric,
  five_hour_resets_at timestamptz,
  seven_day_resets_at timestamptz,
  primary key (org_id, organization_uuid, collector_id, source, sampled_at)
);

-- ============================================================================
-- Row level security and indexes
-- ============================================================================

alter table organizations enable row level security;
alter table org_members enable row level security;
alter table collector_tokens enable row level security;
alter table enrollment_secrets enable row level security;
alter table machines enable row level security;
alter table machine_users enable row level security;
alter table collector_runs enable row level security;
alter table usage_daily enable row level security;
alter table usage_sessions enable row level security;
alter table usage_anomalies enable row level security;
alter table usage_sources enable row level security;
alter table usage_activity_daily enable row level security;
alter table identity_aliases enable row level security;
alter table claude_accounts enable row level security;
alter table collector_features enable row level security;
alter table plan_usage_samples enable row level security;

create index if not exists usage_daily_org_day_idx on usage_daily(org_id, day desc);
create index if not exists usage_daily_user_idx on usage_daily(org_id, user_id, day desc);
create index if not exists usage_sessions_total_idx on usage_sessions(org_id, reported_total desc);
create index if not exists usage_anomalies_seen_idx on usage_anomalies(org_id, last_seen_at desc);
create index if not exists usage_sources_seen_idx on usage_sources(org_id, latest_activity_at desc);
create index if not exists usage_activity_daily_org_day_idx on usage_activity_daily(org_id, day desc);
create index if not exists collector_runs_identity_seen_idx on collector_runs(org_id, user_id, machine_id, collector_id, received_at desc);
create index if not exists claude_accounts_seen_idx on claude_accounts(org_id, last_seen_at desc);
create index if not exists plan_usage_samples_time_idx on plan_usage_samples(org_id, organization_uuid, sampled_at desc);

-- ============================================================================
-- Access helpers and policies (members read; admins manage aliases; no client writes)
-- ============================================================================

create or replace function is_org_member(target_org_id text)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1
    from org_members
    where org_id = target_org_id
      and user_id = auth.uid()
  );
$$;

create or replace function is_org_admin(target_org_id text)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1
    from org_members
    where org_id = target_org_id
      and user_id = auth.uid()
      and role in ('admin', 'owner')
  );
$$;

drop policy if exists "members can read organizations" on organizations;
create policy "members can read organizations"
on organizations for select
using (is_org_member(id));

drop policy if exists "members can read org membership" on org_members;
create policy "members can read org membership"
on org_members for select
using (is_org_member(org_id));

drop policy if exists "members can read machines" on machines;
create policy "members can read machines"
on machines for select
using (is_org_member(org_id));

drop policy if exists "members can read machine users" on machine_users;
create policy "members can read machine users"
on machine_users for select
using (is_org_member(org_id));

drop policy if exists "members can read collector runs" on collector_runs;
create policy "members can read collector runs"
on collector_runs for select
using (is_org_member(org_id));

drop policy if exists "members can read daily usage" on usage_daily;
create policy "members can read daily usage"
on usage_daily for select
using (is_org_member(org_id));

drop policy if exists "members can read session usage" on usage_sessions;
create policy "members can read session usage"
on usage_sessions for select
using (is_org_member(org_id));

drop policy if exists "members can read anomalies" on usage_anomalies;
create policy "members can read anomalies"
on usage_anomalies for select
using (is_org_member(org_id));

drop policy if exists "members can read sources" on usage_sources;
create policy "members can read sources"
on usage_sources for select
using (is_org_member(org_id));

drop policy if exists "members can read activity daily" on usage_activity_daily;
create policy "members can read activity daily"
on usage_activity_daily for select
using (is_org_member(org_id));

drop policy if exists "members can read identity aliases" on identity_aliases;
create policy "members can read identity aliases"
on identity_aliases for select
using (is_org_member(org_id));

drop policy if exists "admins can insert identity aliases" on identity_aliases;
create policy "admins can insert identity aliases"
on identity_aliases for insert
with check (is_org_admin(org_id));

drop policy if exists "admins can update identity aliases" on identity_aliases;
create policy "admins can update identity aliases"
on identity_aliases for update
using (is_org_admin(org_id))
with check (is_org_admin(org_id));

drop policy if exists "admins can delete identity aliases" on identity_aliases;
create policy "admins can delete identity aliases"
on identity_aliases for delete
using (is_org_admin(org_id));

drop policy if exists "members can read claude accounts" on claude_accounts;
create policy "members can read claude accounts"
on claude_accounts for select
using (is_org_member(org_id));

drop policy if exists "members can read collector features" on collector_features;
create policy "members can read collector features"
on collector_features for select
using (is_org_member(org_id));

drop policy if exists "members can read plan usage samples" on plan_usage_samples;
create policy "members can read plan usage samples"
on plan_usage_samples for select
using (is_org_member(org_id));

-- ============================================================================
-- Dashboard read model
-- ============================================================================

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
  s.tool_calls,
  s.git_branch,
  s.entrypoints,
  s.claude_code_version,
  s.desktop_surface,
  s.desktop_effort,
  s.completed_turns
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
  coalesce((r.summary ->> 'transcript_files')::bigint, 0) as last_transcript_files,
  r.payload -> 'install' ->> 'install_method' as install_method,
  r.payload -> 'install' ->> 'claude_code_version' as claude_code_version
from (
  select distinct on (org_id, collector_id, machine_id, user_id, account_label)
    org_id, collector_id, machine_id, user_id, account_label, collector_version, received_at, summary, payload
  from collector_runs
  order by org_id, collector_id, machine_id, user_id, account_label, received_at desc
) r
left join machine_users mu
  on mu.org_id = r.org_id and mu.user_id = r.user_id and mu.machine_id = r.machine_id
left join machines m
  on m.org_id = r.org_id and m.machine_id = r.machine_id;

-- Claude accounts each collector identity is signed in to.
create or replace view dashboard_accounts
with (security_invoker = true)
as
select
  a.org_id,
  a.collector_id,
  a.machine_id,
  a.user_id,
  a.account_uuid,
  a.organization_uuid,
  a.organization_name,
  a.email_hash,
  a.billing_type,
  a.user_rate_limit_tier,
  a.organization_rate_limit_tier,
  a.has_extra_usage,
  a.source,
  a.first_seen_at,
  a.last_seen_at
from claude_accounts a;

create or replace view dashboard_features
with (security_invoker = true)
as
select org_id, collector_id, machine_id, user_id, kind, name, count, updated_at
from collector_features;

-- Account-wide usage % in 5-minute buckets; every machine on the account reports the same series.
create or replace view dashboard_plan_usage
with (security_invoker = true)
as
select
  org_id,
  organization_uuid,
  to_timestamp(floor(extract(epoch from sampled_at) / 300) * 300) as bucket_at,
  max(five_hour_pct) as five_hour_pct,
  max(seven_day_pct) as seven_day_pct,
  max(extra_usage) as extra_usage,
  max(five_hour_resets_at) as five_hour_resets_at,
  max(seven_day_resets_at) as seven_day_resets_at,
  array_agg(distinct collector_id) as collector_ids,
  array_agg(distinct source) as sources
from plan_usage_samples
group by org_id, organization_uuid, to_timestamp(floor(extract(epoch from sampled_at) / 300) * 300);

-- ============================================================================
-- Grants
-- ============================================================================

revoke all on identity_aliases from anon, authenticated;
revoke all on enrollment_secrets from anon, authenticated;
revoke all on dashboard_people_usage from anon, authenticated;
revoke all on dashboard_user_project_usage from anon, authenticated;
revoke all on dashboard_user_model_usage from anon, authenticated;
revoke all on dashboard_sessions from anon, authenticated;
revoke all on dashboard_anomalies from anon, authenticated;
revoke all on dashboard_sources from anon, authenticated;
revoke all on dashboard_activity_daily from anon, authenticated;
revoke all on dashboard_person_tools from anon, authenticated;
revoke all on dashboard_collectors from anon, authenticated;
revoke all on dashboard_accounts from anon, authenticated;
revoke all on dashboard_features from anon, authenticated;
revoke all on dashboard_plan_usage from anon, authenticated;

grant select, insert, update, delete on identity_aliases to authenticated;
grant select on dashboard_people_usage to authenticated;
grant select on dashboard_user_project_usage to authenticated;
grant select on dashboard_user_model_usage to authenticated;
grant select on dashboard_sessions to authenticated;
grant select on dashboard_anomalies to authenticated;
grant select on dashboard_sources to authenticated;
grant select on dashboard_activity_daily to authenticated;
grant select on dashboard_person_tools to authenticated;
grant select on dashboard_collectors to authenticated;
grant select on dashboard_accounts to authenticated;
grant select on dashboard_features to authenticated;
grant select on dashboard_plan_usage to authenticated;

-- ============================================================================
-- Admin functions (service role only)
-- ============================================================================

create or replace function create_collector_token(
  p_org_id text,
  p_label text,
  p_plain_token text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into collector_tokens(token_hash, org_id, label)
  values (encode(digest(p_plain_token, 'sha256'), 'hex'), p_org_id, p_label)
  on conflict (token_hash) do update
  set label = excluded.label,
      revoked_at = null;
end;
$$;

-- Functions in public are executable by PUBLIC by default, which exposes this
-- security definer function to anon/authenticated callers via PostgREST RPC.
revoke execute on function create_collector_token(text, text, text) from public, anon, authenticated;
grant execute on function create_collector_token(text, text, text) to service_role;
