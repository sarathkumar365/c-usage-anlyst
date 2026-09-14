from __future__ import annotations

import json
import os
import platform
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DISCOVERY_CACHE_TTL_HOURS = 24
DISCOVERY_PATTERNS = (
    "claude",
    "anthropic",
    "claude.ai",
    "claude-code",
    "cowork",
    "com.anthropic",
)
SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "Cache_Data",
    "GPUCache",
    "DawnGraphiteCache",
    "VideoDecodeStats",
}
STRUCTURAL_SOURCE_NAMES = {
    "indexeddb",
    "session storage",
    "local storage",
    "webstorage",
    "logs",
    "claude extensions",
    "claude extensions settings",
    "native messaginghosts",
    "nativemessaginghosts",
}
SAFE_METADATA_NAMES = {
    "claude_desktop_config.json",
    "settings.json",
    "preferences",
    "stats-cache.json",
    "history.jsonl",
}
SAFE_METADATA_SUFFIXES = {".plist", ".log"}


@dataclass
class SourceRecord:
    source_id: str
    surface: str
    path: str
    path_hash: str
    status: str
    confidence: str
    file_count: int = 0
    latest_activity_at: str | None = None
    extractor: str = ""
    anomalies: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("path", None)
        return payload

    def to_cache(self) -> dict[str, Any]:
        return asdict(self)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8", errors="ignore")
    return __import__("hashlib").sha256(encoded).hexdigest()


def parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def iso_from_mtime(mtime: float | None) -> str | None:
    if mtime is None:
        return None
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


def name_matches(path: Path) -> bool:
    text = path.name.lower()
    return any(pattern in text for pattern in DISCOVERY_PATTERNS)


def structural_name_matches(path: Path) -> bool:
    return path.name.lower() in STRUCTURAL_SOURCE_NAMES


def safe_metadata_file_matches(path: Path) -> bool:
    name = path.name.lower()
    return name in SAFE_METADATA_NAMES or (
        name_matches(path) and path.suffix.lower() in SAFE_METADATA_SUFFIXES
    )


def normalize_candidate(path: Path, claude_dir: Path) -> Path:
    try:
        rel = path.relative_to(claude_dir / "projects")
        if rel.parts:
            return claude_dir / "projects"
    except Exception:
        pass
    return path


def path_depth(path: Path, root: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except Exception:
        return 0


def known_parent_roots(system: str | None = None, home: Path | None = None, claude_dir: Path | None = None) -> list[Path]:
    home = home or Path.home()
    system = system or platform.system()
    roots = [claude_dir or home / ".claude"]
    if system == "Darwin":
        roots.extend([
            home / "Library" / "Application Support",
            home / "Library" / "Logs",
            home / "Library" / "Preferences",
        ])
    elif system == "Windows":
        appdata = os.environ.get("APPDATA")
        localappdata = os.environ.get("LOCALAPPDATA")
        roots.extend([
            Path(appdata) if appdata else home / "AppData" / "Roaming",
            Path(localappdata) if localappdata else home / "AppData" / "Local",
        ])
    elif system == "Linux":
        roots.extend([
            home / ".config",
            home / ".cache",
            home / ".local" / "share",
        ])
    else:
        roots.extend([home])
    return unique_paths(roots)


def unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path.expanduser())
        if key not in seen:
            seen.add(key)
            out.append(Path(key))
    return out


def classify_source(path: Path, claude_dir: Path) -> tuple[str, str, str]:
    text = str(path).lower()
    name = path.name.lower()
    if path == claude_dir:
        return "claude_code", "evidence", "claude_code_config"
    if path == claude_dir / "projects" or (
        text.startswith(str(claude_dir / "projects").lower()) and name.endswith(".jsonl")
    ):
        return "claude_code", "exact", "claude_code_jsonl"
    if name == "stats-cache.json":
        return "claude_code", "exact", "claude_code_stats"
    if text.startswith(str(claude_dir).lower()):
        return "claude_code", "evidence", "claude_code_config"
    if "claude-code-sessions" in text or "claude-code-vm" in text or "vm_bundles" in text or "cowork" in text:
        return "cowork", "derived", "claude_desktop_cowork"
    if "extensions" in text or "native messaginghosts" in text or "nativemessaginghosts" in text or "claude_desktop_config" in name:
        return "extensions", "derived", "claude_desktop_extensions"
    if "indexeddb" in text or "session storage" in text or "local storage" in text or "webstorage" in text or "claude.ai" in text:
        return "desktop_chat", "derived", "claude_desktop_chat"
    return "desktop_app", "evidence", "claude_desktop_app"


def source_id_for(path: Path, surface: str) -> str:
    return stable_hash({"surface": surface, "path": str(path)})[:24]


def summarize_path(path: Path, max_files: int = 5000) -> tuple[str, int, str | None, list[str]]:
    anomalies: list[str] = []
    if not path.exists():
        return "missing", 0, None, ["path_missing"]
    if path.is_file():
        try:
            return "present", 1, iso_from_mtime(path.stat().st_mtime), []
        except OSError:
            return "unreadable", 1, None, ["path_unreadable"]

    file_count = 0
    latest_mtime: float | None = None
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
            file_count += len(files)
            if file_count > max_files:
                anomalies.append("file_count_truncated")
                file_count = max_files
                break
            for filename in files:
                try:
                    stat = (Path(root) / filename).stat()
                    latest_mtime = max(latest_mtime or stat.st_mtime, stat.st_mtime)
                except OSError:
                    anomalies.append("some_files_unreadable")
                    continue
    except OSError:
        return "unreadable", file_count, iso_from_mtime(latest_mtime), ["path_unreadable"]
    return "present", file_count, iso_from_mtime(latest_mtime), sorted(set(anomalies))


def discover_claude_sources(
    claude_dir: Path,
    *,
    system: str | None = None,
    home: Path | None = None,
    full: bool = True,
    cached_sources: list[dict[str, Any]] | None = None,
    max_depth: int = 5,
) -> tuple[list[SourceRecord], dict[str, Any]]:
    claude_dir = claude_dir.expanduser()
    roots = known_parent_roots(system=system, home=home, claude_dir=claude_dir)
    candidates: dict[str, Path] = {}

    for seed in (
        claude_dir,
        claude_dir / "projects",
        claude_dir / "stats-cache.json",
        claude_dir / "history.jsonl",
    ):
        if seed.exists():
            candidates[str(seed)] = seed

    if full or not cached_sources:
        for root in roots:
            if not root.exists():
                continue
            if name_matches(root):
                candidates[str(root)] = root
            if root.is_file():
                continue
            for current, dirs, files in os.walk(root):
                current_path = Path(current)
                depth = path_depth(current_path, root)
                dirs[:] = [
                    d for d in dirs
                    if d not in SKIP_DIR_NAMES and depth < max_depth
                ]
                for dirname in dirs:
                    candidate = current_path / dirname
                    if name_matches(candidate) or (name_matches(current_path) and structural_name_matches(candidate)):
                        candidate = normalize_candidate(candidate, claude_dir)
                        candidates[str(candidate)] = candidate
                for filename in files:
                    candidate = current_path / filename
                    if safe_metadata_file_matches(candidate):
                        candidate = normalize_candidate(candidate, claude_dir)
                        candidates[str(candidate)] = candidate
    else:
        for item in cached_sources:
            raw_path = item.get("path")
            if raw_path:
                candidates[str(raw_path)] = Path(raw_path)

    sources: list[SourceRecord] = []
    for path in sorted(candidates.values(), key=lambda p: str(p).lower()):
        surface, confidence, extractor = classify_source(path, claude_dir)
        status, file_count, latest_activity_at, anomalies = summarize_path(path)
        sources.append(SourceRecord(
            source_id=source_id_for(path, surface),
            surface=surface,
            path=str(path),
            path_hash=stable_hash(str(path)),
            status=status,
            confidence=confidence,
            file_count=file_count,
            latest_activity_at=latest_activity_at,
            extractor=extractor,
            anomalies=anomalies,
        ))

    roots_summary = []
    for root in roots:
        try:
            stat = root.stat()
            roots_summary.append({"path": str(root), "exists": True, "mtime": int(stat.st_mtime)})
        except OSError:
            roots_summary.append({"path": str(root), "exists": False, "mtime": None})

    cache = {
        "schema_version": 1,
        "collector_version": None,
        "full": full,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "roots": roots_summary,
        "fingerprint": stable_hash({
            "roots": roots_summary,
            "source_paths": sorted(str(p) for p in candidates.values()),
            "source_count": len(sources),
        }),
        "source_count": len(sources),
        "sources": [source.to_cache() for source in sources],
    }
    return sources, cache


def discovery_needs_full(
    state: dict[str, Any],
    *,
    claude_dir: Path,
    collector_version: str,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    cache = state.get("discovery") if isinstance(state.get("discovery"), dict) else {}
    if not cache:
        return True
    if cache.get("collector_version") != collector_version:
        return True
    if cache.get("claude_config_dir") != str(claude_dir):
        return True
    discovered_at = parse_ts(cache.get("discovered_at"))
    if not discovered_at or now - discovered_at > timedelta(hours=DISCOVERY_CACHE_TTL_HOURS):
        return True

    old_roots = cache.get("roots") if isinstance(cache.get("roots"), list) else []
    current_roots = []
    for item in old_roots:
        root = Path(str(item.get("path", "")))
        try:
            stat = root.stat()
            current_roots.append({"path": str(root), "exists": True, "mtime": int(stat.st_mtime)})
        except OSError:
            current_roots.append({"path": str(root), "exists": False, "mtime": None})
    return current_roots != old_roots


def cache_discovery(state: dict[str, Any], cache: dict[str, Any], *, claude_dir: Path, collector_version: str) -> dict[str, Any]:
    state = dict(state)
    discovery = dict(cache)
    discovery["collector_version"] = collector_version
    discovery["claude_config_dir"] = str(claude_dir)
    state["discovery"] = discovery
    return state


def evidence_exists(sources: Iterable[SourceRecord], surfaces: set[str] | None = None) -> bool:
    for source in sources:
        if source.status != "present":
            continue
        if surfaces and source.surface not in surfaces:
            continue
        return True
    return False
