"""Which Claude account each local install is signed in to, plus install and feature-usage facts.

Only identifiers and plan tiers leave the machine; the login email is reduced to a hash.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from claude_usage.models import AccountSnapshot, FeatureUsage
from claude_usage.paths import claude_json_path
from claude_usage.util import read_json_file, stable_hash


UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _organization_name(value: Any) -> str | None:
    # Personal organizations are named "<email>'s Organization"; the email must not leave the machine.
    name = _text(value)
    return "Personal organization" if name and "@" in name else name


def _merge(accounts: dict[str, AccountSnapshot], snapshot: AccountSnapshot):
    current = accounts.get(snapshot.account_uuid)
    if current is None:
        accounts[snapshot.account_uuid] = snapshot
        return
    for field, value in vars(snapshot).items():
        if field == "source":
            sources = set(filter(None, current.source.split(","))) | {snapshot.source}
            current.source = ",".join(sorted(sources))
        elif getattr(current, field) is None and value is not None:
            setattr(current, field, value)


def read_accounts(claude_dirs: list[Path], desktop_dirs: list[Path]) -> list[AccountSnapshot]:
    accounts: dict[str, AccountSnapshot] = {}
    for claude_dir in claude_dirs:
        oauth = read_json_file(claude_json_path(claude_dir)).get("oauthAccount")
        if not isinstance(oauth, dict) or not oauth.get("accountUuid"):
            continue
        email = _text(oauth.get("emailAddress"))
        _merge(accounts, AccountSnapshot(
            account_uuid=str(oauth["accountUuid"]),
            organization_uuid=_text(oauth.get("organizationUuid")),
            organization_name=_organization_name(oauth.get("organizationName")),
            email_hash=stable_hash(email.strip().lower()) if email else None,
            billing_type=_text(oauth.get("billingType")),
            seat_tier=_text(oauth.get("seatTier")),
            user_rate_limit_tier=_text(oauth.get("userRateLimitTier")),
            organization_rate_limit_tier=_text(oauth.get("organizationRateLimitTier")),
            has_extra_usage=oauth.get("hasExtraUsageEnabled") if isinstance(oauth.get("hasExtraUsageEnabled"), bool) else None,
            source="claude_code",
        ))

    for desktop in desktop_dirs:
        # Desktop keeps per-account session folders named <accountUuid>/<organizationUuid>.
        for sessions_root in (desktop / "claude-code-sessions", desktop / "local-agent-mode-sessions"):
            for org_dir in sorted(sessions_root.glob("*/*")):
                if org_dir.is_dir() and UUID.fullmatch(org_dir.name) and UUID.fullmatch(org_dir.parent.name):
                    _merge(accounts, AccountSnapshot(org_dir.parent.name, org_dir.name, source="claude_desktop"))
        last_known = _text(read_json_file(desktop / "config.json").get("lastKnownAccountUuid"))
        if last_known:
            _merge(accounts, AccountSnapshot(last_known, source="claude_desktop"))
    return sorted(accounts.values(), key=lambda a: a.account_uuid)


def read_install_info(claude_dirs: list[Path]) -> dict[str, Any]:
    for claude_dir in claude_dirs:
        data = read_json_file(claude_json_path(claude_dir))
        if data:
            return {
                "install_method": _text(data.get("installMethod")),
                "first_start_time": _text(data.get("firstStartTime")),
                "num_startups": data.get("numStartups") if isinstance(data.get("numStartups"), int) else None,
            }
    return {}


def read_feature_usage(claude_dirs: list[Path]) -> list[FeatureUsage]:
    counts: dict[tuple[str, str], int] = {}
    for claude_dir in claude_dirs:
        data = read_json_file(claude_json_path(claude_dir))
        for kind, key in (("skill", "skillUsage"), ("plugin", "pluginUsage")):
            entries = data.get(key)
            if not isinstance(entries, dict):
                continue
            for name, entry in entries.items():
                count = entry.get("usageCount") if isinstance(entry, dict) else None
                if isinstance(count, int):
                    counts[(kind, str(name))] = max(counts.get((kind, str(name)), 0), count)
    return [FeatureUsage(kind, name, count) for (kind, name), count in sorted(counts.items())]
