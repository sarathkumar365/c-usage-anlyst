"""Sync flow: collect usage + discover sources -> build payload -> upload -> record outcome."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from claude_usage.accounts import read_accounts, read_feature_usage, read_install_info
from claude_usage.activity import extract_activity_daily
from claude_usage.anomalies import discover_anomalies, plan_usage_anomalies, retention_anomalies, sanitize_sync_anomalies
from claude_usage.desktop_sessions import desktop_session_activity, read_desktop_sessions
from claude_usage.discovery import evidence_exists
from claude_usage.flows.collect import UsageQuery, collect_usage
from claude_usage.flows.discover import discover_for_sync, run_discovery
from claude_usage.identity import collect_identity
from claude_usage.models import AccountSnapshot
from claude_usage.paths import ClaudePaths, claude_config_dirs, desktop_data_dirs, statusline_samples_path
from claude_usage.payload import build_sync_payload
from claude_usage.plan_usage import parse_statusline_line, read_desktop_samples, read_statusline_samples
from claude_usage.store import load_config, load_state, redact_config, save_config, update_state
from claude_usage.transcripts import transcript_digest
from claude_usage.transport import post_json
from claude_usage.ui import c
from claude_usage.util import parse_ts, write_private_text


def upload_payload(payload: dict[str, Any], config: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    ingest_url = config.get("ingest_url")
    token = config.get("collector_token")
    if not ingest_url or not token:
        raise RuntimeError("Collector is not enrolled. Run the installer, or --enroll with ENROLLMENT_SECRET set.")
    return post_json(
        ingest_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(payload["idempotency_key"]),
        },
        payload=payload,
        timeout=timeout,
    )


def _primary_organization(accounts: list[AccountSnapshot]) -> str:
    ranked = sorted(accounts, key=lambda a: ("claude_code" not in a.source, a.organization_uuid is None))
    return next((a.organization_uuid for a in ranked if a.organization_uuid), "unknown")


def build_payload(claude: ClaudePaths, query: UsageQuery) -> tuple[dict[str, Any], dict[str, Any]]:
    usage = collect_usage(claude, query)
    config = load_config()
    state = load_state()

    sources, discovery_was_full = discover_for_sync(claude)
    if not usage.requests and evidence_exists(sources, {"desktop_chat", "cowork", "desktop_app", "desktop_code"}):
        sources = run_discovery(claude, full=True)
        discovery_was_full = True
    sources = query.filter_sources(sources)

    claude_dirs = claude_config_dirs(claude)
    desktop_dirs = desktop_data_dirs()
    accounts = read_accounts(claude_dirs, desktop_dirs, salt=config.get("org_id") or "")
    cursor = state.get("plan_usage_cursor") if isinstance(state.get("plan_usage_cursor"), dict) else {}
    desktop_samples, unknown_versions = read_desktop_samples(desktop_dirs, since=parse_ts(cursor.get("desktop")))
    statusline_samples = read_statusline_samples(
        statusline_samples_path(), _primary_organization(accounts), since=parse_ts(cursor.get("statusline"))
    )
    desktop_sessions = [d for d in read_desktop_sessions(desktop_dirs) if query.includes_surface(d.surface)]
    anomalies = [
        *discover_anomalies(claude, usage.jsonl_paths),
        *plan_usage_anomalies(unknown_versions),
        *retention_anomalies(claude_dirs, state.get("last_success_at")),
    ]

    payload = build_sync_payload(
        claude=claude,
        config=config,
        identity=collect_identity(config),
        requests=usage.requests,
        sessions=usage.sessions,
        stats=usage.stats,
        stats_cache_present=claude.stats_cache.exists(),
        transcript_files=len(usage.jsonl_paths),
        transcript_digest=transcript_digest(usage.jsonl_paths),
        period_start=usage.period_start,
        period_end=usage.period_end,
        anomalies=sanitize_sync_anomalies(anomalies, claude),
        sources=sources,
        activity_daily=extract_activity_daily(sources) + desktop_session_activity(desktop_sessions),
        sync_scope=query.scope(),
        accounts=accounts,
        install=read_install_info(claude_dirs),
        feature_usage=read_feature_usage(claude_dirs),
        plan_usage=desktop_samples + statusline_samples,
        desktop_sessions=desktop_sessions,
        # Only an all-history sync may add stats-cache days; a shorter window would overlap days sent before.
        include_stats_history=query.days is None,
    )
    payload["summary"]["discovery_mode"] = "full" if discovery_was_full else "light"
    return payload, config


def _advance_plan_usage_cursor(payload: dict[str, Any]):
    """Remember the newest uploaded sample per source and drop uploaded lines from the statusline capture."""
    state = load_state()
    cursor = dict(state.get("plan_usage_cursor") or {})
    for sample in payload.get("plan_usage") or []:
        if sample["sampled_at"] > (cursor.get(sample["source"]) or ""):
            cursor[sample["source"]] = sample["sampled_at"]
    update_state({"plan_usage_cursor": cursor})

    uploaded_until = parse_ts(cursor.get("statusline"))
    path = statusline_samples_path()
    if not uploaded_until or not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        keep = [line for line in lines if (sample := parse_statusline_line(line, "")) and parse_ts(sample.sampled_at) > uploaded_until]
        write_private_text(path, "".join(f"{line}\n" for line in keep))
    except OSError:
        pass


def run_sync(claude: ClaudePaths, query: UsageQuery, *, dry_run: bool = False) -> int:
    payload, config = build_payload(claude, query)

    if dry_run:
        print(json.dumps({
            "config": redact_config(config),
            "payload": payload,
        }, indent=2, sort_keys=True))
        return 0

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        result = upload_payload(payload, config)
    except Exception as exc:
        update_state({
            "last_attempt_at": started_at,
            "last_error_at": datetime.now(timezone.utc).isoformat(),
            "last_error": str(exc),
        })
        print(c(str(exc), "red"))
        return 2

    if not config.get("machine_id") or not config.get("user_id"):
        # Installs enrolled before IDs were stored: pin the IDs this upload used.
        save_config({**load_config(), "machine_id": payload["identity"]["machine_id"], "user_id": payload["identity"]["user_id"]})

    _advance_plan_usage_cursor(payload)
    update_state({
        "last_attempt_at": started_at,
        "last_success_at": datetime.now(timezone.utc).isoformat(),
        "last_error": "",
        "last_idempotency_key": payload["idempotency_key"],
        "last_summary": payload["summary"],
        "last_response_status": result["status"],
    })
    print(c("Usage uploaded successfully.", "green"))
    print(f"Status          : {result['status']}")
    print(f"Idempotency key : {payload['idempotency_key']}")
    return 0
