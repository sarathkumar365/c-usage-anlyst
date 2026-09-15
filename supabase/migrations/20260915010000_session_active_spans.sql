-- Sessions record active spans; duration_seconds becomes active time rather than first-to-last wall clock.

alter table usage_sessions add column if not exists active_spans jsonb not null default '[]'::jsonb;

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
  s.completed_turns,
  s.active_spans
from usage_sessions s;
