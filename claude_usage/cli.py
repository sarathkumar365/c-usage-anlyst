"""Command line: parse arguments, resolve inputs once, dispatch to exactly one flow."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from claude_usage.constants import DEFAULT_ENROLL_URL, DEFAULT_SYNC_INTERVAL_MINUTES
from claude_usage.flows import enroll as enroll_flow
from claude_usage.flows.collect import UsageQuery, collect_usage
from claude_usage.flows.discover import run_discovery
from claude_usage.flows.preflight import run_preflight
from claude_usage.flows.status import print_status
from claude_usage.flows.statusline import install_statusline, uninstall_statusline
from claude_usage.flows.sync import run_sync
from claude_usage.paths import resolve_claude_paths
from claude_usage.reports.export import export_csv, export_json
from claude_usage.reports.plain import plain_usage_story
from claude_usage.reports.sources import print_sources
from claude_usage.reports.verbose import analyze
from claude_usage.store import load_config
from claude_usage.ui import c, disable_color


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze local Claude usage and Claude surface activity on this machine."
    )
    commands = parser.add_argument_group("commands (default: plain-English report)")
    commands.add_argument("--verbose", action="store_true",
                          help="Print the detailed table-heavy analyzer report.")
    commands.add_argument("--plain", action="store_true",
                          help="Print the default plain-English report (kept for compatibility).")
    commands.add_argument("--sources", action="store_true",
                          help="Print discovered Claude surfaces and confidence levels.")
    commands.add_argument("--status", action="store_true",
                          help="Show collector install, identity, sync, and Claude data status.")
    commands.add_argument("--preflight", action="store_true",
                          help="Check whether this machine is ready for collector install/sync.")
    commands.add_argument("--enroll", action="store_true",
                          help="Enroll this machine with the team backend and save collector configuration.")
    commands.add_argument("--register", action="store_true",
                          help="Save collector configuration for background/team sync.")
    commands.add_argument("--sync", action="store_true",
                          help="Upload metrics-only usage payload to the configured ingest endpoint.")
    commands.add_argument("--install-statusline", action="store_true",
                          help="Capture the account's usage %% from Claude Code's statusline (keeps any existing statusline).")
    commands.add_argument("--uninstall-statusline", action="store_true",
                          help="Remove the usage %% capture and restore the previous statusline.")

    filters = parser.add_argument_group("filters (reports and --sync)")
    filters.add_argument("--days", type=int, default=None, help="Only analyze the last N days.")
    filters.add_argument("--project", help="Filter by project path/name substring.")
    filters.add_argument("--include-subagents", action="store_true", default=True,
                         help="Include subagent transcripts (default: yes).")
    filters.add_argument("--main-only", action="store_true", help="Exclude subagent transcripts.")
    filters.add_argument("--surface",
                         choices=["all", "claude-code", "desktop", "cowork", "extensions"],
                         default="all",
                         help="Filter report/sync by Claude surface. Default: all.")

    options = parser.add_argument_group("options")
    options.add_argument("--json", metavar="PATH", help="Export deduplicated request data as JSON.")
    options.add_argument("--csv", metavar="PATH", help="Export deduplicated request data as CSV.")
    options.add_argument("--dry-run", action="store_true",
                         help="With --sync, print the upload payload instead of sending it.")
    options.add_argument("--ingest-url",
                         help="HTTPS ingest endpoint, usually a Supabase Edge Function URL.")
    options.add_argument("--collector-token",
                         help="Collector registration/upload token. Stored locally by --register.")
    options.add_argument("--enroll-url", default=DEFAULT_ENROLL_URL,
                         help=f"HTTPS enrollment endpoint. Default: {DEFAULT_ENROLL_URL}.")
    options.add_argument("--enrollment-secret", default=os.environ.get("ENROLLMENT_SECRET", ""),
                         help="Shared secret required by --enroll. Defaults to ENROLLMENT_SECRET; not stored.")
    options.add_argument("--release-asset-url", default="",
                         help="Optional release asset URL to test during --preflight.")
    options.add_argument("--org-id", help="Optional organization/team identifier for uploaded payloads.")
    options.add_argument("--account-label",
                         help="Optional human label for the Claude account/team being used.")
    options.add_argument("--collector-label",
                         help="Optional human label for this machine/user collector.")
    options.add_argument("--claude-dir",
                         help="Override Claude config directory. Defaults to CLAUDE_CONFIG_DIR or ~/.claude.")
    options.add_argument("--sync-interval-minutes", type=int, default=DEFAULT_SYNC_INTERVAL_MINUTES,
                         help=f"Preferred scheduled sync interval. Default: {DEFAULT_SYNC_INTERVAL_MINUTES}.")
    options.add_argument("--no-color", action="store_true")
    return parser


def fail(message: str) -> int:
    print(c(message, "red"))
    return 2


def run_report(args: argparse.Namespace, claude, query: UsageQuery) -> int:
    usage = collect_usage(claude, query)
    if not usage.jsonl_paths:
        print(f"No JSONL files found under {claude.projects_dir}")
        return 1
    if args.verbose:
        analyze(usage.requests, usage.sessions, usage.stats, claude.claude_dir)
    else:
        plain_usage_story(usage.requests, usage.sessions, usage.stats)
    if args.json:
        export_json(Path(args.json).expanduser(), usage.requests, usage.sessions, claude.projects_dir)
        print(f"Wrote {Path(args.json).expanduser()}")
    if args.csv:
        export_csv(Path(args.csv).expanduser(), usage.requests)
        print(f"Wrote {Path(args.csv).expanduser()}")
    return 0


def dispatch(args: argparse.Namespace) -> int:
    claude = resolve_claude_paths(args.claude_dir, load_config())
    labels = {"org_id": args.org_id, "account_label": args.account_label, "collector_label": args.collector_label}

    if args.register:
        if not args.ingest_url or not args.collector_token:
            return fail("--register requires --ingest-url and --collector-token.")
        if not args.ingest_url.startswith("https://"):
            return fail("--ingest-url must be an HTTPS URL.")
        enroll_flow.register(claude, ingest_url=args.ingest_url, collector_token=args.collector_token,
                             sync_interval_minutes=args.sync_interval_minutes, **labels)
        return 0

    if args.enroll:
        if not args.enroll_url.startswith("https://"):
            return fail("--enroll-url must be an HTTPS URL.")
        try:
            enroll_flow.enroll(claude, enroll_url=args.enroll_url, enrollment_secret=args.enrollment_secret,
                               sync_interval_minutes=args.sync_interval_minutes, **labels)
        except RuntimeError as exc:
            return fail(str(exc))
        return 0

    if args.status:
        print_status(claude)
        return 0

    if args.install_statusline:
        return install_statusline(claude)

    if args.uninstall_statusline:
        return uninstall_statusline(claude)

    if args.preflight:
        return run_preflight(claude, release_asset_url=args.release_asset_url,
                             enroll_url=args.enroll_url, ingest_url=args.ingest_url)

    if args.sources:
        print_sources(run_discovery(claude, full=True))
        return 0

    query = UsageQuery(days=args.days, project=args.project, main_only=args.main_only, surface=args.surface)
    if args.sync:
        return run_sync(claude, query, dry_run=args.dry_run)
    return run_report(args, claude, query)


def main(argv: list[str] | None = None):
    args = build_parser().parse_args(argv)
    if args.no_color:
        disable_color()
    code = dispatch(args)
    if code:
        sys.exit(code)
