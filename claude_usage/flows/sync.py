"""Sync flow: collect usage + discover sources -> build payload -> upload -> record outcome."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from claude_usage.activity import extract_activity_daily
from claude_usage.anomalies import discover_anomalies, sanitize_sync_anomalies
from claude_usage.discovery import evidence_exists
from claude_usage.flows.collect import UsageQuery, collect_usage
from claude_usage.flows.discover import discover_for_sync, run_discovery
from claude_usage.identity import collect_identity
from claude_usage.paths import ClaudePaths
from claude_usage.payload import build_sync_payload
from claude_usage.store import load_config, redact_config, save_config, update_state
from claude_usage.transcripts import transcript_digest
from claude_usage.transport import post_json
from claude_usage.ui import c


def upload_payload(payload: dict[str, Any], config: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    ingest_url = config.get("ingest_url")
    token = config.get("collector_token")
    if not ingest_url or not token:
        raise RuntimeError("Collector is not registered. Run --register with --ingest-url and --collector-token first.")
    return post_json(
        ingest_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(payload["idempotency_key"]),
        },
        payload=payload,
        timeout=timeout,
    )


def build_payload(claude: ClaudePaths, query: UsageQuery) -> tuple[dict[str, Any], dict[str, Any]]:
    usage = collect_usage(claude, query)
    config = load_config()

    sources, discovery_was_full = discover_for_sync(claude)
    if not usage.requests and evidence_exists(sources, {"desktop_chat", "cowork", "desktop_app"}):
        sources = run_discovery(claude, full=True)
        discovery_was_full = True
    sources = query.filter_sources(sources)

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
        anomalies=sanitize_sync_anomalies(discover_anomalies(claude, usage.jsonl_paths), claude),
        sources=sources,
        activity_daily=extract_activity_daily(sources),
        sync_scope=query.scope(),
    )
    payload["summary"]["discovery_mode"] = "full" if discovery_was_full else "light"
    return payload, config


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
