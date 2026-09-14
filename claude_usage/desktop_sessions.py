"""Claude Desktop session records (Code tab and Cowork) -> per-session metadata. Titles are never read."""

from __future__ import annotations

from pathlib import Path

from claude_usage.models import ActivityDaily, DesktopSession
from claude_usage.util import date_key, read_json_file, stable_hash, ts_from_epoch

SESSION_STORES = (("claude-code-sessions", "desktop_code"), ("local-agent-mode-sessions", "cowork"))


def read_desktop_sessions(desktop_dirs: list[Path]) -> list[DesktopSession]:
    sessions: list[DesktopSession] = []
    for desktop in desktop_dirs:
        for store, surface in SESSION_STORES:
            for path in sorted((desktop / store).glob("*/*/local_*.json")):
                meta = read_json_file(path)
                session_id = meta.get("cliSessionId") or meta.get("sessionId")
                if not session_id:
                    continue
                turns = meta.get("completedTurns")
                sessions.append(DesktopSession(
                    cli_session_id=str(session_id),
                    surface=surface,
                    model=str(meta["model"]) if meta.get("model") else None,
                    effort=str(meta["effort"]) if meta.get("effort") else None,
                    completed_turns=turns if isinstance(turns, int) else 0,
                    created_at=ts_from_epoch(meta.get("createdAt")),
                    last_activity_at=ts_from_epoch(meta.get("lastActivityAt")),
                    is_archived=bool(meta.get("isArchived")),
                ))
    return sessions


def desktop_session_activity(sessions: list[DesktopSession]) -> list[ActivityDaily]:
    """Activity on the day each Desktop session was last active (not the folder's newest file)."""
    rows: dict[tuple[str, str], ActivityDaily] = {}
    for session in sessions:
        day = date_key(session.last_activity_at or session.created_at)
        if day == "unknown":
            continue
        key = (day, session.surface)
        if key not in rows:
            rows[key] = ActivityDaily(
                day=day,
                surface=session.surface,
                source_id=stable_hash({"surface": session.surface, "store": "desktop_sessions"})[:24],
                confidence="derived",
            )
        rows[key].sessions += 1
        rows[key].turns += session.completed_turns
    return sorted(rows.values(), key=lambda r: (r.day, r.surface))
