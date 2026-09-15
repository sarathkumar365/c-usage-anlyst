"""Statusline flow: install or remove the capture that records the account's usage % from Claude Code.

Claude Code passes rate_limits (5-hour and 7-day used %) to statusline commands. A tiny script appends
that input to the agent's samples file at most once a minute, then runs the member's previous statusline.
"""

from __future__ import annotations

import json
import platform
import shlex
import shutil
import stat
from pathlib import Path
from typing import Any

from claude_usage.paths import (
    ClaudePaths,
    claude_config_dirs,
    managed_settings_paths,
    statusline_chain_path,
    statusline_samples_path,
    statusline_script_path,
)
from claude_usage.store import load_config, save_config
from claude_usage.ui import c
from claude_usage.util import ensure_private_dir, read_json_file, write_json_file, write_private_text

SH_SCRIPT = """#!/bin/sh
# Claude Usage Agent: record the account's rate-limit percentages, then run the previous statusline.
umask 077
input=$(cat)
case "$input" in
  *rate_limits*)
    if [ ! -f "$1" ] || [ -z "$(find "$1" -mmin -1 2>/dev/null)" ]; then
      printf '{"t":%s,"input":%s}\\n' "$(date +%s)" "$(printf '%s' "$input" | tr -d '\\r\\n')" >> "$1"
    fi
    ;;
esac
if [ -s "$2" ]; then
  printf '%s' "$input" | sh -c "$(cat "$2")"
fi
"""

PS1_SCRIPT = """param([string]$Samples, [string]$Chain)
# Claude Usage Agent: record the account's rate-limit percentages, then run the previous statusline.
$inputText = [Console]::In.ReadToEnd()
if ($inputText -match 'rate_limits') {
  $recent = (Test-Path $Samples) -and (((Get-Date) - (Get-Item $Samples).LastWriteTime).TotalSeconds -lt 60)
  if (-not $recent) {
    $line = '{"t":' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + ',"input":' + ($inputText -replace "[\\r\\n]", '') + '}'
    [IO.File]::AppendAllText($Samples, $line + "`n")
  }
}
if ($Chain -and (Test-Path $Chain)) {
  $command = (Get-Content -Raw $Chain).Trim()
  if ($command) { $inputText | cmd /c $command }
}
"""


def _command(claude_dir) -> str:
    paths = [str(statusline_script_path()), str(statusline_samples_path()), str(statusline_chain_path(claude_dir))]
    if platform.system() == "Windows":
        if any('"' in path for path in paths):
            raise RuntimeError("The agent directory path contains a double quote; statusline capture not installed.")
        script, samples, chain = paths
        return f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" "{samples}" "{chain}"'
    return "sh " + " ".join(shlex.quote(path) for path in paths)


def _is_ours(statusline: Any) -> bool:
    return isinstance(statusline, dict) and str(statusline_script_path()) in str(statusline.get("command", ""))


def _write_settings(path: Path, settings: dict[str, Any]):
    # Edit the real file behind a dotfile-manager symlink, and keep its permissions (settings can hold secrets).
    target = path.resolve() if path.is_symlink() else path
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    write_json_file(target, settings, sort_keys=False, mode=mode)


def _is_wsl(claude_dir: Path) -> bool:
    return str(claude_dir).startswith("\\\\")


def _load_settings(path) -> dict[str, Any] | None:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def install_statusline(claude: ClaudePaths) -> int:
    forced = [str(p) for p in managed_settings_paths() if "statusLine" in read_json_file(p)]
    if forced:
        print(c(f"Statusline is set by managed settings ({', '.join(forced)}); capture not installed.", "yellow"))
        return 0
    dirs = claude_config_dirs(claude)
    if not dirs:
        print(c(f"No Claude Code config directory found at {claude.claude_dir}; capture not installed.", "yellow"))
        return 0

    script = statusline_script_path()
    ensure_private_dir(script.parent)
    write_private_text(script, PS1_SCRIPT if script.suffix == ".ps1" else SH_SCRIPT)

    config = load_config()
    chained = dict(config.get("chained_statusline") or {})
    for claude_dir in dirs:
        settings_path = claude_dir / "settings.json"
        if _is_wsl(claude_dir):
            # A Windows command would break the statusline inside WSL.
            continue
        settings = _load_settings(settings_path)
        if settings is None:
            print(c(f"Skipped {settings_path}: not valid JSON.", "yellow"))
            continue
        current = settings.get("statusLine")
        command = _command(claude_dir)
        if _is_ours(current) and current.get("command") == command:
            print(f"Already installed in {settings_path}")
            continue
        if not _is_ours(current):
            chained[str(claude_dir)] = current
            previous = current.get("command") if isinstance(current, dict) and current.get("type") == "command" else ""
            chain = statusline_chain_path(claude_dir)
            ensure_private_dir(chain.parent)
            write_private_text(chain, str(previous or ""))
            backup = settings_path.with_name("settings.json.claude-usage.bak")
            if settings_path.exists() and not backup.exists():
                shutil.copy2(settings_path, backup)
        statusline = {"type": "command", "command": command}
        if isinstance(current, dict) and "padding" in current:
            statusline["padding"] = current["padding"]
        settings["statusLine"] = statusline
        _write_settings(settings_path, settings)
        print(c(f"Usage % capture installed in {settings_path}", "green"))
    save_config({**config, "chained_statusline": chained})
    return 0


def uninstall_statusline(claude: ClaudePaths) -> int:
    config = load_config()
    chained = dict(config.get("chained_statusline") or {})
    for claude_dir in claude_config_dirs(claude):
        settings_path = claude_dir / "settings.json"
        settings = _load_settings(settings_path)
        if not settings or not _is_ours(settings.get("statusLine")):
            continue
        previous = chained.pop(str(claude_dir), None)
        if previous:
            settings["statusLine"] = previous
        else:
            settings.pop("statusLine", None)
        _write_settings(settings_path, settings)
        statusline_chain_path(claude_dir).unlink(missing_ok=True)
        print(c(f"Usage % capture removed from {settings_path}", "green"))
    save_config({**config, "chained_statusline": chained})
    return 0
