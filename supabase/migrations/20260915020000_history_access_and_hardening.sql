-- History permission (data older than 30 days), access-management RPCs, enrollment attempt tracking,
-- and tighter grants: no anon table access, no client writes except admin-managed aliases.

alter table org_members add column if not exists can_view_history boolean not null default false;

create table if not exists enrollment_attempts (
  ip_hash text not null,
  attempted_at timestamptz not null default now(),
  succeeded boolean not null
);

alter table enrollment_attempts enable row level security;
create index if not exists enrollment_attempts_ip_idx on enrollment_attempts(ip_hash, attempted_at desc);

create or replace function can_view_history(target_org_id text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from org_members
    where org_id = target_org_id
      and user_id = auth.uid()
      and can_view_history
  );
$$;

-- Members see the last 30 days of collected data; older rows need the history permission.
drop policy if exists "members can read collector runs" on collector_runs;
create policy "members can read collector runs"
on collector_runs for select
using (is_org_member(org_id) and (can_view_history(org_id) or received_at >= now() - interval '30 days'));

drop policy if exists "members can read daily usage" on usage_daily;
create policy "members can read daily usage"
on usage_daily for select
using (is_org_member(org_id) and (can_view_history(org_id) or day >= current_date - 30));

drop policy if exists "members can read session usage" on usage_sessions;
create policy "members can read session usage"
on usage_sessions for select
using (is_org_member(org_id) and (can_view_history(org_id) or coalesce(last_ts, first_ts, updated_at) >= now() - interval '30 days'));

drop policy if exists "members can read anomalies" on usage_anomalies;
create policy "members can read anomalies"
on usage_anomalies for select
using (is_org_member(org_id) and (can_view_history(org_id) or last_seen_at >= now() - interval '30 days'));

drop policy if exists "members can read sources" on usage_sources;
create policy "members can read sources"
on usage_sources for select
using (is_org_member(org_id) and (can_view_history(org_id) or coalesce(latest_activity_at, updated_at) >= now() - interval '30 days'));

drop policy if exists "members can read activity daily" on usage_activity_daily;
create policy "members can read activity daily"
on usage_activity_daily for select
using (is_org_member(org_id) and (can_view_history(org_id) or day >= current_date - 30));

drop policy if exists "members can read claude accounts" on claude_accounts;
create policy "members can read claude accounts"
on claude_accounts for select
using (is_org_member(org_id) and (can_view_history(org_id) or last_seen_at >= now() - interval '30 days'));

drop policy if exists "members can read collector features" on collector_features;
create policy "members can read collector features"
on collector_features for select
using (is_org_member(org_id) and (can_view_history(org_id) or updated_at >= now() - interval '30 days'));

drop policy if exists "members can read plan usage samples" on plan_usage_samples;
create policy "members can read plan usage samples"
on plan_usage_samples for select
using (is_org_member(org_id) and (can_view_history(org_id) or sampled_at >= now() - interval '30 days'));

-- What the signed-in user may see, for the dashboard to offer only the ranges it can load.
create or replace function my_dashboard_access(target_org_id text)
returns table(role text, can_view_history boolean)
language sql
stable
security definer
set search_path = public
as $$
  select m.role, m.can_view_history
  from org_members m
  where m.org_id = target_org_id
    and m.user_id = auth.uid();
$$;

-- Admin-only: members with their email and history permission.
create or replace function org_access_list(target_org_id text)
returns table(user_id uuid, email text, role text, can_view_history boolean)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if not is_org_admin(target_org_id) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  return query
    select m.user_id, u.email::text, m.role, m.can_view_history
    from org_members m
    join auth.users u on u.id = m.user_id
    where m.org_id = target_org_id
    order by u.email;
end;
$$;

-- Admin-only: grant or remove the history permission.
create or replace function set_history_access(target_org_id text, target_user_id uuid, allowed boolean)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_org_admin(target_org_id) then
    raise exception 'not allowed' using errcode = '42501';
  end if;
  update org_members
  set can_view_history = allowed
  where org_id = target_org_id
    and user_id = target_user_id;
end;
$$;

-- Only enrolled collector tokens are accepted now; admin-issued shared tokens could write as anyone.
drop function if exists create_collector_token(text, text, text);

revoke all on all tables in schema public from anon;
revoke insert, update, delete, truncate, references, trigger on all tables in schema public from authenticated;
grant insert, update, delete on identity_aliases to authenticated;
alter default privileges in schema public revoke all on tables from anon;

revoke all on function is_org_member(text) from public, anon;
revoke all on function is_org_admin(text) from public, anon;
revoke all on function can_view_history(text) from public, anon;
revoke all on function my_dashboard_access(text) from public, anon;
revoke all on function org_access_list(text) from public, anon;
revoke all on function set_history_access(text, uuid, boolean) from public, anon;
grant execute on function is_org_member(text) to authenticated;
grant execute on function is_org_admin(text) to authenticated;
grant execute on function can_view_history(text) to authenticated;
grant execute on function my_dashboard_access(text) to authenticated;
grant execute on function org_access_list(text) to authenticated;
grant execute on function set_history_access(text, uuid, boolean) to authenticated;
