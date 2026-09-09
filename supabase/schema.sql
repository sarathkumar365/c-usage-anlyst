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

alter table organizations enable row level security;
alter table org_members enable row level security;
alter table collector_tokens enable row level security;
alter table machines enable row level security;
alter table machine_users enable row level security;
alter table collector_runs enable row level security;
alter table usage_daily enable row level security;
alter table usage_sessions enable row level security;
alter table usage_anomalies enable row level security;

create index if not exists usage_daily_org_day_idx on usage_daily(org_id, day desc);
create index if not exists usage_daily_user_idx on usage_daily(org_id, user_id, day desc);
create index if not exists usage_sessions_total_idx on usage_sessions(org_id, reported_total desc);
create index if not exists usage_anomalies_seen_idx on usage_anomalies(org_id, last_seen_at desc);

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
