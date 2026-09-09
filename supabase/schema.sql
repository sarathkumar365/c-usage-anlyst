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

create table if not exists identity_aliases (
  org_id text not null references organizations(id) on delete cascade,
  entity_type text not null check (entity_type in ('user', 'machine', 'collector', 'account')),
  entity_id text not null,
  display_name text not null,
  notes text,
  updated_at timestamptz not null default now(),
  primary key (org_id, entity_type, entity_id)
);

alter table organizations enable row level security;
alter table org_members enable row level security;
alter table collector_tokens enable row level security;
alter table machines enable row level security;
alter table machine_users enable row level security;
alter table collector_runs enable row level security;
alter table usage_daily enable row level security;
alter table usage_sessions enable row level security;
alter table usage_anomalies enable row level security;
alter table identity_aliases enable row level security;

create index if not exists usage_daily_org_day_idx on usage_daily(org_id, day desc);
create index if not exists usage_daily_user_idx on usage_daily(org_id, user_id, day desc);
create index if not exists usage_sessions_total_idx on usage_sessions(org_id, reported_total desc);
create index if not exists usage_anomalies_seen_idx on usage_anomalies(org_id, last_seen_at desc);
create index if not exists collector_runs_identity_seen_idx on collector_runs(org_id, user_id, machine_id, collector_id, received_at desc);

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

create policy "members can read organizations"
on organizations for select
using (is_org_member(id));

create policy "members can read org membership"
on org_members for select
using (is_org_member(org_id));

create policy "members can read machines"
on machines for select
using (is_org_member(org_id));

create policy "members can read machine users"
on machine_users for select
using (is_org_member(org_id));

create policy "members can read collector runs"
on collector_runs for select
using (is_org_member(org_id));

create policy "members can read daily usage"
on usage_daily for select
using (is_org_member(org_id));

create policy "members can read session usage"
on usage_sessions for select
using (is_org_member(org_id));

create policy "members can read anomalies"
on usage_anomalies for select
using (is_org_member(org_id));

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
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(u.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(u.machine_id, 10)) as machine_label,
  coalesce(nullif(collector_alias.display_name, ''), nullif(m.hostname, ''), left(u.collector_id, 10)) as collector_label,
  coalesce(nullif(account_alias.display_name, ''), nullif(u.account_label, ''), 'Unlabeled account') as account_display_label,
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
left join identity_aliases user_alias
  on user_alias.org_id = u.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = u.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = u.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = u.machine_id
left join identity_aliases collector_alias
  on collector_alias.org_id = u.org_id and collector_alias.entity_type = 'collector' and collector_alias.entity_id = u.collector_id
left join identity_aliases account_alias
  on account_alias.org_id = u.org_id and account_alias.entity_type = 'account' and account_alias.entity_id = u.account_label
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
group by d.org_id, d.day, d.collector_id, d.machine_id, d.user_id, d.account_label, person_label, machine_label, account_display_label, d.project, d.project_name;

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
group by d.org_id, d.day, d.collector_id, d.machine_id, d.user_id, d.account_label, person_label, machine_label, account_display_label, model_label;

create or replace view dashboard_sessions
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

create or replace view dashboard_anomalies
with (security_invoker = true)
as
select
  a.org_id,
  a.collector_id,
  a.machine_id,
  a.user_id,
  coalesce(nullif(user_alias.display_name, ''), nullif(mu.os_username, ''), left(a.user_id, 10)) as person_label,
  coalesce(nullif(machine_alias.display_name, ''), nullif(m.hostname, ''), left(a.machine_id, 10)) as machine_label,
  a.code,
  a.severity,
  a.message,
  a.last_seen_at
from usage_anomalies a
left join machine_users mu
  on mu.org_id = a.org_id and mu.user_id = a.user_id and mu.machine_id = a.machine_id
left join machines m
  on m.org_id = a.org_id and m.machine_id = a.machine_id
left join identity_aliases user_alias
  on user_alias.org_id = a.org_id and user_alias.entity_type = 'user' and user_alias.entity_id = a.user_id
left join identity_aliases machine_alias
  on machine_alias.org_id = a.org_id and machine_alias.entity_type = 'machine' and machine_alias.entity_id = a.machine_id;

revoke all on identity_aliases from anon, authenticated;
revoke all on dashboard_people_usage from anon, authenticated;
revoke all on dashboard_user_project_usage from anon, authenticated;
revoke all on dashboard_user_model_usage from anon, authenticated;
revoke all on dashboard_sessions from anon, authenticated;
revoke all on dashboard_anomalies from anon, authenticated;

grant select, insert, update, delete on identity_aliases to authenticated;
grant select on dashboard_people_usage to authenticated;
grant select on dashboard_user_project_usage to authenticated;
grant select on dashboard_user_model_usage to authenticated;
grant select on dashboard_sessions to authenticated;
grant select on dashboard_anomalies to authenticated;

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
