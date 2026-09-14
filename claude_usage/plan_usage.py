"""Account-wide plan usage percentages (5-hour and 7-day limits) from Claude Desktop and the statusline capture.

These percentages cover everyone signed in to the account, including claude.ai web use.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_usage.constants import PLAN_USAGE_HISTORY_FILE, PLAN_USAGE_HISTORY_VERSIONS
from claude_usage.models import PlanUsageSample
from claude_usage.util import parse_ts, read_json_file, ts_from_epoch


def _pct(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _after(sample: PlanUsageSample, since: datetime | None) -> bool:
    ts = parse_ts(sample.sampled_at)
    return bool(ts) and (since is None or ts > since)


def read_desktop_samples(desktop_dirs: list[Path], since: datetime | None = None) -> tuple[list[PlanUsageSample], list[int]]:
    """Samples newer than `since`, plus any file format versions this collector does not understand."""
    samples: list[PlanUsageSample] = []
    unknown_versions: list[int] = []
    for desktop in desktop_dirs:
        history = read_json_file(desktop / PLAN_USAGE_HISTORY_FILE)
        if not history:
            continue
        if history.get("version") not in PLAN_USAGE_HISTORY_VERSIONS:
            unknown_versions.append(history.get("version"))
            continue
        for raw in history.get("samples") or []:
            if not isinstance(raw, dict) or not isinstance(raw.get("u"), dict):
                continue
            sampled_at = ts_from_epoch(raw.get("t"))
            if not sampled_at:
                continue
            sample = PlanUsageSample(
                sampled_at=sampled_at,
                organization_uuid=str(raw.get("org") or "unknown"),
                source="desktop",
                five_hour_pct=_pct(raw["u"].get("fh")),
                seven_day_pct=_pct(raw["u"].get("sd")),
                extra_usage=_pct(raw["u"].get("xu")),
            )
            if _after(sample, since):
                samples.append(sample)
    return samples, unknown_versions


def parse_statusline_line(line: str, organization_uuid: str) -> PlanUsageSample | None:
    """One capture line: {"t": epoch seconds, "input": <Claude Code statusline JSON>}."""
    try:
        # PowerShell may prefix the file with a byte-order mark.
        record = json.loads(line.lstrip("\ufeff"))
    except ValueError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("input"), dict):
        return None
    limits = record["input"].get("rate_limits")
    sampled_at = ts_from_epoch(record.get("t"))
    if not isinstance(limits, dict) or not sampled_at:
        return None
    five = limits.get("five_hour") if isinstance(limits.get("five_hour"), dict) else {}
    seven = limits.get("seven_day") if isinstance(limits.get("seven_day"), dict) else {}
    if not five and not seven:
        return None
    return PlanUsageSample(
        sampled_at=sampled_at,
        organization_uuid=organization_uuid,
        source="statusline",
        five_hour_pct=_pct(five.get("used_percentage")),
        seven_day_pct=_pct(seven.get("used_percentage")),
        five_hour_resets_at=ts_from_epoch(five.get("resets_at")),
        seven_day_resets_at=ts_from_epoch(seven.get("resets_at")),
    )


def read_statusline_samples(samples_path: Path, organization_uuid: str, since: datetime | None = None) -> list[PlanUsageSample]:
    try:
        lines = samples_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    samples = [parse_statusline_line(line, organization_uuid) for line in lines]
    return [s for s in samples if s and _after(s, since)]
