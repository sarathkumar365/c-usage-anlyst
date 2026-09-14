"""Stable, hashed identifiers for this machine, OS user, and collector install."""

from __future__ import annotations

import getpass
import os
import platform
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_usage.util import safe_read_text, stable_hash


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


def new_collector_id() -> str:
    return stable_hash({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "random": uuid.uuid4().hex,
    })[:32]
