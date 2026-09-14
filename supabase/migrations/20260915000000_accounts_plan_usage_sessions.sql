-- Claude account per collector, account-wide plan usage %, feature usage, and richer session context.

alter table usage_sessions add column if not exists git_branch text;
alter table usage_sessions add column if not exists entrypoints text[] not null default '{}';
alter table usage_sessions add column if not exists claude_code_version text;
alter table usage_sessions add column if not exists desktop_surface text;
alter table usage_sessions add column if not exists desktop_effort text;
alter table usage_sessions add column if not exists completed_turns integer;

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

alter table claude_accounts enable row level security;
alter table collector_features enable row level security;
alter table plan_usage_samples enable row level security;

create index if not exists claude_accounts_seen_idx on claude_accounts(org_id, last_seen_at desc);
create index if not exists plan_usage_samples_time_idx on plan_usage_samples(org_id, organization_uuid, sampled_at desc);

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

revoke all on dashboard_accounts from anon, authenticated;
revoke all on dashboard_features from anon, authenticated;
revoke all on dashboard_plan_usage from anon, authenticated;
grant select on dashboard_accounts to authenticated;
grant select on dashboard_features to authenticated;
grant select on dashboard_plan_usage to authenticated;
