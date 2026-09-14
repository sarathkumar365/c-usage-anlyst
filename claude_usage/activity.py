"""Discovered Desktop/Cowork/extension sources -> per-day activity rows."""

from __future__ import annotations

from pathlib import Path

from claude_usage.discovery import SourceRecord
from claude_usage.models import ActivityDaily
from claude_usage.util import date_key, read_json_file

DESKTOP_SESSION_EXTRACTORS = {"claude_desktop_code", "claude_desktop_cowork"}


def extract_activity_daily(sources: list[SourceRecord]) -> list[ActivityDaily]:
    rows: dict[tuple[str, str, str, str], ActivityDaily] = {}
    for source in sources:
        # Desktop session stores are counted per session by desktop_sessions.desktop_session_activity.
        if source.status != "present" or source.confidence == "exact" or source.extractor in DESKTOP_SESSION_EXTRACTORS:
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

        if source.extractor == "claude_desktop_extensions":
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
