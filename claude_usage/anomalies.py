"""Local data-location problems worth reporting (missing dirs, stale caches, unreadable files)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from claude_usage.paths import ClaudePaths
from claude_usage.transcripts import discover_jsonl, load_stats_cache


def discover_anomalies(claude: ClaudePaths, paths: list[Path] | None = None) -> list[dict[str, str]]:
    anomalies: list[dict[str, str]] = []
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    default_dir = Path.home() / ".claude"

    if env_dir:
        anomalies.append({
            "code": "custom_claude_config_dir",
            "severity": "info",
            "message": f"CLAUDE_CONFIG_DIR is set, using {claude.claude_dir}.",
        })

    if not claude.claude_dir.exists():
        anomalies.append({
            "code": "claude_dir_missing",
            "severity": "bad",
            "message": f"Claude config directory was not found at {claude.claude_dir}.",
        })
        return anomalies

    if env_dir and default_dir.exists() and default_dir.resolve() != claude.claude_dir.resolve():
        anomalies.append({
            "code": "default_and_custom_dirs_exist",
            "severity": "warn",
            "message": f"Both default {default_dir} and custom {claude.claude_dir} Claude directories exist.",
        })

    if not claude.projects_dir.exists():
        anomalies.append({
            "code": "projects_dir_missing",
            "severity": "bad",
            "message": f"Claude projects directory was not found at {claude.projects_dir}.",
        })

    if not claude.stats_cache.exists():
        anomalies.append({
            "code": "stats_cache_missing",
            "severity": "warn",
            "message": f"Claude stats cache was not found at {claude.stats_cache}.",
        })
    else:
        stats = load_stats_cache(claude.stats_cache)
        cache_date = stats.get("lastComputedDate") if stats else None
        if cache_date and cache_date < datetime.now().strftime("%Y-%m-%d"):
            anomalies.append({
                "code": "stats_cache_stale",
                "severity": "info",
                "message": f"stats-cache.json is stale as of {cache_date}; JSONL transcripts are used.",
            })

    try:
        projects_files = paths if paths is not None else discover_jsonl(claude.projects_dir)
    except Exception as exc:
        anomalies.append({
            "code": "jsonl_discovery_failed",
            "severity": "bad",
            "message": f"Could not scan Claude JSONL files: {exc}.",
        })
        projects_files = []

    if claude.projects_dir.exists() and not projects_files:
        anomalies.append({
            "code": "no_transcript_files",
            "severity": "warn",
            "message": f"No Claude transcript JSONL files were found under {claude.projects_dir}.",
        })

    unreadable = 0
    for path in projects_files[:5000]:
        try:
            with path.open("rb") as f:
                f.read(1)
        except OSError:
            unreadable += 1
    if unreadable:
        anomalies.append({
            "code": "unreadable_transcripts",
            "severity": "warn",
            "message": f"{unreadable} transcript files could not be read by this user.",
        })

    return anomalies

def sanitize_sync_anomalies(anomalies: list[dict[str, str]], claude: ClaudePaths) -> list[dict[str, str]]:
    replacements = {
        str(claude.claude_dir): "<claude_config_dir>",
        str(claude.projects_dir): "<claude_projects_dir>",
        str(claude.stats_cache): "<claude_stats_cache>",
        str(Path.home()): "<home>",
    }
    sanitized = []
    for anomaly in anomalies:
        message = anomaly.get("message", "")
        for raw, label in replacements.items():
            if raw:
                message = message.replace(raw, label)
        sanitized.append({
            "code": anomaly.get("code", "unknown"),
            "severity": anomaly.get("severity", "warn"),
            "message": message,
        })
    return sanitized
