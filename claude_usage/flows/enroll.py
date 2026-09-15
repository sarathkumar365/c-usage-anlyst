"""Enroll flow: exchange the shared enrollment secret for this collector's own token, stored in config.json."""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any

from claude_usage.constants import APP_VERSION, DEFAULT_SYNC_INTERVAL_MINUTES
from claude_usage.identity import collect_identity, new_collector_id
from claude_usage.paths import ClaudePaths, config_path, log_dir
from claude_usage.store import load_config, save_config
from claude_usage.transport import HttpStatusError, post_json
from claude_usage.ui import c
from claude_usage.util import ensure_private_dir


def _labels(existing: dict[str, Any], org_id: str | None, account_label: str | None, collector_label: str | None) -> dict[str, str]:
    return {
        "org_id": org_id or existing.get("org_id") or "",
        "account_label": account_label or existing.get("account_label") or "",
        "collector_label": collector_label or existing.get("collector_label") or socket.gethostname(),
    }


def _request(enroll_url: str, secret: str, identity: dict[str, Any], org_id: str, token: str | None) -> dict[str, Any]:
    headers = {"X-Enrollment-Secret": secret}
    if token:
        # Proves this machine owns the collector_id; the server refuses re-enrollment without it.
        headers["Authorization"] = f"Bearer {token}"
    result = post_json(enroll_url, {
        "schema_version": 1,
        "collector_version": APP_VERSION,
        "org_id": org_id,
        "identity": identity,
    }, headers=headers)
    return result.get("response") or {}


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
    if not enrollment_secret:
        raise RuntimeError("Enrollment requires the ENROLLMENT_SECRET environment variable. Ask your admin for it.")
    existing = load_config()
    labels = _labels(existing, org_id, account_label, collector_label)
    collector_id = existing.get("collector_id") or new_collector_id()
    identity = collect_identity({**existing, "collector_id": collector_id, **labels})
    try:
        response = _request(enroll_url, enrollment_secret, identity, labels["org_id"], existing.get("collector_token"))
    except HttpStatusError as exc:
        if exc.status != 409:
            raise
        # This collector_id is already enrolled and our token is not accepted (lost or revoked): start a new collector.
        collector_id = new_collector_id()
        identity = collect_identity({**existing, "collector_id": collector_id, "machine_id": None, "user_id": None, **labels})
        response = _request(enroll_url, enrollment_secret, identity, labels["org_id"], None)
    collector_token = response.get("collector_token")
    ingest_url = response.get("ingest_url")
    if not collector_token or not ingest_url:
        raise RuntimeError("Enrollment response was missing the collector token or ingest URL.")

    now = datetime.now(timezone.utc).isoformat()
    config = {
        **existing,
        "schema_version": 1,
        "collector_id": collector_id,
        # The server keeps the IDs from an earlier enrollment of this collector, so its answer wins.
        "machine_id": response.get("machine_id") or identity["machine_id"],
        "user_id": response.get("user_id") or identity["user_id"],
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
    save_config(config)
    ensure_private_dir(log_dir())
    print(c("Collector enrolled.", "green"))
    print(f"Config : {config_path()}")
    print(f"ID     : {config['collector_id']}")
    print(f"Label  : {config['collector_label']}")
    print(f"Sync   : every {config['sync_interval_minutes']} minutes")
