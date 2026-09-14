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
from claude_usage.paths import ClaudePaths
from claude_usage.transcripts import discover_jsonl, load_stats_cache, parse_all

SURFACES_BY_FILTER = {
    "claude-code": {"claude_code"},
    "desktop": {"desktop_chat", "desktop_app"},
    "cowork": {"cowork"},
    "extensions": {"extensions"},
}


@dataclass(frozen=True)
class UsageQuery:
    days: int | None = None
    project: str | None = None
    main_only: bool = False
    surface: str = "all"

    def scope(self) -> dict[str, Any]:
        return {"days": self.days, "project": self.project, "main_only": self.main_only, "surface": self.surface}

    @property
    def includes_transcripts(self) -> bool:
        return self.surface in ("all", "claude-code")

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


def _keep_sessions(sessions: dict[str, SessionSummary], requests: list[RequestUsage]) -> dict[str, SessionSummary]:
    wanted = set(r.session_id for r in requests)
    return {k: v for k, v in sessions.items() if k in wanted}


def collect_usage(claude: ClaudePaths, query: UsageQuery) -> Usage:
    paths = discover_jsonl(claude.projects_dir)
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    start = end - timedelta(days=max(0, query.days)) if query.days is not None else None
    usage = Usage(jsonl_paths=paths, period_start=start, period_end=end, stats=load_stats_cache(claude.stats_cache))
    if not query.includes_transcripts:
        return usage

    requests, sessions = parse_all(paths, claude.projects_dir, start=start, end=end)
    if query.project:
        needle = query.project.lower()
        requests = [r for r in requests if needle in r.project.lower() or needle in r.file.lower()]
        sessions = _keep_sessions(sessions, requests)
    if query.main_only:
        requests = [r for r in requests if not r.is_subagent]
        sessions = _keep_sessions(sessions, requests)
    usage.requests = requests
    usage.sessions = sessions
    return usage
