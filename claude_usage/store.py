"""The only reader/writer of the agent's config.json and state.json."""

from __future__ import annotations

from typing import Any

from claude_usage.paths import config_path, state_path
from claude_usage.util import ensure_private_dir, read_json_file, write_json_file


def load_config() -> dict[str, Any]:
    return read_json_file(config_path())


def save_config(config: dict[str, Any]):
    ensure_private_dir(config_path().parent)
    write_json_file(config_path(), config)


def load_state() -> dict[str, Any]:
    return read_json_file(state_path())


def update_state(changes: dict[str, Any]) -> dict[str, Any]:
    # Re-read before merging so a concurrent command's fields aren't overwritten with stale copies.
    state = load_state()
    state.update(changes)
    ensure_private_dir(state_path().parent)
    write_json_file(state_path(), state)
    return state


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(config)
    if redacted.get("collector_token"):
        redacted["collector_token"] = "***"
    return redacted
