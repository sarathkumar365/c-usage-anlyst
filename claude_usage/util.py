from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8", errors="ignore")
    return hashlib.sha256(encoded).hexdigest()

def private_hash(value: Any, salt: str) -> str:
    """Hash of a local value (path, email) salted per organization, so a guessed value cannot be matched across orgs."""
    return stable_hash({"salt": salt or "", "value": value})

def read_json_file(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

def ensure_private_dir(path: Path):
    """Create a directory only this user can read (tokens live in the agent dir)."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)

def write_private_text(path: Path, text: str, *, mode: int = 0o600):
    """Atomically replace `path`, creating it with `mode` from the start so it is never briefly readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

def write_json_file(path: Path, payload: dict[str, Any], *, sort_keys: bool = True, mode: int = 0o600):
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=sort_keys), mode=mode)

def safe_read_text(path: Path, max_bytes: int = 4096) -> str | None:
    try:
        with path.open("rb") as f:
            return f.read(max_bytes).decode("utf-8", errors="ignore").strip()
    except Exception:
        return None

def ts_from_epoch(value: Any) -> str | None:
    """Epoch seconds or milliseconds -> ISO timestamp."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e11:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None

def date_key(ts: str | None) -> str:
    dt = parse_ts(ts)
    return dt.astimezone().strftime("%Y-%m-%d") if dt else "unknown"
