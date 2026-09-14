from __future__ import annotations

from dataclasses import dataclass

from claude_usage.util import parse_ts


@dataclass
class RequestUsage:
    request_id: str
    timestamp: str
    model: str
    file: str
    project: str
    session_id: str
    is_subagent: bool
    agent_id: str | None
    finalized: bool
    duplicate_records: int

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_5m_tokens: int = 0
    cache_1h_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0

    iteration_count: int = 0
    iteration_input_tokens: int = 0
    iteration_output_tokens: int = 0
    iteration_cache_read_tokens: int = 0
    iteration_cache_creation_tokens: int = 0
    advisor_iterations: int = 0

    tool_use_count: int = 0
    tool_names: tuple[str, ...] = ()

    stop_reason: str | None = None
    parent_uuid: str | None = None

    @property
    def reported_total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

@dataclass
class SessionSummary:
    session_id: str
    project: str
    cwd: str
    file: str
    is_subagent: bool
    agent_id: str | None
    first_ts: str
    last_ts: str
    requests: int
    finalized_requests: int
    incomplete_requests: int
    tool_calls: int
    messages: int
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_creation: int
    cache_5m: int
    cache_1h: int
    reported_total: int
    models: dict[str, int]

    @property
    def duration_seconds(self) -> float:
        a = parse_ts(self.first_ts)
        b = parse_ts(self.last_ts)
        if not a or not b:
            return 0.0
        return max(0.0, (b - a).total_seconds())

    @property
    def tokens_per_hour(self) -> float:
        if self.duration_seconds <= 0:
            return 0.0
        return self.reported_total / (self.duration_seconds / 3600.0)

@dataclass
class ActivityDaily:
    day: str
    surface: str
    source_id: str
    sessions: int = 0
    messages: int = 0
    turns: int = 0
    tool_calls: int = 0
    reported_total: int = 0
    confidence: str = "evidence"
