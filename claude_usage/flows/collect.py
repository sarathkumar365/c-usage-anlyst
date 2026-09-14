"""Collect flow: Claude Code transcripts -> usage filtered by the user's query.

Every filter (--days, --project, --main-only, --surface) is applied here and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from claude_usage.discovery import SourceRecord
from claude_usage.models import RequestUsage, SessionSummary
from claude_usage.paths import ClaudePaths, transcript_roots
from claude_usage.transcripts import dedupe_requests, discover_jsonl, load_stats_cache, parse_requests, summarize_sessions

SURFACES_BY_FILTER = {
    "claude-code": {"claude_code"},
    "desktop": {"desktop_chat", "desktop_app", "desktop_code"},
    "cowork": {"cowork"},
    "extensions": {"extensions", "ide_extension", "browser_extension"},
}


@dataclass(frozen=True)
class UsageQuery:
    days: int | None = None
    project: str | None = None
    main_only: bool = False
    surface: str = "all"

    def scope(self) -> dict[str, Any]:
        return {"days": self.days, "project": self.project, "main_only": self.main_only, "surface": self.surface}

    def includes_surface(self, surface: str) -> bool:
        return self.surface == "all" or surface in SURFACES_BY_FILTER.get(self.surface, set())

    def filter_sources(self, sources: list[SourceRecord]) -> list[SourceRecord]:
        wanted = SURFACES_BY_FILTER.get(self.surface)
        if not wanted:
            return sources
        return [source for source in sources if source.surface in wanted]


@dataclass
class Usage:
    jsonl_paths: list[Path]
    period_start: datetime | None
    period_end: datetime
    requests: list[RequestUsage] = field(default_factory=list)
    sessions: dict[str, SessionSummary] = field(default_factory=dict)
    stats: dict[str, Any] | None = None


def collect_usage(claude: ClaudePaths, query: UsageQuery) -> Usage:
    roots = [root for root in transcript_roots(claude) if query.includes_surface(root.surface)]
    root_paths = [(root, discover_jsonl(root.projects_dir)) for root in roots]
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    start = end - timedelta(days=max(0, query.days)) if query.days is not None else None
    usage = Usage(
        jsonl_paths=[path for _, paths in root_paths for path in paths],
        period_start=start,
        period_end=end,
        stats=load_stats_cache(claude.stats_cache),
    )
    if not root_paths:
        return usage

    parsed: list[RequestUsage] = []
    for root, paths in root_paths:
        parsed.extend(parse_requests(paths, root.projects_dir, start=start, end=end, surface=root.surface))
    requests = dedupe_requests(parsed)
    if query.project:
        needle = query.project.lower()
        requests = [r for r in requests if needle in r.project.lower() or needle in r.file.lower()]
    if query.main_only:
        requests = [r for r in requests if not r.is_subagent]
    usage.requests = requests
    usage.sessions = summarize_sessions(requests)
    return usage
