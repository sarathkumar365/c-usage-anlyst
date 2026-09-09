#!/usr/bin/env python3
"""
Claude Code Local Usage Analyzer for macOS
------------------------------------------

Analyzes Claude Code's local JSONL transcripts under:
    ~/.claude/projects/

Designed for Claude Code 2.x and newer schemas.

Key goals:
- Token usage by day / project / session / model / subagent
- Input/output/cache creation/cache read breakdown
- 5-minute vs 1-hour cache creation
- Tool-call counts and tool names
- Session duration and burn rate
- Large-context / cache-heavy sessions
- Repeated-request / possible loop indicators
- Subagent accounting
- Cross-check against ~/.claude/stats-cache.json
- Detect transcript accounting anomalies without double-counting
- JSON export for further analysis

Important accounting choices:
1. Claude Code may write multiple JSONL records for one requestId.
   We deduplicate by requestId.
2. For a request, prefer a finalized assistant record (stop_reason != null
   and/or iterations present) over early streaming snapshots.
3. We DO NOT sum top-level usage + iterations. `iterations` is treated as
   diagnostic detail because some Claude Code versions can roll multiple
   iterations into top-level context counters.
4. Subagent transcripts can occasionally lack a finalized usage record.
   Those requests are marked incomplete instead of pretending they are exact.
5. The script reports "reported transcript tokens", not Anthropic subscription
   quota consumption. API-equivalent dollars are estimates only.

Dependencies: Python 3 standard library only.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import sys
import socket
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


APP_VERSION = "0.4.0"
DEFAULT_SYNC_INTERVAL_MINUTES = 30
DEFAULT_ENROLL_URL = "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll"
DEFAULT_INGEST_URL = "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest"

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
PROJECTS_DIR = CLAUDE_DIR / "projects"
STATS_CACHE = CLAUDE_DIR / "stats-cache.json"

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
}

CHECK_STYLES = {
    "OK": "green",
    "WARN": "yellow",
    "BLOCKED": "red",
    "INFO": "cyan",
    "READY": "green",
}

MODEL_PRICES_USD_PER_M = {
    # Best-effort reference prices. Unknown/new aliases are intentionally $0.
    # These are NOT subscription billing amounts.
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-fable-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

# Tool names that are frequently expensive/noisy in transcripts.
TOOL_NAME_NORMALIZATION = {
    "mcp__": "MCP",
}


def configure_claude_dir(value: str | None):
    global CLAUDE_DIR, PROJECTS_DIR, STATS_CACHE
    if not value:
        return
    CLAUDE_DIR = Path(value).expanduser()
    PROJECTS_DIR = CLAUDE_DIR / "projects"
    STATS_CACHE = CLAUDE_DIR / "stats-cache.json"


@dataclass
class RequestUsage:
    request_id: str
    timestamp: str
    model: str
    file: str
    project: str
    session_id: str
    is_subagent: bool
    agent_id: str | None
    finalized: bool
    duplicate_records: int

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_5m_tokens: int = 0
    cache_1h_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0

    iteration_count: int = 0
    iteration_input_tokens: int = 0
    iteration_output_tokens: int = 0
    iteration_cache_read_tokens: int = 0
    iteration_cache_creation_tokens: int = 0
    advisor_iterations: int = 0

    tool_use_count: int = 0
    tool_names: tuple[str, ...] = ()

    stop_reason: str | None = None
    parent_uuid: str | None = None

    @property
    def reported_total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    @property
    def executor_iteration_context_max(self) -> int:
        # Best available estimate of largest executor context seen in iterations.
        vals = []
        for _ in range(self.iteration_count):
            pass
        return (
            self.iteration_cache_read_tokens
            + self.iteration_cache_creation_tokens
            + self.iteration_input_tokens
        )


@dataclass
class SessionSummary:
    session_id: str
    project: str
    cwd: str
    file: str
    is_subagent: bool
    agent_id: str | None
    first_ts: str
    last_ts: str
    requests: int
    finalized_requests: int
    incomplete_requests: int
    tool_calls: int
    messages: int
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_creation: int
    cache_5m: int
    cache_1h: int
    reported_total: int
    models: dict[str, int]

    @property
    def duration_seconds(self) -> float:
        a = parse_ts(self.first_ts)
        b = parse_ts(self.last_ts)
        if not a or not b:
            return 0.0
        return max(0.0, (b - a).total_seconds())

    @property
    def tokens_per_hour(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return self.reported_total / (self.duration_seconds / 3600.0)


def c(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return f"{ANSI[color]}{text}{ANSI['reset']}"


def parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fmt_int(n: int | float) -> str:
    try:
        n = int(round(n))
    except Exception:
        return "0"
    return f"{n:,}"


def fmt_compact(n: int | float) -> str:
    n = float(n)
    if abs(n) >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(int(n))


def fmt_money(n: float) -> str:
    if n <= 0:
        return "$0"
    if n >= 100:
        return f"${n:,.0f}"
    if n >= 10:
        return f"${n:,.1f}"
    return f"${n:,.2f}"


def fmt_pct(value: float) -> str:
    if 0 < value < 0.1:
        return "<0.1%"
    return f"{value:.1f}%"


def short_project_name(project: str) -> str:
    parts = [p for p in project.rstrip("/").split("/") if p]
    if not parts:
        return project or "unknown project"
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]


def sentence_join(items: list[str]) -> str:
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def friendly_tool_name(name: str) -> str:
    if name.startswith("mcp__Claude_Browser__"):
        browser_tool = name[len("mcp__Claude_Browser__"):].replace("_", " ")
        browser_tool = browser_tool.replace("javascript", "JavaScript")
        return "Browser " + browser_tool
    if name.startswith("mcp__"):
        return "MCP " + name[len("mcp__"):].replace("__", " ").replace("_", " ")
    return name


def plain_section(title: str):
    print()
    print(c(title.upper(), "bold"))
    print(c("-" * min(80, max(36, len(title) + 12)), "cyan"))


def plain_status(label: str, message: str, severity: str = "info"):
    styles = {
        "good": ("GOOD", "green"),
        "ok": ("OK", "green"),
        "info": ("INFO", "cyan"),
        "warn": ("NEEDS WORK", "yellow"),
        "bad": ("HIGH", "red"),
        "action": ("FIX", "yellow"),
    }
    tag, color = styles.get(severity, styles["info"])
    print(f"{c(tag.ljust(10), color)} {c(label + ':', 'bold')} {message}")


def plain_metric(label: str, value: str, note: str = "", severity: str = "info"):
    color = {
        "good": "green",
        "ok": "green",
        "info": "cyan",
        "warn": "yellow",
        "bad": "red",
    }.get(severity, "cyan")
    line = f"  {label.ljust(18)} {c(value, color)}"
    if note:
        line += f"  {c(note, 'dim')}"
    print(line)


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


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def write_json_file(path: Path, payload: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8", errors="ignore")
    return hashlib.sha256(encoded).hexdigest()


def safe_read_text(path: Path, max_bytes: int = 4096) -> str | None:
    try:
        with path.open("rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="ignore").strip()
    except Exception:
        return None


def platform_machine_hint() -> str | None:
    if platform.system().lower() == "linux":
        for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
            text = safe_read_text(candidate, 256)
            if text:
                return text
    if platform.system().lower() == "windows":
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                return str(value)
        except Exception:
            return None
    return None


def collect_identity(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    os_username = getpass.getuser()
    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    system = platform.system() or os.name
    machine_hint = platform_machine_hint()
    raw_fingerprint = {
        "system": system,
        "node": platform.node(),
        "hostname": hostname,
        "fqdn": fqdn,
        "machine": platform.machine(),
        "processor": platform.processor(),
        "os_username": os_username,
        "home": str(Path.home()),
        "machine_hint": machine_hint,
        "mac_node": uuid.getnode(),
    }
    machine_id = stable_hash(raw_fingerprint)[:32]
    user_id = stable_hash({
        "machine_id": machine_id,
        "os_username": os_username,
        "home": str(Path.home()),
    })[:32]
    return {
        "collector_id": config.get("collector_id") or stable_hash({
            "machine_id": machine_id,
            "created_for": config.get("org_id") or config.get("team_id") or "default",
        })[:32],
        "collector_label": config.get("collector_label") or hostname,
        "account_label": config.get("account_label") or "",
        "machine_id": machine_id,
        "machine_fingerprint_hash": stable_hash(raw_fingerprint),
        "user_id": user_id,
        "os_username": os_username,
        "hostname": hostname,
        "fqdn": fqdn,
        "home_path_hash": stable_hash(str(Path.home())),
        "platform": {
            "system": system,
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "timezone": datetime.now().astimezone().tzname(),
    }


def load_agent_config() -> dict[str, Any]:
    return read_json_file(config_path())


def load_agent_state() -> dict[str, Any]:
    return read_json_file(state_path())


def save_agent_state(state: dict[str, Any]):
    write_json_file(state_path(), state)


def register_collector(args: argparse.Namespace):
    existing = load_agent_config()
    claude_config_dir = (
        args.claude_dir
        or existing.get("claude_config_dir")
        or os.environ.get("CLAUDE_CONFIG_DIR")
        or str(Path.home() / ".claude")
    )
    collector_id = existing.get("collector_id") or stable_hash({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "random": uuid.uuid4().hex,
    })[:32]
    config = {
        **existing,
        "schema_version": 1,
        "collector_id": collector_id,
        "ingest_url": args.ingest_url,
        "collector_token": args.collector_token,
        "org_id": args.org_id or existing.get("org_id") or "",
        "account_label": args.account_label or existing.get("account_label") or "",
        "collector_label": args.collector_label or existing.get("collector_label") or socket.gethostname(),
        "claude_config_dir": claude_config_dir,
        "sync_interval_minutes": args.sync_interval_minutes or existing.get("sync_interval_minutes") or DEFAULT_SYNC_INTERVAL_MINUTES,
        "registered_at": existing.get("registered_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_file(config_path(), config)
    log_dir().mkdir(parents=True, exist_ok=True)
    print(c("Collector registered.", "green"))
    print(f"Config : {config_path()}")
    print(f"ID     : {collector_id}")
    print(f"Label  : {config['collector_label']}")


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"claude-usage-agent/{APP_VERSION}",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"raw": text}
            return {"status": response.status, "response": parsed}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from exc


def enroll_collector(args: argparse.Namespace):
    existing = load_agent_config()
    claude_config_dir = (
        args.claude_dir
        or existing.get("claude_config_dir")
        or os.environ.get("CLAUDE_CONFIG_DIR")
        or str(Path.home() / ".claude")
    )
    collector_id = existing.get("collector_id") or stable_hash({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "random": uuid.uuid4().hex,
    })[:32]
    identity_config = {
        **existing,
        "collector_id": collector_id,
        "org_id": args.org_id or existing.get("org_id") or "",
        "account_label": args.account_label or existing.get("account_label") or "",
        "collector_label": args.collector_label or existing.get("collector_label") or socket.gethostname(),
    }
    identity = collect_identity(identity_config)
    result = post_json(args.enroll_url, {
        "schema_version": 1,
        "collector_version": APP_VERSION,
        "org_id": args.org_id or existing.get("org_id") or "",
        "identity": identity,
    })
    response = result.get("response") or {}
    collector_token = response.get("collector_token")
    ingest_url = response.get("ingest_url")
    org_id = response.get("org_id") or args.org_id or existing.get("org_id") or ""
    if not collector_token or not ingest_url:
        raise RuntimeError(f"Enrollment response missing token or ingest URL: {response}")

    config = {
        **existing,
        "schema_version": 1,
        "collector_id": collector_id,
        "enroll_url": args.enroll_url,
        "ingest_url": ingest_url,
        "collector_token": collector_token,
        "org_id": org_id,
        "account_label": identity["account_label"],
        "collector_label": identity["collector_label"],
        "claude_config_dir": claude_config_dir,
        "sync_interval_minutes": int(response.get("sync_interval_minutes") or args.sync_interval_minutes or DEFAULT_SYNC_INTERVAL_MINUTES),
        "registered_at": existing.get("registered_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "enrolled_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json_file(config_path(), config)
    log_dir().mkdir(parents=True, exist_ok=True)
    print(c("Collector enrolled.", "green"))
    print(f"Config : {config_path()}")
    print(f"ID     : {collector_id}")
    print(f"Label  : {config['collector_label']}")
    print(f"Sync   : every {config['sync_interval_minutes']} minutes")


def probe_url(url: str, method: str = "GET", timeout: int = 10) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", f"claude-usage-agent/{APP_VERSION}")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 500, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def scheduler_status() -> tuple[str, str, bool]:
    system = platform.system()
    if system == "Darwin":
        return "LaunchAgent", "available" if shutil.which("launchctl") else "launchctl missing", bool(shutil.which("launchctl"))
    if system == "Linux":
        if shutil.which("systemctl"):
            return "systemd user timer", "available", True
        if shutil.which("crontab"):
            return "cron", "available", True
        return "scheduler", "systemctl and crontab missing", False
    if system == "Windows":
        return "Task Scheduler", "available" if shutil.which("schtasks") else "schtasks missing", bool(shutil.which("schtasks"))
    return "scheduler", f"unsupported OS: {system or os.name}", False


def print_preflight_check(status: str, label: str, detail: str = "", fix: str = ""):
    print(f"{c(status.ljust(8), CHECK_STYLES.get(status, 'cyan'))} {label}{(': ' + detail) if detail else ''}")
    if fix:
        print(f"{c('FIX'.ljust(8), 'cyan')} {fix}")


def run_preflight(args: argparse.Namespace) -> int:
    config = load_agent_config()
    configure_claude_dir(args.claude_dir or config.get("claude_config_dir") or os.environ.get("CLAUDE_CONFIG_DIR"))

    print()
    print(c("SYSTEM CHECK", "bold"))
    print(c("-" * 36, "cyan"))

    blocked = 0
    warnings = 0
    system = platform.system() or os.name
    machine = platform.machine().lower()
    supported = (
        (system == "Darwin" and machine in {"arm64", "aarch64", "x86_64", "amd64"}) or
        (system == "Linux" and machine in {"arm64", "aarch64", "x86_64", "amd64"}) or
        (system == "Windows" and machine in {"x86_64", "amd64"})
    )
    if supported:
        print_preflight_check("OK", "OS supported", f"{system} {platform.machine()}")
    else:
        blocked += 1
        print_preflight_check("BLOCKED", "OS supported", f"{system} {platform.machine()}", "Use macOS/Linux arm64/x64 or Windows x64.")

    try:
        app_dir().mkdir(parents=True, exist_ok=True)
        test_path = app_dir() / ".write-test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink()
        print_preflight_check("OK", "Install dir writable", str(app_dir()))
    except Exception as exc:
        blocked += 1
        print_preflight_check("BLOCKED", "Install dir writable", str(exc), "Run as a normal user with a writable home directory.")

    try:
        usage = shutil.disk_usage(str(app_dir()))
        free_mb = usage.free // (1024 * 1024)
        if free_mb >= 100:
            print_preflight_check("OK", "Disk space", f"{free_mb} MB free")
        else:
            blocked += 1
            print_preflight_check("BLOCKED", "Disk space", f"{free_mb} MB free", "Free at least 100 MB.")
    except Exception as exc:
        warnings += 1
        print_preflight_check("WARN", "Disk space", str(exc))

    now = datetime.now(timezone.utc)
    if 2024 <= now.year <= 2035:
        print_preflight_check("OK", "System clock", now.isoformat(timespec="seconds"))
    else:
        blocked += 1
        print_preflight_check("BLOCKED", "System clock", now.isoformat(timespec="seconds"), "Fix the machine date/time.")

    for label, url, method in (
        ("GitHub reachable", args.release_asset_url, "HEAD"),
        ("Supabase enroll reachable", args.enroll_url, "OPTIONS"),
        ("Supabase ingest reachable", args.ingest_url or DEFAULT_INGEST_URL, "OPTIONS"),
    ):
        if not url:
            warnings += 1
            print_preflight_check("WARN", label, "not configured")
            continue
        ok, detail = probe_url(url, method=method)
        if ok:
            print_preflight_check("OK", label, detail)
        else:
            blocked += 1
            print_preflight_check("BLOCKED", label, detail, "Check internet access, proxy/VPN, or firewall rules.")

    if CLAUDE_DIR.exists():
        print_preflight_check("OK", "Claude dir", str(CLAUDE_DIR))
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude dir", f"missing at {CLAUDE_DIR}", "Install/use Claude Code once, or set CLAUDE_CONFIG_DIR.")

    if PROJECTS_DIR.exists():
        try:
            has_jsonl = any(PROJECTS_DIR.rglob("*.jsonl"))
        except Exception:
            has_jsonl = False
        if has_jsonl:
            print_preflight_check("OK", "Claude transcripts", "JSONL files found")
        else:
            warnings += 1
            print_preflight_check("WARN", "Claude transcripts", "no JSONL files found yet")
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude projects dir", f"missing at {PROJECTS_DIR}")

    if STATS_CACHE.exists():
        print_preflight_check("OK", "Claude stats cache", str(STATS_CACHE))
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude stats cache", f"missing at {STATS_CACHE}")

    scheduler, detail, scheduler_ok = scheduler_status()
    if scheduler_ok:
        print_preflight_check("OK", "Scheduler available", scheduler)
    else:
        blocked += 1
        print_preflight_check("BLOCKED", "Scheduler available", detail, "Install a user-level scheduler or run sync manually.")

    if config:
        state = load_agent_state()
        print_preflight_check("INFO", "Existing install", config.get("collector_id", "configured"))
        print_preflight_check("INFO", "Last sync", state.get("last_sync_at", "never"))
    else:
        print_preflight_check("INFO", "Existing install", "none")

    print()
    if blocked:
        print_preflight_check("BLOCKED", "Install cannot continue", f"{blocked} blocking issue(s), {warnings} warning(s)")
        return 2
    print_preflight_check("READY", "Install can continue", f"{warnings} warning(s)")
    return 0


def print_status():
    config = load_agent_config()
    configure_claude_dir(config.get("claude_config_dir") or os.environ.get("CLAUDE_CONFIG_DIR"))
    state = load_agent_state()
    identity = collect_identity(config)
    anomalies = discover_anomalies()

    print()
    print(c("=" * 80, "cyan"))
    print(c(" CLAUDE USAGE COLLECTOR STATUS", "bold"))
    print(c("=" * 80, "cyan"))
    plain_section("Install")
    plain_metric("Version", APP_VERSION, "", "info")
    plain_metric("Config", str(config_path()), "found" if config else "missing", "good" if config else "warn")
    plain_metric("Ingest URL", config.get("ingest_url", "not configured"), "", "good" if config.get("ingest_url") else "warn")
    plain_metric("Collector", identity["collector_label"], identity["collector_id"], "info")
    plain_metric("Account label", identity["account_label"] or "not set", "manual Claude account/team label", "warn" if not identity["account_label"] else "good")

    plain_section("Machine")
    plain_metric("OS user", identity["os_username"], "", "info")
    plain_metric("Hostname", identity["hostname"], "", "info")
    plain_metric("Platform", f"{identity['platform']['system']} {identity['platform']['release']}", identity["platform"]["machine"], "info")
    plain_metric("Machine ID", identity["machine_id"], "hashed fingerprint", "info")

    plain_section("Claude Data")
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        claude_dir_source = "from CLAUDE_CONFIG_DIR"
    elif config.get("claude_config_dir"):
        claude_dir_source = "from collector config"
    else:
        claude_dir_source = "default"
    plain_metric("Claude dir", str(CLAUDE_DIR), claude_dir_source, "info")
    plain_metric("Projects dir", str(PROJECTS_DIR), "exists" if PROJECTS_DIR.exists() else "missing", "good" if PROJECTS_DIR.exists() else "bad")
    plain_metric("Last sync", state.get("last_success_at", "never"), "", "good" if state.get("last_success_at") else "warn")
    if anomalies:
        for anomaly in anomalies:
            plain_status("Anomaly", anomaly["message"], anomaly.get("severity", "warn"))
    else:
        plain_status("Anomalies", "No obvious Claude data location anomalies found.", "good")
    print()


def fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "n/a"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m and len(parts) < 2:
        parts.append(f"{m}m")
    if not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def safe_project_from_path(path: Path) -> str:
    try:
        rel = path.relative_to(PROJECTS_DIR)
        first = rel.parts[0] if rel.parts else path.parent.name
    except Exception:
        first = path.parent.name

    # Claude encodes path separators as "-". Preserve human-friendly path
    # where possible.
    if first.startswith("-Users-"):
        decoded = first.replace("-", "/")
        if decoded.startswith("/Users/"):
            return decoded
        # Fallback: strip leading username-ish encoding.
        return first
    return first


def is_subagent_path(path: Path) -> bool:
    return "subagents" in path.parts


def agent_id_from_path(path: Path) -> str | None:
    for part in path.parts:
        if part.startswith("agent-") and part.endswith(".jsonl"):
            return part[:-6]
    return None


def session_id_from_path(path: Path) -> str | None:
    try:
        rel = path.relative_to(PROJECTS_DIR)
    except Exception:
        return None
    parts = rel.parts
    for p in parts:
        if re.fullmatch(r"[0-9a-fA-F-]{36}", p):
            return p
    # Main session filenames are UUID.jsonl.
    stem = path.stem
    if re.fullmatch(r"[0-9a-fA-F-]{36}", stem):
        return stem
    return None


def discover_jsonl() -> list[Path]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(PROJECTS_DIR.rglob("*.jsonl"))


def json_load_line(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def tool_names_from_content(content: Any) -> list[str]:
    names: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if isinstance(name, str):
                    names.append(name)
    elif isinstance(content, dict):
        if content.get("type") == "tool_use" and isinstance(content.get("name"), str):
            names.append(content["name"])
    return names


def usage_is_final(msg: dict[str, Any], usage: dict[str, Any]) -> bool:
    if usage.get("iterations"):
        return True
    stop_reason = msg.get("stop_reason")
    if stop_reason not in (None, "", "null"):
        return True
    # Some versions may use stop_details.
    if msg.get("stop_details"):
        return True
    return False


def extract_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    return usage


def choose_best_record(records: list[tuple[dict[str, Any], dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Pick the best usage record for one requestId.

    Preference:
      1. finalized record with iterations
      2. finalized record
      3. largest cache-read + cache-create + output
    """
    def score(item: tuple[dict[str, Any], dict[str, Any]]) -> tuple[int, int, int]:
        obj, usage = item
        msg = obj.get("message") or {}
        finalized = usage_is_final(msg, usage)
        iterations = 1 if usage.get("iterations") else 0
        size = sum(
            int(usage.get(k) or 0)
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
        return (int(finalized), iterations, size)

    return max(records, key=score)


def parse_all(
    paths: Iterable[Path],
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[RequestUsage], dict[str, SessionSummary], Counter]:
    requests: list[RequestUsage] = []
    record_type_counter: Counter = Counter()
    session_buckets: dict[str, list[RequestUsage]] = defaultdict(list)

    for path in paths:
        project = safe_project_from_path(path)
        session_id = session_id_from_path(path) or f"file:{path.name}"
        subagent = is_subagent_path(path)
        agent_id = agent_id_from_path(path)

        by_request: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        fallback_counter = 0
        file_model_hint = "unknown"

        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    obj = json_load_line(line)
                    if not obj:
                        continue

                    record_type_counter[str(obj.get("type"))] += 1

                    ts = parse_ts(obj.get("timestamp"))
                    if start and ts and ts < start:
                        continue
                    if end and ts and ts >= end:
                        continue

                    msg = obj.get("message")
                    if isinstance(msg, dict) and msg.get("model"):
                        file_model_hint = str(msg["model"])

                    usage = extract_usage(obj)
                    if usage is None:
                        continue

                    msg = obj.get("message") or {}
                    request_id = msg.get("id") or obj.get("requestId")
                    if not request_id:
                        # A tiny number of versions/events can omit ids.
                        request_id = f"fallback:{obj.get('uuid') or fallback_counter}"
                        fallback_counter += 1

                    by_request[str(request_id)].append((obj, usage))
        except (OSError, UnicodeError):
            continue

        for request_id, records_for_request in by_request.items():
            obj, usage = choose_best_record(records_for_request)
            msg = obj.get("message") or {}
            ts = parse_ts(obj.get("timestamp"))
            timestamp = ts.isoformat() if ts else str(obj.get("timestamp") or "")

            cache = usage.get("cache_creation") or {}
            if not isinstance(cache, dict):
                cache = {}

            iterations = usage.get("iterations") or []
            if not isinstance(iterations, list):
                iterations = []

            iter_in = sum(int((x or {}).get("input_tokens") or 0) for x in iterations if isinstance(x, dict))
            iter_out = sum(int((x or {}).get("output_tokens") or 0) for x in iterations if isinstance(x, dict))
            iter_cr = sum(int((x or {}).get("cache_creation_input_tokens") or 0) for x in iterations if isinstance(x, dict))
            iter_rr = sum(int((x or {}).get("cache_read_input_tokens") or 0) for x in iterations if isinstance(x, dict))
            advisor_count = sum(
                1 for x in iterations
                if isinstance(x, dict) and x.get("type") == "advisor_message"
            )

            tools = tool_names_from_content(msg.get("content"))
            server = usage.get("server_tool_use") or {}
            if not isinstance(server, dict):
                server = {}

            request = RequestUsage(
                request_id=request_id,
                timestamp=timestamp,
                model=str(msg.get("model") or file_model_hint or "unknown"),
                file=str(path),
                project=project,
                session_id=session_id,
                is_subagent=subagent,
                agent_id=agent_id,
                finalized=usage_is_final(msg, usage),
                duplicate_records=len(records_for_request),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
                cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                cache_5m_tokens=int(cache.get("ephemeral_5m_input_tokens") or 0),
                cache_1h_tokens=int(cache.get("ephemeral_1h_input_tokens") or 0),
                web_search_requests=int(server.get("web_search_requests") or 0),
                web_fetch_requests=int(server.get("web_fetch_requests") or 0),
                iteration_count=len(iterations),
                iteration_input_tokens=iter_in,
                iteration_output_tokens=iter_out,
                iteration_cache_read_tokens=iter_rr,
                iteration_cache_creation_tokens=iter_cr,
                advisor_iterations=advisor_count,
                tool_use_count=len(tools),
                tool_names=tuple(tools),
                stop_reason=str(msg.get("stop_reason")) if msg.get("stop_reason") is not None else None,
                parent_uuid=obj.get("parentUuid"),
            )

            requests.append(request)
            session_buckets[session_id].append(request)

    sessions: dict[str, SessionSummary] = {}
    for sid, reqs in session_buckets.items():
        reqs.sort(key=lambda r: parse_ts(r.timestamp) or datetime.min.replace(tzinfo=timezone.utc))
        first = reqs[0]
        last = reqs[-1]
        models = Counter(r.model for r in reqs)
        sessions[sid] = SessionSummary(
            session_id=sid,
            project=first.project,
            cwd="",
            file=first.file,
            is_subagent=first.is_subagent,
            agent_id=first.agent_id,
            first_ts=first.timestamp,
            last_ts=last.timestamp,
            requests=len(reqs),
            finalized_requests=sum(r.finalized for r in reqs),
            incomplete_requests=sum(not r.finalized for r in reqs),
            tool_calls=sum(r.tool_use_count for r in reqs),
            messages=len(reqs),
            input_tokens=sum(r.input_tokens for r in reqs),
            output_tokens=sum(r.output_tokens for r in reqs),
            cache_read=sum(r.cache_read_input_tokens for r in reqs),
            cache_creation=sum(r.cache_creation_input_tokens for r in reqs),
            cache_5m=sum(r.cache_5m_tokens for r in reqs),
            cache_1h=sum(r.cache_1h_tokens for r in reqs),
            reported_total=sum(r.reported_total for r in reqs),
            models=dict(models),
        )
    return requests, sessions, record_type_counter


def load_stats_cache() -> dict[str, Any] | None:
    if not STATS_CACHE.exists():
        return None
    try:
        return json.loads(STATS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def calculate_estimated_cost(r: RequestUsage) -> float:
    prices = MODEL_PRICES_USD_PER_M.get(r.model)
    if not prices:
        return 0.0
    in_price, out_price = prices
    # Conservative API-style estimate:
    # cache reads are charged differently than normal input; exact pricing varies.
    # We therefore include them at 10% of input price as a rough diagnostic, not a bill.
    normal_input = r.input_tokens + r.cache_creation_input_tokens
    cache_input = r.cache_read_input_tokens
    return (
        normal_input / 1_000_000 * in_price
        + cache_input / 1_000_000 * in_price * 0.10
        + r.output_tokens / 1_000_000 * out_price
    )


def group_sum(rows: Iterable[RequestUsage], key_fn, value_fn=lambda r: r.reported_total):
    out = defaultdict(int)
    for r in rows:
        out[key_fn(r)] += value_fn(r)
    return out


def date_key(ts: str) -> str:
    dt = parse_ts(ts)
    return dt.astimezone().strftime("%Y-%m-%d") if dt else "unknown"


def terminal_width() -> int:
    try:
        return max(80, min(180, os.get_terminal_size().columns))
    except OSError:
        return 120


def print_table(headers: list[str], rows: list[list[Any]], max_rows: int = 20):
    if not rows:
        print("(none)")
        return
    rows = rows[:max_rows]
    converted = [[str(x) for x in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in converted:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = min(max(widths[i], len(cell)), 42)

    def fit(s: str, w: int) -> str:
        if len(s) <= w:
            return s
        return s[: max(1, w - 1)] + "…"

    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in converted:
        print("  ".join(fit(row[i], widths[i]).ljust(widths[i]) for i in range(len(headers))))


def analyze(requests: list[RequestUsage], sessions: dict[str, SessionSummary], stats: dict[str, Any] | None):
    if not requests:
        print(c("No finalized/usage-bearing assistant records found.", "red"))
        return

    total = sum(r.reported_total for r in requests)
    inp = sum(r.input_tokens for r in requests)
    out = sum(r.output_tokens for r in requests)
    cr = sum(r.cache_creation_input_tokens for r in requests)
    rr = sum(r.cache_read_input_tokens for r in requests)
    cache_total = cr + rr
    tools = sum(r.tool_use_count for r in requests)
    finalized = sum(r.finalized for r in requests)
    incomplete = len(requests) - finalized
    advisor = sum(r.advisor_iterations for r in requests)
    web_search = sum(r.web_search_requests for r in requests)
    web_fetch = sum(r.web_fetch_requests for r in requests)
    duplicate_records = sum(max(0, r.duplicate_records - 1) for r in requests)

    print()
    print(c("═" * 90, "cyan"))
    print(c(" CLAUDE CODE LOCAL USAGE ANALYZER", "bold"))
    print(c("═" * 90, "cyan"))
    print(f"Claude directory : {CLAUDE_DIR}")
    print(f"Transcript files : {len(set(r.file for r in requests)):,}")
    print(f"Deduped requests : {len(requests):,}")
    print(f"Finalized        : {finalized:,}")
    print(f"Incomplete       : {incomplete:,}")
    print(f"Duplicate records removed: {duplicate_records:,}")
    print()

    print(c("TOTAL REPORTED TOKEN VOLUME", "bold"))
    print(f"  Total reported : {fmt_int(total)} ({fmt_compact(total)})")
    print(f"  Input          : {fmt_int(inp)} ({total_pct(inp, total):.1f}%)")
    print(f"  Output         : {fmt_int(out)} ({total_pct(out, total):.1f}%)")
    print(f"  Cache create   : {fmt_int(cr)} ({total_pct(cr, total):.1f}%)")
    print(f"  Cache read     : {fmt_int(rr)} ({total_pct(rr, total):.1f}%)")
    if total:
        print(f"  Cache share    : {cache_total / total * 100:.1f}%")
    print(f"  5m cache       : {fmt_int(sum(r.cache_5m_tokens for r in requests))}")
    print(f"  1h cache       : {fmt_int(sum(r.cache_1h_tokens for r in requests))}")
    print()

    print(c("MODELS", "bold"))
    by_model = group_sum(requests, lambda r: r.model)
    model_rows = []
    for model, tokens in sorted(by_model.items(), key=lambda x: x[1], reverse=True):
        rows_req = [r for r in requests if r.model == model]
        model_rows.append([
            model,
            fmt_compact(tokens),
            f"{tokens / total * 100:.1f}%",
            fmt_int(len(rows_req)),
            fmt_int(sum(r.cache_read_input_tokens for r in rows_req)),
        ])
    print_table(["MODEL", "TOKENS", "SHARE", "REQ", "CACHE READ"], model_rows, 20)
    print()

    print(c("PROJECTS", "bold"))
    by_project = group_sum(requests, lambda r: r.project)
    project_rows = []
    for project, tokens in sorted(by_project.items(), key=lambda x: x[1], reverse=True):
        reqs = [r for r in requests if r.project == project]
        project_rows.append([
            project,
            fmt_compact(tokens),
            f"{tokens / total * 100:.1f}%",
            fmt_int(len(reqs)),
            len(set(r.session_id for r in reqs)),
        ])
    print_table(["PROJECT", "TOKENS", "SHARE", "REQ", "SESSIONS"], project_rows, 15)
    print()

    print(c("SUBAGENTS", "bold"))
    sub = [r for r in requests if r.is_subagent]
    main = [r for r in requests if not r.is_subagent]
    sub_total = sum(r.reported_total for r in sub)
    print(f"  Main sessions : {fmt_int(sum(r.reported_total for r in main))}")
    print(f"  Subagents     : {fmt_int(sub_total)} ({(sub_total / total * 100 if total else 0):.1f}%)")
    print(f"  Subagent reqs : {fmt_int(len(sub))}")
    if sub:
        by_agent = defaultdict(int)
        for r in sub:
            by_agent[(r.project, r.session_id, r.agent_id or "?")] += r.reported_total
        rows = []
        for (proj, sid, aid), tokens in sorted(by_agent.items(), key=lambda x: x[1], reverse=True)[:20]:
            rows.append([proj, sid[:12], aid or "?", fmt_compact(tokens), f"{tokens/total*100:.1f}%"])
        print_table(["PROJECT", "SESSION", "AGENT", "TOKENS", "TOTAL SHARE"], rows, 20)
    print()

    print(c("TOOL ACTIVITY", "bold"))
    tool_counter = Counter()
    for r in requests:
        for name in r.tool_names:
            tool_counter[name] += 1
    print(f"  Tool calls detected : {fmt_int(tools)}")
    print(f"  Server web searches : {fmt_int(web_search)}")
    print(f"  Server web fetches  : {fmt_int(web_fetch)}")
    print(f"  Advisor iterations  : {fmt_int(advisor)}")
    tool_rows = [[name, count] for name, count in tool_counter.most_common(25)]
    print_table(["TOOL", "CALLS"], tool_rows, 25)
    print()

    print(c("DAILY USAGE", "bold"))
    daily = group_sum(requests, lambda r: date_key(r.timestamp))
    daily_rows = []
    for day, tokens in sorted(daily.items(), key=lambda x: x[1], reverse=True)[:20]:
        day_reqs = [r for r in requests if date_key(r.timestamp) == day]
        daily_rows.append([
            day,
            fmt_compact(tokens),
            f"{tokens/total*100:.1f}%",
            fmt_int(len(day_reqs)),
            fmt_int(sum(r.tool_use_count for r in day_reqs)),
        ])
    print_table(["DATE", "TOKENS", "SHARE", "REQ", "TOOLS"], daily_rows, 20)
    print()

    print(c("TOP SESSIONS", "bold"))
    session_rows = []
    for s in sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True)[:25]:
        session_rows.append([
            s.project,
            s.session_id[:12],
            fmt_compact(s.reported_total),
            f"{s.reported_total/total*100:.1f}%",
            fmt_int(s.requests),
            fmt_duration(s.duration_seconds),
            f"{fmt_compact(s.tokens_per_hour)}/h" if s.tokens_per_hour else "n/a",
            "subagent" if s.is_subagent else "main",
        ])
    print_table(["PROJECT", "SESSION", "TOKENS", "SHARE", "REQ", "DURATION", "TOK/H", "TYPE"], session_rows, 25)
    print()

    print(c("USAGE / CONTEXT DIAGNOSTICS", "bold"))
    diagnostics: list[tuple[str, str, str]] = []

    # Cache dominance
    if total and rr / total > 0.60:
        diagnostics.append(("HIGH", "Cache-read dominated", f"Cache reads are {rr/total*100:.1f}% of reported transcript volume."))
    if total and cr / total > 0.15:
        diagnostics.append(("HIGH", "Large cache creation", f"Cache creation is {cr/total*100:.1f}% of reported volume."))

    # Session concentration
    if sessions:
        largest = max(sessions.values(), key=lambda s: s.reported_total)
        share = largest.reported_total / total if total else 0
        if share > 0.20:
            diagnostics.append(("HIGH", "Single-session concentration", f"{largest.session_id[:12]} accounts for {share*100:.1f}% of all reported tokens."))

    # Extremely large individual requests
    per_req = sorted(requests, key=lambda r: r.reported_total, reverse=True)
    med = statistics.median([r.reported_total for r in requests])
    if med > 0 and per_req[0].reported_total > med * 20:
        diagnostics.append(("HIGH", "Outlier request", f"Largest request is {per_req[0].reported_total/med:.1f}× the median request."))

    # Subagents
    if total and sub_total / total > 0.40:
        diagnostics.append(("HIGH", "Subagent-heavy workload", f"Subagents account for {sub_total/total*100:.1f}% of reported transcript tokens."))

    # Incomplete subagent accounting
    incomplete_sub = sum(1 for r in sub if not r.finalized)
    if incomplete_sub:
        diagnostics.append(("WARN", "Incomplete subagent records", f"{incomplete_sub:,} subagent requests have no obvious finalized usage snapshot; totals may undercount those requests."))

    # Advisor iterations
    if advisor:
        diagnostics.append(("WARN", "Advisor iterations detected", f"{advisor:,} advisor iterations were recorded. Do not add iterations to top-level usage; they may already be rolled up."))

    # Cache re-write pattern
    rewrite_candidates = 0
    for r in requests:
        if r.cache_5m_tokens > 0 and r.cache_read_input_tokens > 0 and r.cache_5m_tokens > max(50_000, r.cache_read_input_tokens * 0.25):
            rewrite_candidates += 1
    if rewrite_candidates:
        diagnostics.append(("WARN", "Possible cache churn", f"{rewrite_candidates:,} requests show substantial simultaneous 5m cache creation and cache read activity."))

    # Duplicate handling
    if duplicate_records:
        diagnostics.append(("INFO", "Streaming duplicates removed", f"{duplicate_records:,} repeated JSONL records were collapsed by requestId before summing."))

    if not diagnostics:
        diagnostics.append(("OK", "No major heuristic anomaly", "The analyzer did not find a strong usage anomaly from the local data."))

    for sev, name, detail in diagnostics:
        color = "red" if sev == "HIGH" else "yellow" if sev == "WARN" else "cyan" if sev == "INFO" else "green"
        print(f"  {c(sev, color)}  {name}: {detail}")
    print()

    print(c("CANDIDATE EXPENSIVE REQUESTS", "bold"))
    rows = []
    for r in per_req[:20]:
        rows.append([
            date_key(r.timestamp),
            r.project,
            r.model,
            r.request_id[:12],
            fmt_compact(r.reported_total),
            fmt_compact(r.cache_read_input_tokens),
            fmt_compact(r.cache_creation_input_tokens),
            r.stop_reason or "?",
            "final" if r.finalized else "partial",
            f"{len(r.tool_names)}",
        ])
    print_table(["DATE", "PROJECT", "MODEL", "REQUEST", "TOTAL", "CACHE READ", "CACHE CREATE", "STOP", "STATE", "TOOLS"], rows, 20)
    print()

    print(c("CROSS-CHECK: stats-cache.json", "bold"))
    if stats:
        cache_date = stats.get("lastComputedDate")
        print(f"  stats-cache lastComputedDate : {cache_date}")
        total_sessions = stats.get("totalSessions")
        total_messages = stats.get("totalMessages")
        print(f"  stats totalSessions           : {total_sessions}")
        print(f"  stats totalMessages           : {total_messages}")
        if stats.get("dailyModelTokens"):
            daily_model_total = 0
            for item in stats["dailyModelTokens"]:
                for v in (item.get("tokensByModel") or {}).values():
                    try:
                        daily_model_total += int(v)
                    except Exception:
                        pass
            print(f"  stats dailyModelTokens sum    : {fmt_int(daily_model_total)}")
        if cache_date and cache_date < datetime.now().strftime("%Y-%m-%d"):
            print(c("  NOTE: stats-cache is stale relative to current date; JSONL is used for current/historical detail.", "yellow"))
    else:
        print("  stats-cache.json not available or unreadable.")

    print()
    print(c("ACCURACY NOTES", "bold"))
    print("  • Counts are based on finalized/deduplicated JSONL usage snapshots when available.")
    print("  • Top-level usage is used for totals; iterations are diagnostics, not added again.")
    print("  • Subagent requests without a finalized snapshot are flagged as incomplete.")
    print("  • Local token totals are NOT the same thing as your Claude Pro/Max remaining quota.")
    print("  • Any dollar figure derived from model prices is an API-style estimate, not subscription billing.")
    print()


def plain_usage_story(
    requests: list[RequestUsage],
    sessions: dict[str, SessionSummary],
    stats: dict[str, Any] | None,
):
    if not requests:
        print(c("No usage-bearing Claude Code records found for this filter.", "red"))
        return

    total = sum(r.reported_total for r in requests)
    inp = sum(r.input_tokens for r in requests)
    out = sum(r.output_tokens for r in requests)
    cr = sum(r.cache_creation_input_tokens for r in requests)
    rr = sum(r.cache_read_input_tokens for r in requests)
    cache_total = cr + rr
    finalized = sum(r.finalized for r in requests)
    incomplete = len(requests) - finalized
    duplicate_records = sum(max(0, r.duplicate_records - 1) for r in requests)
    tool_calls = sum(r.tool_use_count for r in requests)
    estimated_cost = sum(calculate_estimated_cost(r) for r in requests)

    by_model = group_sum(requests, lambda r: r.model)
    top_models = sorted(by_model.items(), key=lambda x: x[1], reverse=True)

    by_project = group_sum(requests, lambda r: r.project)
    top_projects = sorted(by_project.items(), key=lambda x: x[1], reverse=True)

    daily = group_sum(requests, lambda r: date_key(r.timestamp))
    top_days = sorted(daily.items(), key=lambda x: x[1], reverse=True)

    top_sessions = sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True)
    sub_total = sum(r.reported_total for r in requests if r.is_subagent)
    main_total = total - sub_total

    cache_pct = cache_total / total * 100 if total else 0.0
    cache_read_pct = rr / total * 100 if total else 0.0
    output_pct = total_pct(out, total)
    input_pct = total_pct(inp, total)
    sub_pct = sub_total / total * 100 if total else 0.0
    largest_session_pct = (
        top_sessions[0].reported_total / total * 100
        if top_sessions and total
        else 0.0
    )
    top_project_pct = top_projects[0][1] / total * 100 if top_projects and total else 0.0
    top_model_pct = top_models[0][1] / total * 100 if top_models and total else 0.0

    health_score = 0
    if cache_read_pct > 80:
        health_score += 2
    elif cache_read_pct > 60:
        health_score += 1
    if largest_session_pct > 25:
        health_score += 2
    elif largest_session_pct > 15:
        health_score += 1
    if top_project_pct > 75:
        health_score += 1
    if top_model_pct > 75 and top_models and top_models[0][0].startswith("claude-opus"):
        health_score += 1
    if sub_pct > 25:
        health_score += 1

    if health_score >= 5:
        verdict = ("High usage pressure", "bad")
    elif health_score >= 2:
        verdict = ("Improvement needed", "warn")
    else:
        verdict = ("Looks manageable", "good")

    print()
    print(c("=" * 80, "cyan"))
    print(c(" CLAUDE USAGE - PLAIN ENGLISH REPORT", "bold"))
    print(c("=" * 80, "cyan"))

    plain_section("Overall")
    plain_metric("Verdict", verdict[0], severity=verdict[1])
    plain_metric("Total usage", f"{fmt_compact(total)} tokens", f"{fmt_int(len(requests))} requests", "info")
    plain_metric("Context reuse", fmt_pct(cache_pct), "cache read + cache create", "bad" if cache_pct > 90 else "warn")
    plain_metric("Model output", fmt_pct(output_pct), "actual answer text", "ok" if output_pct < 10 else "warn")
    plain_metric("Typed/new input", fmt_pct(input_pct), "your direct prompt/input share", "ok" if input_pct < 10 else "warn")
    if estimated_cost:
        plain_metric("API estimate", fmt_money(estimated_cost), "not your subscription bill", "warn")

    plain_section("Health Check")
    if cache_total and total:
        if cache_read_pct > 80:
            plain_status(
                "Cache usage",
                f"Very high. {fmt_pct(cache_read_pct)} is cached context being reread.",
                "bad",
            )
            plain_status(
                "Meaning",
                "The big number is mostly Claude carrying a large session/project context forward.",
                "info",
            )
        elif cache_read_pct > 60:
            plain_status(
                "Cache usage",
                f"Elevated. {fmt_pct(cache_read_pct)} is cached context being reread.",
                "warn",
            )
        else:
            plain_status("Cache usage", "Reasonable. Cached context is not dominating the report.", "good")

    if largest_session_pct > 25:
        plain_status(
            "Session size",
            f"One session is very large at {fmt_pct(largest_session_pct)} of total usage.",
            "bad",
        )
    elif largest_session_pct > 15:
        plain_status(
            "Session size",
            f"One session is doing a lot of work at {fmt_pct(largest_session_pct)} of total usage.",
            "warn",
        )
    else:
        plain_status("Session size", "Usage is not dominated by one session.", "good")

    if sub_pct > 25:
        plain_status("Subagents", f"High at {fmt_pct(sub_pct)} of total usage.", "warn")
    elif sub_total:
        plain_status("Subagents", f"Low impact at {fmt_pct(sub_pct)} of total usage.", "good")
    else:
        plain_status("Subagents", "No subagent usage found in this filter.", "good")

    if incomplete:
        plain_status(
            "Data quality",
            f"{fmt_int(incomplete)} requests were incomplete, so usage may be slightly undercounted.",
            "warn",
        )
    else:
        plain_status("Data quality", "All usage records in this filter look finalized.", "good")

    plain_section("Main Drivers")

    if top_projects:
        for i, (project, tokens) in enumerate(top_projects[:3], start=1):
            share = tokens / total * 100 if total else 0.0
            severity = "bad" if i == 1 and share > 75 else "warn" if i == 1 and share > 50 else "info"
            plain_metric(f"Project #{i}", short_project_name(project), f"{fmt_compact(tokens)} / {fmt_pct(share)}", severity)

    if top_models:
        for i, (model, tokens) in enumerate(top_models[:3], start=1):
            share = tokens / total * 100 if total else 0.0
            severity = "bad" if i == 1 and model.startswith("claude-opus") and share > 75 else "warn" if i == 1 and share > 50 else "info"
            plain_metric(f"Model #{i}", model, f"{fmt_compact(tokens)} / {fmt_pct(share)}", severity)

    if top_days:
        day, tokens = top_days[0]
        share = tokens / total * 100 if total else 0.0
        plain_metric("Heaviest day", day, f"{fmt_compact(tokens)} / {fmt_pct(share)}", "warn" if share > 25 else "info")

    if top_sessions:
        largest = top_sessions[0]
        plain_metric(
            "Largest session",
            largest.session_id[:12],
            (
                f"{short_project_name(largest.project)}, {fmt_pct(largest_session_pct)}, "
                f"{fmt_duration(largest.duration_seconds)}, {fmt_compact(largest.tokens_per_hour)}/hour"
            ),
            "bad" if largest_session_pct > 25 else "warn" if largest_session_pct > 15 else "info",
        )

    if sub_total:
        plain_metric("Main sessions", fmt_compact(main_total), "normal chat/tool sessions", "info")
        plain_metric("Subagents", fmt_compact(sub_total), fmt_pct(sub_pct), "warn" if sub_pct > 25 else "ok")

    tool_counter = Counter()
    for r in requests:
        for name in r.tool_names:
            tool_counter[name] += 1
    if tool_counter:
        tool_phrases = [f"{friendly_tool_name(name)} ({count})" for name, count in tool_counter.most_common(3)]
        plain_metric("Top tools", sentence_join(tool_phrases), "file/code inspection activity", "info")
    elif tool_calls:
        plain_metric("Tool calls", fmt_int(tool_calls), "", "info")

    reasons: list[str] = []
    if total and rr / total > 0.60:
        reasons.append("large cached context being replayed across requests")
    if top_sessions and total and top_sessions[0].reported_total / total > 0.20:
        reasons.append("one very large session dominating the totals")
    if top_projects and total and top_projects[0][1] / total > 0.50:
        reasons.append(f"most work happening in {short_project_name(top_projects[0][0])}")
    if top_models and top_models[0][0] != "<synthetic>" and total and top_models[0][1] / total > 0.50:
        reasons.append(f"most requests using {top_models[0][0]}")
    if sub_total and total and sub_total / total > 0.25:
        reasons.append("subagent or parallel task usage")

    plain_section("Why Usage Is High")
    if reasons:
        for reason in reasons:
            plain_status("Driver", reason + ".", "bad" if "large cached context" in reason or "one very large session" in reason else "warn")
    else:
        plain_status("Driver", "Usage looks spread out; there is no single obvious driver.", "good")

    recommendations: list[str] = []
    if total and rr / total > 0.60:
        recommendations.append("Start a fresh session after a task is done, especially after large file reads or long debugging runs.")
    if top_sessions and total and top_sessions[0].reported_total / total > 0.20:
        recommendations.append("Break very long sessions into smaller task-focused chats.")
    if tool_counter.get("Read", 0) + tool_counter.get("Bash", 0) > max(20, len(requests) * 0.20):
        recommendations.append("Ask Claude to inspect only the files needed for the current change.")
    if sub_total and total and sub_total / total > 0.25:
        recommendations.append("Use subagents for broad searches, but avoid them for small direct edits.")
    if top_models and top_models[0][0].startswith("claude-opus"):
        recommendations.append("Use a cheaper/faster model for routine edits when quality requirements allow it.")

    plain_section("Recommended Fixes")
    if recommendations:
        for rec in recommendations[:5]:
            plain_status("Next step", rec, "action")
    else:
        plain_status("Next step", "Keep using shorter, task-specific sessions; no major waste pattern stands out.", "good")

    notes: list[str] = []
    if incomplete:
        notes.append(f"{fmt_int(incomplete)} requests were incomplete and may slightly undercount usage")
    if duplicate_records:
        notes.append(f"{fmt_int(duplicate_records)} streaming duplicate records were removed before counting")
    if stats and stats.get("lastComputedDate") and stats.get("lastComputedDate") < datetime.now().strftime("%Y-%m-%d"):
        notes.append(f"stats-cache.json is stale as of {stats.get('lastComputedDate')}, so JSONL transcripts were used")

    if notes:
        plain_section("Accounting Notes")
        for note in notes:
            plain_status("Note", note.replace("jsonl", "JSONL") + ".", "info")
    print()


def discover_anomalies(paths: list[Path] | None = None) -> list[dict[str, str]]:
    anomalies: list[dict[str, str]] = []
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    default_dir = Path.home() / ".claude"

    if env_dir:
        anomalies.append({
            "code": "custom_claude_config_dir",
            "severity": "info",
            "message": f"CLAUDE_CONFIG_DIR is set, using {CLAUDE_DIR}.",
        })

    if not CLAUDE_DIR.exists():
        anomalies.append({
            "code": "claude_dir_missing",
            "severity": "bad",
            "message": f"Claude config directory was not found at {CLAUDE_DIR}.",
        })
        return anomalies

    if env_dir and default_dir.exists() and default_dir.resolve() != CLAUDE_DIR.resolve():
        anomalies.append({
            "code": "default_and_custom_dirs_exist",
            "severity": "warn",
            "message": f"Both default {default_dir} and custom {CLAUDE_DIR} Claude directories exist.",
        })

    if not PROJECTS_DIR.exists():
        anomalies.append({
            "code": "projects_dir_missing",
            "severity": "bad",
            "message": f"Claude projects directory was not found at {PROJECTS_DIR}.",
        })

    if not STATS_CACHE.exists():
        anomalies.append({
            "code": "stats_cache_missing",
            "severity": "warn",
            "message": f"Claude stats cache was not found at {STATS_CACHE}.",
        })
    else:
        stats = load_stats_cache()
        cache_date = stats.get("lastComputedDate") if stats else None
        if cache_date and cache_date < datetime.now().strftime("%Y-%m-%d"):
            anomalies.append({
                "code": "stats_cache_stale",
                "severity": "info",
                "message": f"stats-cache.json is stale as of {cache_date}; JSONL transcripts are used.",
            })

    try:
        projects_files = paths if paths is not None else discover_jsonl()
    except Exception as exc:
        anomalies.append({
            "code": "jsonl_discovery_failed",
            "severity": "bad",
            "message": f"Could not scan Claude JSONL files: {exc}.",
        })
        projects_files = []

    if PROJECTS_DIR.exists() and not projects_files:
        anomalies.append({
            "code": "no_transcript_files",
            "severity": "warn",
            "message": f"No Claude transcript JSONL files were found under {PROJECTS_DIR}.",
        })

    unreadable = 0
    for path in projects_files[:5000]:
        try:
            with path.open("rb") as f:
                f.read(1)
        except OSError:
            unreadable += 1
    if unreadable:
        anomalies.append({
            "code": "unreadable_transcripts",
            "severity": "warn",
            "message": f"{unreadable} transcript files could not be read by this user.",
        })

    return anomalies


def token_breakdown(rows: Iterable[RequestUsage]) -> dict[str, int]:
    rows = list(rows)
    return {
        "input_tokens": sum(r.input_tokens for r in rows),
        "output_tokens": sum(r.output_tokens for r in rows),
        "cache_read_input_tokens": sum(r.cache_read_input_tokens for r in rows),
        "cache_creation_input_tokens": sum(r.cache_creation_input_tokens for r in rows),
        "cache_5m_tokens": sum(r.cache_5m_tokens for r in rows),
        "cache_1h_tokens": sum(r.cache_1h_tokens for r in rows),
        "reported_total": sum(r.reported_total for r in rows),
        "tool_calls": sum(r.tool_use_count for r in rows),
        "web_search_requests": sum(r.web_search_requests for r in rows),
        "web_fetch_requests": sum(r.web_fetch_requests for r in rows),
    }


def build_sync_payload(
    requests: list[RequestUsage],
    sessions: dict[str, SessionSummary],
    stats: dict[str, Any] | None,
    paths: list[Path],
    period_start: datetime | None,
    period_end: datetime | None,
) -> dict[str, Any]:
    config = load_agent_config()
    identity = collect_identity(config)
    anomalies = discover_anomalies(paths)
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        claude_config_source = "CLAUDE_CONFIG_DIR"
    elif config.get("claude_config_dir"):
        claude_config_source = "collector_config"
    else:
        claude_config_source = "default"

    duplicate_records = sum(max(0, r.duplicate_records - 1) for r in requests)
    transcript_inventory = []
    for path in paths:
        try:
            stat = path.stat()
            transcript_inventory.append({
                "path_hash": stable_hash(str(path)),
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
        except OSError:
            transcript_inventory.append({
                "path_hash": stable_hash(str(path)),
                "size": None,
                "mtime": None,
            })

    daily_rows = []
    daily_buckets: dict[tuple[str, str, str], list[RequestUsage]] = defaultdict(list)
    for r in requests:
        daily_buckets[(date_key(r.timestamp), r.project, r.model)].append(r)
    for (day, project, model), rows in sorted(daily_buckets.items()):
        daily_rows.append({
            "day": day,
            "project": project,
            "project_name": short_project_name(project),
            "model": model,
            "requests": len(rows),
            "finalized_requests": sum(r.finalized for r in rows),
            "incomplete_requests": sum(not r.finalized for r in rows),
            **token_breakdown(rows),
        })

    session_rows = []
    for s in sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True):
        session_rows.append({
            "session_id": s.session_id,
            "project": s.project,
            "project_name": short_project_name(s.project),
            "is_subagent": s.is_subagent,
            "agent_id": s.agent_id,
            "first_ts": s.first_ts,
            "last_ts": s.last_ts,
            "duration_seconds": s.duration_seconds,
            "tokens_per_hour": s.tokens_per_hour,
            "requests": s.requests,
            "finalized_requests": s.finalized_requests,
            "incomplete_requests": s.incomplete_requests,
            "models": s.models,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "cache_read_input_tokens": s.cache_read,
            "cache_creation_input_tokens": s.cache_creation,
            "cache_5m_tokens": s.cache_5m,
            "cache_1h_tokens": s.cache_1h,
            "reported_total": s.reported_total,
            "tool_calls": s.tool_calls,
        })

    tool_counter = Counter()
    for r in requests:
        for name in r.tool_names:
            tool_counter[friendly_tool_name(name)] += 1

    total = sum(r.reported_total for r in requests)
    by_project = sorted(group_sum(requests, lambda r: r.project).items(), key=lambda x: x[1], reverse=True)
    by_model = sorted(group_sum(requests, lambda r: r.model).items(), key=lambda x: x[1], reverse=True)

    drivers = {
        "top_projects": [
            {
                "project": project,
                "project_name": short_project_name(project),
                "tokens": tokens,
                "share": tokens / total if total else 0.0,
            }
            for project, tokens in by_project[:10]
        ],
        "top_models": [
            {"model": model, "tokens": tokens, "share": tokens / total if total else 0.0}
            for model, tokens in by_model[:10]
        ],
        "top_tools": [
            {"tool": tool, "calls": calls}
            for tool, calls in tool_counter.most_common(10)
        ],
        "top_sessions": [
            {
                "session_id": s.session_id,
                "project_name": short_project_name(s.project),
                "tokens": s.reported_total,
                "share": s.reported_total / total if total else 0.0,
            }
            for s in sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True)[:10]
        ],
    }

    payload_core = {
        "schema_version": 1,
        "collector_version": APP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "org_id": config.get("org_id", ""),
        "identity": identity,
        "claude": {
            "config_dir": str(CLAUDE_DIR),
            "projects_dir": str(PROJECTS_DIR),
            "stats_cache_present": STATS_CACHE.exists(),
            "stats_cache_last_computed_date": stats.get("lastComputedDate") if stats else None,
            "config_source": claude_config_source,
        },
        "summary": {
            "requests": len(requests),
            "sessions": len(sessions),
            "transcript_files": len(paths),
            "finalized_requests": sum(r.finalized for r in requests),
            "incomplete_requests": sum(not r.finalized for r in requests),
            "duplicate_records_removed": duplicate_records,
            **token_breakdown(requests),
        },
        "daily": daily_rows,
        "sessions": session_rows,
        "drivers": drivers,
        "anomalies": anomalies,
    }
    payload_core["transcript_digest"] = stable_hash(transcript_inventory)
    payload_core["idempotency_key"] = stable_hash({
        "collector_id": identity["collector_id"],
        "machine_id": identity["machine_id"],
        "user_id": identity["user_id"],
        "period_start": payload_core["period_start"],
        "period_end": payload_core["period_end"],
        "transcript_digest": payload_core["transcript_digest"],
    })
    return payload_core


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(config)
    if redacted.get("collector_token"):
        redacted["collector_token"] = "***"
    return redacted


def upload_payload(payload: dict[str, Any], config: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    ingest_url = config.get("ingest_url")
    token = config.get("collector_token")
    if not ingest_url or not token:
        raise RuntimeError("Collector is not registered. Run --register with --ingest-url and --collector-token first.")

    return post_json(
        ingest_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": str(payload["idempotency_key"]),
        },
        payload=payload,
        timeout=timeout,
    )


def sync_usage(
    requests: list[RequestUsage],
    sessions: dict[str, SessionSummary],
    stats: dict[str, Any] | None,
    paths: list[Path],
    period_start: datetime | None,
    period_end: datetime | None,
    dry_run: bool = False,
):
    config = load_agent_config()
    payload = build_sync_payload(requests, sessions, stats, paths, period_start, period_end)

    if dry_run:
        print(json.dumps({
            "config": redact_config(config),
            "payload": payload,
        }, indent=2, sort_keys=True))
        return

    started_at = datetime.now(timezone.utc).isoformat()
    state = load_agent_state()
    try:
        result = upload_payload(payload, config)
    except Exception as exc:
        state.update({
            "last_attempt_at": started_at,
            "last_error_at": datetime.now(timezone.utc).isoformat(),
            "last_error": str(exc),
        })
        save_agent_state(state)
        print(c(str(exc), "red"))
        sys.exit(2)

    state.update({
        "last_attempt_at": started_at,
        "last_success_at": datetime.now(timezone.utc).isoformat(),
        "last_error": "",
        "last_idempotency_key": payload["idempotency_key"],
        "last_summary": payload["summary"],
        "last_response_status": result["status"],
    })
    save_agent_state(state)
    print(c("Usage uploaded successfully.", "green"))
    print(f"Status          : {result['status']}")
    print(f"Idempotency key : {payload['idempotency_key']}")


def total_pct(a: int, b: int) -> float:
    return 0.0 if not b else a / b * 100.0


def export_json(path: Path, requests: list[RequestUsage], sessions: dict[str, SessionSummary]):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(PROJECTS_DIR),
        "requests": [asdict(r) for r in requests],
        "sessions": [asdict(s) | {
            "duration_seconds": s.duration_seconds,
            "tokens_per_hour": s.tokens_per_hour,
        } for s in sessions.values()],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def export_csv(path: Path, requests: list[RequestUsage]):
    fields = [
        "timestamp", "project", "session_id", "is_subagent", "agent_id",
        "model", "request_id", "finalized", "duplicate_records",
        "input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "cache_5m_tokens", "cache_1h_tokens",
        "reported_total", "tool_use_count", "web_search_requests",
        "web_fetch_requests", "iteration_count", "advisor_iterations",
        "stop_reason", "file",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in requests:
            row = {k: getattr(r, k) for k in fields if hasattr(r, k)}
            row["reported_total"] = r.reported_total
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Claude Code local usage from ~/.claude/projects/*.jsonl"
    )
    parser.add_argument("--days", type=int, default=None, help="Only analyze the last N days.")
    parser.add_argument("--project", help="Filter by project path/name substring.")
    parser.add_argument("--include-subagents", action="store_true", default=True,
                        help="Include subagent transcripts (default: yes).")
    parser.add_argument("--main-only", action="store_true", help="Exclude subagent transcripts.")
    parser.add_argument("--json", metavar="PATH", help="Export deduplicated request data as JSON.")
    parser.add_argument("--csv", metavar="PATH", help="Export deduplicated request data as CSV.")
    parser.add_argument("--plain", action="store_true",
                        help="Print the default plain-English report (kept for compatibility).")
    parser.add_argument("--verbose", action="store_true",
                        help="Print the detailed table-heavy analyzer report.")
    parser.add_argument("--register", action="store_true",
                        help="Save collector configuration for background/team sync.")
    parser.add_argument("--enroll", action="store_true",
                        help="Enroll this machine with the team backend and save collector configuration.")
    parser.add_argument("--preflight", action="store_true",
                        help="Check whether this machine is ready for collector install/sync.")
    parser.add_argument("--status", action="store_true",
                        help="Show collector install, identity, sync, and Claude data status.")
    parser.add_argument("--sync", action="store_true",
                        help="Upload metrics-only usage payload to the configured ingest endpoint.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --sync, print the upload payload instead of sending it.")
    parser.add_argument("--ingest-url",
                        help="HTTPS ingest endpoint, usually a Supabase Edge Function URL.")
    parser.add_argument("--collector-token",
                        help="Collector registration/upload token. Stored locally by --register.")
    parser.add_argument("--enroll-url", default=DEFAULT_ENROLL_URL,
                        help=f"HTTPS enrollment endpoint. Default: {DEFAULT_ENROLL_URL}.")
    parser.add_argument("--release-asset-url", default="",
                        help="Optional release asset URL to test during --preflight.")
    parser.add_argument("--org-id", help="Optional organization/team identifier for uploaded payloads.")
    parser.add_argument("--account-label",
                        help="Optional human label for the Claude account/team being used.")
    parser.add_argument("--collector-label",
                        help="Optional human label for this machine/user collector.")
    parser.add_argument("--claude-dir",
                        help="Override Claude config directory. Defaults to CLAUDE_CONFIG_DIR or ~/.claude.")
    parser.add_argument("--sync-interval-minutes", type=int, default=DEFAULT_SYNC_INTERVAL_MINUTES,
                        help=f"Preferred scheduled sync interval. Default: {DEFAULT_SYNC_INTERVAL_MINUTES}.")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    if args.no_color:
        for key in ANSI:
            ANSI[key] = ""

    config = load_agent_config()
    configure_claude_dir(args.claude_dir or config.get("claude_config_dir") or os.environ.get("CLAUDE_CONFIG_DIR"))

    if args.register:
        if not args.ingest_url or not args.collector_token:
            print(c("--register requires --ingest-url and --collector-token.", "red"))
            sys.exit(2)
        if not args.ingest_url.startswith("https://"):
            print(c("--ingest-url must be an HTTPS URL.", "red"))
            sys.exit(2)
        register_collector(args)
        return

    if args.enroll:
        if not args.enroll_url.startswith("https://"):
            print(c("--enroll-url must be an HTTPS URL.", "red"))
            sys.exit(2)
        enroll_collector(args)
        return

    if args.status:
        print_status()
        return

    if args.preflight:
        sys.exit(run_preflight(args))

    paths = discover_jsonl()
    if not paths and not args.sync:
        print(f"No JSONL files found under {PROJECTS_DIR}")
        sys.exit(1)

    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    start = None
    if args.days is not None:
        start = end - timedelta(days=max(0, args.days))

    reqs, sessions, record_types = parse_all(paths, start=start, end=end)

    if args.project:
        needle = args.project.lower()
        reqs = [r for r in reqs if needle in r.project.lower() or needle in r.file.lower()]
        wanted = set(r.session_id for r in reqs)
        sessions = {k: v for k, v in sessions.items() if k in wanted}

    if args.main_only:
        reqs = [r for r in reqs if not r.is_subagent]
        wanted = set(r.session_id for r in reqs)
        sessions = {k: v for k, v in sessions.items() if k in wanted}

    stats = load_stats_cache()

    if args.sync:
        sync_usage(reqs, sessions, stats, paths, start, end, dry_run=args.dry_run)
        return

    if args.verbose:
        analyze(reqs, sessions, stats)
    else:
        plain_usage_story(reqs, sessions, stats)

    if args.json:
        export_json(Path(args.json).expanduser(), reqs, sessions)
        print(f"Wrote {Path(args.json).expanduser()}")
    if args.csv:
        export_csv(Path(args.csv).expanduser(), reqs)
        print(f"Wrote {Path(args.csv).expanduser()}")


if __name__ == "__main__":
    main()
