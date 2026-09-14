"""--preflight: can this machine install and run the collector?"""

from __future__ import annotations

import os
import platform
import shutil
from datetime import datetime, timezone

from claude_usage.constants import DEFAULT_INGEST_URL
from claude_usage.flows.discover import run_discovery
from claude_usage.paths import ClaudePaths, app_dir
from claude_usage.store import load_config, load_state
from claude_usage.transport import probe_url
from claude_usage.ui import c

CHECK_STYLES = {
    "OK": "green",
    "WARN": "yellow",
    "BLOCKED": "red",
    "INFO": "cyan",
    "READY": "green",
}


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

def run_preflight(claude: ClaudePaths, *, release_asset_url: str, enroll_url: str, ingest_url: str | None) -> int:
    config = load_config()

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
        ("GitHub reachable", release_asset_url, "HEAD"),
        ("Supabase enroll reachable", enroll_url, "OPTIONS"),
        ("Supabase ingest reachable", ingest_url or DEFAULT_INGEST_URL, "OPTIONS"),
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

    if claude.claude_dir.exists():
        print_preflight_check("OK", "Claude dir", str(claude.claude_dir))
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude dir", f"missing at {claude.claude_dir}", "Install/use Claude Code once, or set CLAUDE_CONFIG_DIR.")

    if claude.projects_dir.exists():
        try:
            has_jsonl = any(claude.projects_dir.rglob("*.jsonl"))
        except Exception:
            has_jsonl = False
        if has_jsonl:
            print_preflight_check("OK", "Claude transcripts", "JSONL files found")
        else:
            warnings += 1
            print_preflight_check("WARN", "Claude transcripts", "no JSONL files found yet")
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude projects dir", f"missing at {claude.projects_dir}")

    if claude.stats_cache.exists():
        print_preflight_check("OK", "Claude stats cache", str(claude.stats_cache))
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude stats cache", f"missing at {claude.stats_cache}")

    sources = run_discovery(claude, full=True)
    present_sources = [s for s in sources if s.status == "present"]
    exact_sources = [s for s in present_sources if s.confidence == "exact"]
    derived_sources = [s for s in present_sources if s.confidence == "derived"]
    evidence_sources = [s for s in present_sources if s.confidence == "evidence"]
    if present_sources:
        print_preflight_check(
            "OK",
            "Claude surfaces",
            f"{len(present_sources)} present ({len(exact_sources)} exact, {len(derived_sources)} derived, {len(evidence_sources)} evidence)",
        )
    else:
        warnings += 1
        print_preflight_check("WARN", "Claude surfaces", "none discovered")

    scheduler, detail, scheduler_ok = scheduler_status()
    if scheduler_ok:
        print_preflight_check("OK", "Scheduler available", scheduler)
    else:
        blocked += 1
        print_preflight_check("BLOCKED", "Scheduler available", detail, "Install a user-level scheduler or run sync manually.")

    if config:
        state = load_state()
        print_preflight_check("INFO", "Existing install", config.get("collector_id", "configured"))
        print_preflight_check("INFO", "Last sync", state.get("last_success_at", "never"))
    else:
        print_preflight_check("INFO", "Existing install", "none")

    print()
    if blocked:
        print_preflight_check("BLOCKED", "Install cannot continue", f"{blocked} blocking issue(s), {warnings} warning(s)")
        return 2
    print_preflight_check("READY", "Install can continue", f"{warnings} warning(s)")
    return 0
