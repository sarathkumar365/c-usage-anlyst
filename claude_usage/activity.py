"""Discovered Desktop/Cowork/extension sources -> per-day activity rows."""

from __future__ import annotations

from pathlib import Path

from claude_usage.discovery import SourceRecord
from claude_usage.models import ActivityDaily
from claude_usage.util import date_key, read_json_file


def extract_activity_daily(sources: list[SourceRecord]) -> list[ActivityDaily]:
    rows: dict[tuple[str, str, str, str], ActivityDaily] = {}
    for source in sources:
        if source.status != "present" or source.confidence == "exact":
            continue
        day = date_key(source.latest_activity_at)
        if day == "unknown":
            continue
        key = (day, source.surface, source.source_id, source.confidence)
        rows[key] = ActivityDaily(
            day=day,
            surface=source.surface,
            source_id=source.source_id,
            confidence=source.confidence,
        )

        if source.extractor == "claude_desktop_cowork":
            activity = rows[key]
            path = Path(source.path)
            json_paths = [path] if path.is_file() and path.suffix == ".json" else []
            if path.is_dir():
                json_paths = [p for p in path.rglob("*.json") if p.name != "scheduled-tasks.json"]
            enabled_tools: set[str] = set()
            for json_path in json_paths[:5000]:
                meta = read_json_file(json_path)
                try:
                    activity.turns += int(meta.get("completedTurns") or 0)
                except Exception:
                    pass
                tools = meta.get("enabledMcpTools")
                if isinstance(tools, dict):
                    enabled_tools.update(str(name) for name in tools)
            activity.sessions += len(json_paths[:5000]) if json_paths else 1
            activity.tool_calls += len(enabled_tools)
        elif source.extractor == "claude_desktop_extensions":
            activity = rows[key]
            path = Path(source.path)
            if path.is_dir():
                activity.tool_calls += max(1, source.file_count)
            else:
                meta = read_json_file(path)
                activity.tool_calls += len(meta) if meta else 1
        else:
            rows[key].messages += source.file_count

    return sorted(rows.values(), key=lambda r: (r.day, r.surface, r.source_id))
