from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

FIXED_MTIME = 1_788_000_000  # 2026-08-29T12:00:00Z
SESSION_ID = "11111111-2222-3333-4444-555555555555"
PROJECT_DIR = "-Users-test-work-demo"
ACCOUNT_UUID = "aaaaaaaa-1111-2222-3333-444444444444"
ORG_UUID = "bbbbbbbb-1111-2222-3333-444444444444"
COWORK_SESSION_ID = "cccccccc-1111-2222-3333-444444444444"
EMAIL = "Owner@Example.com"
SECRET_TITLE = "Fixture secret session title"


def _assistant(request_id: str, ts: str, usage: dict, *, model: str = "claude-opus-5", stop: str | None = "end_turn", content=None, **line) -> dict:
    return {
        **line,
        "type": "assistant",
        "timestamp": ts,
        "requestId": request_id,
        "uuid": f"u-{request_id}-{ts}",
        "message": {
            "id": request_id,
            "model": model,
            "stop_reason": stop,
            "usage": usage,
            "content": content or [],
        },
    }


def _write_jsonl(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\nnot json\n", encoding="utf-8")


def build_fixture_home(root: Path) -> Path:
    """Build a deterministic fake home with Claude Code and Claude Desktop data."""
    if root.exists():
        shutil.rmtree(root)
    home = root / "home"
    claude = home / ".claude"
    project = claude / "projects" / PROJECT_DIR

    _write_jsonl(project / f"{SESSION_ID}.jsonl", [
        {"type": "user", "timestamp": "2026-08-20T10:00:00Z", "gitBranch": "feature/login", "entrypoint": "cli", "version": "2.1.200",
         "message": {"role": "user", "content": "hi"}},
        _assistant("req_a", "2026-08-20T10:00:05Z", {"input_tokens": 10, "output_tokens": 1}, stop=None),
        _assistant("req_a", "2026-08-20T10:00:09Z", {
            "input_tokens": 12,
            "output_tokens": 340,
            "cache_read_input_tokens": 90_000,
            "cache_creation_input_tokens": 60_000,
            "cache_creation": {"ephemeral_5m_input_tokens": 55_000, "ephemeral_1h_input_tokens": 5_000},
            "server_tool_use": {"web_search_requests": 2, "web_fetch_requests": 1},
            "iterations": [{"input_tokens": 5, "output_tokens": 100, "type": "message"}, {"type": "advisor_message"}],
        }, content=[
            {"type": "tool_use", "name": "Read"},
            {"type": "tool_use", "name": "mcp__Claude_Browser__javascript_tool"},
        ]),
        _assistant("req_b", "2026-08-21T15:30:00Z", {
            "input_tokens": 3,
            "output_tokens": 50,
            "cache_read_input_tokens": 1_000,
        }, model="claude-sonnet-5", content=[{"type": "tool_use", "name": "Bash"}], version="2.1.210"),
    ])
    # A resumed session copies earlier responses into a new file, and a sidechain replays one again.
    _write_jsonl(project / "99999999-2222-3333-4444-555555555555.jsonl", [
        _assistant("req_b", "2026-08-21T15:30:00Z", {"input_tokens": 3, "output_tokens": 50, "cache_read_input_tokens": 1_000}, model="claude-sonnet-5"),
        _assistant("req_b", "2026-08-21T15:30:00Z", {"input_tokens": 3, "output_tokens": 50, "cache_read_input_tokens": 1_000}, model="claude-sonnet-5", isSidechain=True),
    ])
    _write_jsonl(project / SESSION_ID / "subagents" / "agent-abc123.jsonl", [
        _assistant("req_sub", "2026-08-20T10:05:00Z", {"input_tokens": 4, "output_tokens": 20}, stop=None),
    ])
    _write_jsonl(claude / "projects" / "-Users-test-other" / "66666666-7777-8888-9999-000000000000.jsonl", [
        _assistant("req_c", "2026-08-22T08:00:00Z", {"input_tokens": 100, "output_tokens": 200}, model="claude-haiku-4-5-20251001"),
    ])
    (claude / "stats-cache.json").write_text(json.dumps({
        "lastComputedDate": "2026-08-01",
        "totalSessions": 3,
        "totalMessages": 9,
        "dailyModelTokens": [{"date": "2026-08-01", "tokensByModel": {"claude-opus-5": 1234}}],
    }), encoding="utf-8")
    (claude / "history.jsonl").write_text("{}\n", encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps({
        "installMethod": "native",
        "firstStartTime": "2026-01-02T03:04:05Z",
        "numStartups": 7,
        "oauthAccount": {
            "accountUuid": ACCOUNT_UUID,
            "organizationUuid": ORG_UUID,
            "organizationName": f"{EMAIL}'s Organization",
            "emailAddress": EMAIL,
            "billingType": "stripe_subscription",
            "userRateLimitTier": "default_claude_max_20x",
        },
        "skillUsage": {"pdf": {"usageCount": 3, "lastUsedAt": 1}},
        "pluginUsage": {"github": {"usageCount": 2, "lastUsedAt": 1}},
    }), encoding="utf-8")
    (claude / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 30}), encoding="utf-8")
    (home / ".vscode" / "extensions" / "anthropic.claude-code-2.1.200-darwin-arm64").mkdir(parents=True)
    (home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Extensions" / "fcoeoabgfenejglbffodgkkbkcdhcgfn").mkdir(parents=True)

    # Desktop data in both the macOS and Linux locations; each platform only scans its own.
    for support in (home / "Library" / "Application Support" / "Claude", home / ".config" / "Claude"):
        code = support / "claude-code-sessions" / ACCOUNT_UUID / ORG_UUID
        code.mkdir(parents=True)
        (code / "local_1.json").write_text(json.dumps({
            "cliSessionId": SESSION_ID, "model": "claude-opus-5", "effort": "high", "completedTurns": 4,
            "createdAt": 1_787_220_000_000, "lastActivityAt": 1_787_227_200_000, "title": SECRET_TITLE,
            "enabledMcpTools": {"a": True, "b": True},
        }), encoding="utf-8")
        (code / "scheduled-tasks.json").write_text("{}", encoding="utf-8")
        cowork = support / "local-agent-mode-sessions" / ACCOUNT_UUID / ORG_UUID
        cowork.mkdir(parents=True)
        (cowork / "local_2.json").write_text(json.dumps({
            "cliSessionId": COWORK_SESSION_ID, "completedTurns": 2, "lastActivityAt": 1_787_313_600_000, "title": SECRET_TITLE,
        }), encoding="utf-8")
        _write_jsonl(cowork / "local_2" / ".claude" / "projects" / "-sessions-fixture" / f"{COWORK_SESSION_ID}.jsonl", [
            _assistant("req_cowork", "2026-08-22T09:00:00Z", {"input_tokens": 7, "output_tokens": 70}, entrypoint="claude-desktop"),
        ])
        (support / "config.json").write_text(json.dumps({"lastKnownAccountUuid": ACCOUNT_UUID}), encoding="utf-8")
        (support / "plan-usage-history.json").write_text(json.dumps({"version": 2, "samples": [
            {"t": 1_787_220_000_000, "org": ORG_UUID, "u": {"fh": 10, "sd": 40}},
            {"t": 1_787_220_900_000, "org": ORG_UUID, "u": {"fh": 25, "sd": 41, "xu": 0}},
            {"t": 1_787_221_800_000, "org": ORG_UUID, "u": {"fh": 31, "sd": 42}},
        ]}), encoding="utf-8")
        (support / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb").mkdir(parents=True)
        (support / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb" / "000003.log").write_text("x", encoding="utf-8")
        (support / "claude_desktop_config.json").write_text(json.dumps({"mcpServers": {}, "theme": "dark"}), encoding="utf-8")
    prefs = home / "Library" / "Preferences"
    prefs.mkdir(parents=True)
    (prefs / "com.anthropic.claudefordesktop.plist").write_text("plist", encoding="utf-8")

    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames + dirnames:
            os.utime(Path(dirpath) / name, (FIXED_MTIME, FIXED_MTIME))
    os.utime(root, (FIXED_MTIME, FIXED_MTIME))
    return home


def fixture_env(root: Path, home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDE_CONFIG_DIR", "ENROLLMENT_SECRET", "COLLECTOR_TOKEN"}}
    env.update({
        "HOME": str(home),
        "USER": "fixture-user",
        "LOGNAME": "fixture-user",
        "CLAUDE_USAGE_AGENT_DIR": str(root / "agent"),
        "TZ": "UTC",
        "COLUMNS": "140",
    })
    return env
