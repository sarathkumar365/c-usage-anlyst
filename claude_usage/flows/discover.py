"""Discovery flow: find local Claude sources and cache the inventory in state.json."""

from __future__ import annotations

from claude_usage.constants import APP_VERSION
from claude_usage.discovery import SourceRecord, cache_discovery, discover_claude_sources, discovery_needs_full
from claude_usage.paths import ClaudePaths
from claude_usage.store import load_state, update_state


def run_discovery(claude: ClaudePaths, full: bool = True) -> list[SourceRecord]:
    cached = []
    if not full:
        discovery = load_state().get("discovery")
        discovery = discovery if isinstance(discovery, dict) else {}
        cached = discovery.get("sources") if isinstance(discovery.get("sources"), list) else []
    sources, cache = discover_claude_sources(claude.claude_dir, full=full, cached_sources=cached)
    discovery_state = cache_discovery({}, cache, claude_dir=claude.claude_dir, collector_version=APP_VERSION)
    update_state({"discovery": discovery_state["discovery"]})
    return sources


def discover_for_sync(claude: ClaudePaths) -> tuple[list[SourceRecord], bool]:
    full = discovery_needs_full(load_state(), claude_dir=claude.claude_dir, collector_version=APP_VERSION)
    return run_discovery(claude, full=full), full
