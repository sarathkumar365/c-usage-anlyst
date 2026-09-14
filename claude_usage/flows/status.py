"""--status: install, identity, sync, and Claude data health for this machine."""

from __future__ import annotations

from claude_usage.accounts import read_accounts
from claude_usage.anomalies import discover_anomalies
from claude_usage.constants import APP_VERSION
from claude_usage.flows.discover import run_discovery
from claude_usage.identity import collect_identity
from claude_usage.paths import ClaudePaths, claude_config_dirs, config_path, desktop_data_dirs, statusline_samples_path, statusline_script_path
from claude_usage.plan_usage import read_desktop_samples, read_statusline_samples
from claude_usage.reports.sources import confidence_rank
from claude_usage.store import load_config, load_state
from claude_usage.ui import fmt_int, plain_metric, plain_section, plain_status, c
from claude_usage.util import read_json_file

SOURCE_LABELS = {
    "argument": "from --claude-dir",
    "collector_config": "from collector config",
    "CLAUDE_CONFIG_DIR": "from CLAUDE_CONFIG_DIR",
    "default": "default",
}


def _pct(value: float | None) -> str:
    return f"{value:g}%" if value is not None else "n/a"


def print_status(claude: ClaudePaths):
    config = load_config()
    state = load_state()
    identity = collect_identity(config)
    anomalies = discover_anomalies(claude)

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

    plain_section("Claude Account")
    claude_dirs = claude_config_dirs(claude)
    desktop_dirs = desktop_data_dirs()
    accounts = read_accounts(claude_dirs, desktop_dirs)
    for account in accounts:
        plain_metric("Account", account.organization_name or "unknown organization", f"{account.account_uuid[:8]} via {account.source}", "good")
    if not accounts:
        plain_metric("Account", "not found", "no signed-in Claude account on this machine", "warn")
    samples, _ = read_desktop_samples(desktop_dirs)
    samples += read_statusline_samples(statusline_samples_path(), "")
    latest = max(samples, key=lambda sample: sample.sampled_at, default=None)
    if latest:
        plain_metric("Plan usage", f"5h {_pct(latest.five_hour_pct)} · 7d {_pct(latest.seven_day_pct)}", f"{latest.source} at {latest.sampled_at}", "info")
    else:
        plain_metric("Plan usage", "no samples", "needs Claude Desktop or --install-statusline", "warn")
    captured = any(str(statusline_script_path()) in str(read_json_file(d / "settings.json").get("statusLine")) for d in claude_dirs)
    plain_metric("Usage % capture", "installed" if captured else "not installed", "Claude Code statusline", "good" if captured else "warn")

    plain_section("Claude Data")
    plain_metric("Claude dir", str(claude.claude_dir), SOURCE_LABELS[claude.source], "info")
    plain_metric("Projects dir", str(claude.projects_dir), "exists" if claude.projects_dir.exists() else "missing", "good" if claude.projects_dir.exists() else "bad")
    plain_metric("Last sync", state.get("last_success_at", "never"), "", "good" if state.get("last_success_at") else "warn")
    if anomalies:
        for anomaly in anomalies:
            plain_status("Anomaly", anomaly["message"], anomaly.get("severity", "warn"))
    else:
        plain_status("Anomalies", "No obvious Claude data location anomalies found.", "good")
    sources = run_discovery(claude, full=True)
    present_sources = [s for s in sources if s.status == "present"]
    plain_metric(
        "Surfaces",
        str(len(present_sources)),
        "exact/derived/evidence Claude sources",
        "good" if present_sources else "warn",
    )
    for source in sorted(present_sources, key=lambda s: (s.surface, -confidence_rank(s.confidence), s.path))[:10]:
        plain_status(
            source.surface,
            f"{source.confidence} via {source.extractor}; {fmt_int(source.file_count)} files; latest {source.latest_activity_at or 'n/a'}",
            "good" if source.confidence == "exact" else "info",
        )
    print()
