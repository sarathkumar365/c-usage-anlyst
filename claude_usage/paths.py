"""Where the agent keeps its files and where Claude keeps its data.

Resolved once per command and passed down, so no flow depends on global state.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def app_dir() -> Path:
    override = os.environ.get("CLAUDE_USAGE_AGENT_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ClaudeUsageAgent"
    return Path.home() / ".claude-usage-agent"


def config_path() -> Path:
    return app_dir() / "config.json"


def state_path() -> Path:
    return app_dir() / "state.json"


def log_dir() -> Path:
    return app_dir() / "logs"


@dataclass(frozen=True)
class ClaudePaths:
    claude_dir: Path
    # Which input chose claude_dir: "argument", "collector_config", "CLAUDE_CONFIG_DIR", or "default".
    source: str

    @property
    def projects_dir(self) -> Path:
        return self.claude_dir / "projects"

    @property
    def stats_cache(self) -> Path:
        return self.claude_dir / "stats-cache.json"


def resolve_claude_paths(argument: str | None = None, config: dict[str, Any] | None = None) -> ClaudePaths:
    config = config or {}
    for value, source in (
        (argument, "argument"),
        (config.get("claude_config_dir"), "collector_config"),
        (os.environ.get("CLAUDE_CONFIG_DIR"), "CLAUDE_CONFIG_DIR"),
    ):
        if value:
            return ClaudePaths(Path(value).expanduser(), source)
    return ClaudePaths(Path.home() / ".claude", "default")
