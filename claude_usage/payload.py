"""Pure assembly of the metrics-only upload payload from already-collected data. No I/O."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from claude_usage.constants import APP_VERSION
from claude_usage.discovery import SourceRecord
from claude_usage.metrics import group_sum, token_breakdown
from claude_usage.models import AccountSnapshot, ActivityDaily, DesktopSession, FeatureUsage, PlanUsageSample, RequestUsage, SessionSummary
from claude_usage.paths import ClaudePaths
from claude_usage.transcripts import version_key
from claude_usage.ui import friendly_tool_name, short_project_name
from claude_usage.util import date_key, stable_hash


def build_sync_payload(
    *,
    claude: ClaudePaths,
    config: dict[str, Any],
    identity: dict[str, Any],
    requests: list[RequestUsage],
    sessions: dict[str, SessionSummary],
    stats: dict[str, Any] | None,
    stats_cache_present: bool,
    transcript_files: int,
    transcript_digest: str,
    period_start: datetime | None,
    period_end: datetime | None,
    anomalies: list[dict[str, str]],
    sources: list[SourceRecord] | None = None,
    activity_daily: list[ActivityDaily] | None = None,
    sync_scope: dict[str, Any] | None = None,
    accounts: list[AccountSnapshot] | None = None,
    install: dict[str, Any] | None = None,
    feature_usage: list[FeatureUsage] | None = None,
    plan_usage: list[PlanUsageSample] | None = None,
    desktop_sessions: list[DesktopSession] | None = None,
) -> dict[str, Any]:
    duplicate_records = sum(max(0, r.duplicate_records - 1) for r in requests)

    daily_rows = []
    daily_buckets: dict[tuple[str, str, str], list[RequestUsage]] = defaultdict(list)
    for r in requests:
        daily_buckets[(date_key(r.timestamp), r.project, r.model)].append(r)
    for (day, project, model), rows in sorted(daily_buckets.items()):
        daily_rows.append({
            "day": day,
            "surface": rows[0].surface,
            "confidence": "exact",
            "project": project,
            "project_name": short_project_name(project),
            "model": model,
            "requests": len(rows),
            "finalized_requests": sum(r.finalized for r in rows),
            "incomplete_requests": sum(not r.finalized for r in rows),
            **token_breakdown(rows),
        })

    desktop_by_id = {d.cli_session_id: d for d in desktop_sessions or []}
    session_rows = []
    for s in sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True):
        desktop = None if s.is_subagent else desktop_by_id.get(s.session_id)
        session_rows.append({
            "session_id": s.session_id,
            "surface": s.surface,
            "confidence": "exact",
            "project": s.project,
            "project_name": short_project_name(s.project),
            "is_subagent": s.is_subagent,
            "agent_id": s.agent_id,
            "first_ts": s.first_ts,
            "last_ts": s.last_ts,
            "duration_seconds": s.duration_seconds,
            "tokens_per_hour": s.tokens_per_hour,
            "requests": s.requests,
            "finalized_requests": s.finalized_requests,
            "incomplete_requests": s.incomplete_requests,
            "models": s.models,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "cache_read_input_tokens": s.cache_read,
            "cache_creation_input_tokens": s.cache_creation,
            "cache_5m_tokens": s.cache_5m,
            "cache_1h_tokens": s.cache_1h,
            "reported_total": s.reported_total,
            "tool_calls": s.tool_calls,
            "git_branch": s.git_branch,
            "entrypoints": list(s.entrypoints),
            "claude_code_version": s.claude_code_version,
            "desktop_surface": desktop.surface if desktop else None,
            "desktop_effort": desktop.effort if desktop else None,
            "completed_turns": desktop.completed_turns if desktop else None,
            "active_spans": [list(span) for span in s.active_spans],
        })

    tool_counter = Counter()
    for r in requests:
        for name in r.tool_names:
            tool_counter[friendly_tool_name(name)] += 1

    total = sum(r.reported_total for r in requests)
    by_project = sorted(group_sum(requests, lambda r: r.project).items(), key=lambda x: x[1], reverse=True)
    by_model = sorted(group_sum(requests, lambda r: r.model).items(), key=lambda x: x[1], reverse=True)

    drivers = {
        "top_projects": [
            {
                "project": project,
                "project_name": short_project_name(project),
                "tokens": tokens,
                "share": tokens / total if total else 0.0,
            }
            for project, tokens in by_project[:10]
        ],
        "top_models": [
            {"model": model, "tokens": tokens, "share": tokens / total if total else 0.0}
            for model, tokens in by_model[:10]
        ],
        "top_tools": [
            {"tool": tool, "calls": calls}
            for tool, calls in tool_counter.most_common(10)
        ],
        "top_sessions": [
            {
                "session_id": s.session_id,
                "project_name": short_project_name(s.project),
                "tokens": s.reported_total,
                "share": s.reported_total / total if total else 0.0,
            }
            for s in sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True)[:10]
        ],
    }

    sources = sources or []
    activity_daily = activity_daily or []
    versions = sorted({r.claude_code_version for r in requests if r.claude_code_version}, key=version_key)
    install = {**(install or {}), "claude_code_version": versions[-1] if versions else None}

    payload_core = {
        "schema_version": 2,
        "collector_version": APP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "org_id": config.get("org_id", ""),
        "identity": identity,
        "claude": {
            "config_dir_hash": stable_hash(str(claude.claude_dir)),
            "projects_dir_hash": stable_hash(str(claude.projects_dir)),
            "stats_cache_present": stats_cache_present,
            "stats_cache_last_computed_date": stats.get("lastComputedDate") if stats else None,
            "config_source": claude.source,
        },
        "summary": {
            "requests": len(requests),
            "sessions": len(sessions),
            "transcript_files": transcript_files,
            "sources": len(sources),
            "activity_days": len(activity_daily),
            "finalized_requests": sum(r.finalized for r in requests),
            "incomplete_requests": sum(not r.finalized for r in requests),
            "duplicate_records_removed": duplicate_records,
            **token_breakdown(requests),
        },
        "daily": daily_rows,
        "sessions": session_rows,
        "sources": [source.to_payload() for source in sources],
        "activity_daily": [asdict(row) for row in activity_daily],
        "drivers": drivers,
        "anomalies": anomalies,
        "accounts": [asdict(a) for a in accounts or []],
        "install": install,
        "feature_usage": [asdict(f) for f in feature_usage or []],
        "plan_usage": [asdict(sample) for sample in plan_usage or []],
    }
    payload_core["summary"]["accounts"] = len(payload_core["accounts"])
    payload_core["summary"]["plan_usage_samples"] = len(payload_core["plan_usage"])
    payload_core["transcript_digest"] = transcript_digest
    payload_core["idempotency_key"] = stable_hash({
        "collector_id": identity["collector_id"],
        "machine_id": identity["machine_id"],
        "user_id": identity["user_id"],
        # Period bounds are derived from now(), so including them would make every run unique.
        "sync_scope": sync_scope or {},
        "transcript_digest": payload_core["transcript_digest"],
        "source_digest": stable_hash(payload_core["sources"]),
        "activity_digest": stable_hash(payload_core["activity_daily"]),
        "account_digest": stable_hash([payload_core["accounts"], payload_core["install"], payload_core["feature_usage"]]),
        "plan_usage_digest": stable_hash(payload_core["plan_usage"]),
    })
    return payload_core
