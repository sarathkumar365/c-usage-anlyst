"""Discovered Desktop/Cowork/extension sources -> per-day activity rows."""

from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from claude_usage.discovery import SKIP_DIR_NAMES, SourceRecord
from claude_usage.models import ActivityDaily

DESKTOP_SESSION_EXTRACTORS = {"claude_desktop_code", "claude_desktop_cowork"}
MAX_FILES_PER_SOURCE = 5000


def files_touched_per_day(path: Path) -> Counter:
    """How many files under `path` were last modified on each local day.

    Counting per file keeps each day's number stable across syncs, instead of piling a source's whole file count
    onto whichever day its newest file changed.
    """
    days: Counter = Counter()
    if path.is_file():
        candidates = [path]
    else:
        candidates = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
            candidates.extend(Path(root) / name for name in files)
            if len(candidates) >= MAX_FILES_PER_SOURCE:
                break
    for candidate in candidates[:MAX_FILES_PER_SOURCE]:
        try:
            days[datetime.fromtimestamp(candidate.stat().st_mtime).strftime("%Y-%m-%d")] += 1
        except OSError:
            continue
    return days


def extract_activity_daily(sources: list[SourceRecord]) -> list[ActivityDaily]:
    rows: list[ActivityDaily] = []
    for source in sources:
        # Desktop session stores are counted per session by desktop_sessions.desktop_session_activity.
        if source.status != "present" or source.confidence == "exact" or source.extractor in DESKTOP_SESSION_EXTRACTORS:
            continue
        for day, files in sorted(files_touched_per_day(Path(source.path)).items()):
            row = ActivityDaily(day=day, surface=source.surface, source_id=source.source_id, confidence=source.confidence)
            if source.extractor == "claude_desktop_extensions":
                row.tool_calls = files
            else:
                row.messages = files
            rows.append(row)
    return sorted(rows, key=lambda r: (r.day, r.surface, r.source_id))
