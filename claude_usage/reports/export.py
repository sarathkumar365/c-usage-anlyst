"""--json / --csv exports of deduplicated request data."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from claude_usage.models import RequestUsage, SessionSummary


def export_json(path: Path, requests: list[RequestUsage], sessions: dict[str, SessionSummary], projects_dir: Path):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(projects_dir),
        "requests": [asdict(r) for r in requests],
        "sessions": [asdict(s) | {
            "duration_seconds": s.duration_seconds,
            "tokens_per_hour": s.tokens_per_hour,
        } for s in sessions.values()],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def export_csv(path: Path, requests: list[RequestUsage]):
    fields = [
        "timestamp", "project", "session_id", "is_subagent", "agent_id",
        "model", "request_id", "finalized", "duplicate_records",
        "input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "cache_5m_tokens", "cache_1h_tokens",
        "reported_total", "tool_use_count", "web_search_requests",
        "web_fetch_requests", "iteration_count", "advisor_iterations",
        "stop_reason", "file",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in requests:
            row = {k: getattr(r, k) for k in fields if hasattr(r, k)}
            row["reported_total"] = r.reported_total
            writer.writerow(row)
