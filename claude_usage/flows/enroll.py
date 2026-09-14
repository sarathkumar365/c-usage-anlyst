"""Enroll flow: obtain collector credentials and store them in config.json.

enroll   -> backend issues a token after checking the shared enrollment secret
register -> an admin-issued token is supplied directly
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any

from claude_usage.constants import APP_VERSION, DEFAULT_SYNC_INTERVAL_MINUTES
from claude_usage.identity import collect_identity, new_collector_id
from claude_usage.paths import ClaudePaths, config_path, log_dir
from claude_usage.store import load_config, save_config
from claude_usage.transport import post_json
from claude_usage.ui import c


def _labels(existing: dict[str, Any], org_id: str | None, account_label: str | None, collector_label: str | None) -> dict[str, str]:
    return {
        "org_id": org_id or existing.get("org_id") or "",
        "account_label": account_label or existing.get("account_label") or "",
        "collector_label": collector_label or existing.get("collector_label") or socket.gethostname(),
    }


def _save(config: dict[str, Any], verb: str):
    save_config(config)
    log_dir().mkdir(parents=True, exist_ok=True)
    print(c(f"Collector {verb}.", "green"))
    print(f"Config : {config_path()}")
    print(f"ID     : {config['collector_id']}")
    print(f"Label  : {config['collector_label']}")


def register(
    claude: ClaudePaths,
    *,
    ingest_url: str,
    collector_token: str,
    org_id: str | None,
    account_label: str | None,
    collector_label: str | None,
    sync_interval_minutes: int | None,
):
    existing = load_config()
    now = datetime.now(timezone.utc).isoformat()
    identity = collect_identity(existing)
    _save({
        **existing,
        "schema_version": 1,
        "collector_id": existing.get("collector_id") or new_collector_id(),
        "machine_id": identity["machine_id"],
        "user_id": identity["user_id"],
        "ingest_url": ingest_url,
        "collector_token": collector_token,
        **_labels(existing, org_id, account_label, collector_label),
        "claude_config_dir": str(claude.claude_dir),
        "sync_interval_minutes": sync_interval_minutes or existing.get("sync_interval_minutes") or DEFAULT_SYNC_INTERVAL_MINUTES,
        "registered_at": existing.get("registered_at") or now,
        "updated_at": now,
    }, "registered")


def enroll(
    claude: ClaudePaths,
    *,
    enroll_url: str,
    enrollment_secret: str,
    org_id: str | None,
    account_label: str | None,
    collector_label: str | None,
    sync_interval_minutes: int | None,
):
    """Raises RuntimeError with a user-facing message on any failure."""
    existing = load_config()
    labels = _labels(existing, org_id, account_label, collector_label)
    collector_id = existing.get("collector_id") or new_collector_id()
    identity = collect_identity({**existing, "collector_id": collector_id, **labels})
    if not enrollment_secret:
        raise RuntimeError("Enrollment requires ENROLLMENT_SECRET (or --enrollment-secret). Ask your admin for it.")
    result = post_json(enroll_url, {
        "schema_version": 1,
        "collector_version": APP_VERSION,
        "org_id": labels["org_id"],
        "identity": identity,
    }, headers={"X-Enrollment-Secret": enrollment_secret})
    response = result.get("response") or {}
    collector_token = response.get("collector_token")
    ingest_url = response.get("ingest_url")
    if not collector_token or not ingest_url:
        raise RuntimeError(f"Enrollment response missing token or ingest URL: {response}")

    now = datetime.now(timezone.utc).isoformat()
    config = {
        **existing,
        "schema_version": 1,
        "collector_id": collector_id,
        "machine_id": identity["machine_id"],
        "user_id": identity["user_id"],
        "enroll_url": enroll_url,
        "ingest_url": ingest_url,
        "collector_token": collector_token,
        **labels,
        "org_id": response.get("org_id") or labels["org_id"],
        "claude_config_dir": str(claude.claude_dir),
        "sync_interval_minutes": int(response.get("sync_interval_minutes") or sync_interval_minutes or DEFAULT_SYNC_INTERVAL_MINUTES),
        "registered_at": existing.get("registered_at") or now,
        "updated_at": now,
        "enrolled_at": now,
    }
    _save(config, "enrolled")
    print(f"Sync   : every {config['sync_interval_minutes']} minutes")
