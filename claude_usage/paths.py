"""Where the agent keeps its files and where Claude keeps its data.

Resolved once per command and passed down, so no flow depends on global state.
"""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from claude_usage.constants import CLAUDE_CHROME_EXTENSION_ID, CLAUDE_CODE_EXTENSION_PREFIX, STATUSLINE_SAMPLES_FILE
from claude_usage.util import stable_hash


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


def statusline_samples_path() -> Path:
    return app_dir() / STATUSLINE_SAMPLES_FILE


def statusline_script_path(system: str | None = None) -> Path:
    system = system or platform.system()
    return app_dir() / ("statusline.ps1" if system == "Windows" else "statusline.sh")


def statusline_chain_path(claude_dir: Path) -> Path:
    return app_dir() / "statusline-chain" / stable_hash(str(claude_dir))[:16]


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

    @property
    def settings(self) -> Path:
        return self.claude_dir / "settings.json"


@dataclass(frozen=True)
class TranscriptRoot:
    projects_dir: Path
    surface: str


def _env_config_dirs() -> list[str]:
    # CLAUDE_CONFIG_DIR may list several directories separated by commas (as ccusage reads it).
    return [part.strip() for part in os.environ.get("CLAUDE_CONFIG_DIR", "").split(",") if part.strip()]


def resolve_claude_paths(argument: str | None = None, config: dict[str, Any] | None = None) -> ClaudePaths:
    config = config or {}
    env_dirs = _env_config_dirs()
    for value, source in (
        (argument, "argument"),
        (config.get("claude_config_dir"), "collector_config"),
        (env_dirs[0] if env_dirs else None, "CLAUDE_CONFIG_DIR"),
    ):
        if value:
            return ClaudePaths(Path(value).expanduser(), source)
    return ClaudePaths(Path.home() / ".claude", "default")


def _unique_existing(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        path = path.expanduser()
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        out.append(path)
    return out


def app_data_roots(system: str | None = None, home: Path | None = None) -> list[Path]:
    """Per-user application data roots where Claude apps keep their files."""
    home = home or Path.home()
    system = system or platform.system()
    if system == "Darwin":
        return [home / "Library" / "Application Support", home / "Library" / "Logs", home / "Library" / "Preferences"]
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        return [
            Path(appdata) if appdata else home / "AppData" / "Roaming",
            Path(localappdata) if localappdata else home / "AppData" / "Local",
        ]
    if system == "Linux":
        return [home / ".config", home / ".cache", home / ".local" / "share"]
    return [home]


def desktop_data_dirs(system: str | None = None, home: Path | None = None) -> list[Path]:
    """Existing Claude Desktop data directories (standard, third-party-provider, and Windows Store builds)."""
    home = home or Path.home()
    system = system or platform.system()
    roots = app_data_roots(system, home)
    candidates: list[Path] = []
    if system == "Darwin":
        candidates = [roots[0] / "Claude", roots[0] / "Claude-3p"]
    elif system == "Windows":
        roaming, local = roots
        candidates = [roaming / "Claude", local / "Claude-3p"]
        candidates.extend(sorted((local / "Packages").glob("Claude_*/LocalCache/Roaming/Claude")))
    elif system == "Linux":
        candidates = [roots[0] / "Claude", roots[0] / "Claude-3p"]
    return _unique_existing(candidates)


def claude_json_path(claude_dir: Path, home: Path | None = None) -> Path:
    """Claude Code's account/state file: ~/.claude.json for the default dir, <dir>/.claude.json otherwise."""
    home = home or Path.home()
    if claude_dir.expanduser() == home / ".claude":
        return home / ".claude.json"
    return claude_dir / ".claude.json"


def managed_settings_paths(system: str | None = None) -> list[Path]:
    system = system or platform.system()
    if system == "Darwin":
        return [Path("/Library/Application Support/ClaudeCode/managed-settings.json")]
    if system == "Windows":
        return [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode" / "managed-settings.json"]
    return [Path("/etc/claude-code/managed-settings.json")]


def wsl_claude_dirs(system: str | None = None) -> list[Path]:
    """Claude Code config dirs inside WSL distros, when this Windows user can read them."""
    if (system or platform.system()) != "Windows":
        return []
    try:
        output = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    # wsl.exe prints UTF-16LE.
    distros = [line.strip() for line in output.decode("utf-16-le", errors="ignore").replace("\x00", "").splitlines() if line.strip()]
    dirs: list[Path] = []
    for distro in distros:
        try:
            homes = [h for h in (Path(rf"\\wsl.localhost\{distro}") / "home").iterdir() if h.is_dir()]
        except OSError:
            continue
        # Only a distro with a single user can be assumed to belong to this Windows user.
        if len(homes) == 1:
            dirs.append(homes[0] / ".claude")
    return _unique_existing(dirs)


def claude_config_dirs(claude: ClaudePaths, system: str | None = None, home: Path | None = None) -> list[Path]:
    """Every existing Claude Code config dir for this user, the resolved one first."""
    home = home or Path.home()
    xdg = Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else home / ".config"
    extras = [*(Path(d) for d in _env_config_dirs()), xdg / "claude", home / ".claude", *wsl_claude_dirs(system)]
    # Only dirs that look like Claude Code's; ~/.config/Claude is Desktop's and matches on case-insensitive disks.
    extras = [d for d in extras if (d / "projects").is_dir() or (d / "settings.json").is_file()]
    return _unique_existing([claude.claude_dir, *extras])


def transcript_roots(claude: ClaudePaths, system: str | None = None, home: Path | None = None) -> list[TranscriptRoot]:
    """Every projects dir holding Claude transcripts: Claude Code config dirs, then Cowork sessions inside Desktop."""
    roots = [TranscriptRoot(d / "projects", "claude_code") for d in claude_config_dirs(claude, system, home)]
    if not roots:
        roots = [TranscriptRoot(claude.projects_dir, "claude_code")]
    for desktop in desktop_data_dirs(system, home):
        for projects in sorted((desktop / "local-agent-mode-sessions").glob("**/.claude/projects")):
            roots.append(TranscriptRoot(projects, "cowork"))
    return roots


def extension_evidence_paths(system: str | None = None, home: Path | None = None) -> list[Path]:
    """Existing IDE extension, browser extension, and Claude Code install locations."""
    home = home or Path.home()
    system = system or platform.system()
    roots = app_data_roots(system, home)
    found: list[Path] = []

    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf", ".vscode-server"):
        found.extend(sorted((home / editor / "extensions").glob(f"{CLAUDE_CODE_EXTENSION_PREFIX}*")))

    if system == "Darwin":
        jetbrains = [roots[0] / "JetBrains"]
        browsers = [roots[0] / name for name in ("Google/Chrome", "Google/Chrome Beta", "Microsoft Edge", "BraveSoftware/Brave-Browser", "Arc/User Data", "Chromium")]
    elif system == "Windows":
        jetbrains = [roots[0] / "JetBrains"]
        browsers = [roots[1] / name for name in ("Google/Chrome/User Data", "Microsoft/Edge/User Data", "BraveSoftware/Brave-Browser/User Data", "Chromium/User Data")]
    else:
        jetbrains = [home / ".local" / "share" / "JetBrains", home / ".config" / "JetBrains"]
        browsers = [home / ".config" / name for name in ("google-chrome", "microsoft-edge", "BraveSoftware/Brave-Browser", "chromium")]
    for root in jetbrains:
        for pattern in ("*/plugins/*laude*", "*/*laude*"):
            found.extend(sorted(root.glob(pattern)))
    for root in browsers:
        found.extend(sorted(root.glob(f"*/Extensions/{CLAUDE_CHROME_EXTENSION_ID}")))

    found.extend([
        home / ".local" / "share" / "claude" / "versions",
        home / ".local" / "bin" / ("claude.exe" if system == "Windows" else "claude"),
        home / ".claude" / "local",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ])
    return _unique_existing(found)
