"""Claude Code JSONL transcripts -> deduplicated per-request and per-session usage.

Accounting choices:
1. Claude Code may write multiple JSONL records for one requestId; we deduplicate by requestId.
   Resumed/forked sessions and sidechains copy earlier responses into other files, so requests are
   also deduplicated across every file and transcript root, keeping the original (non-sidechain) copy.
2. For a request, prefer a finalized assistant record (stop_reason != null and/or
   iterations present) over early streaming snapshots.
3. We DO NOT sum top-level usage + iterations. `iterations` is diagnostic detail because
   some Claude Code versions roll multiple iterations into top-level context counters.
4. Subagent transcripts can lack a finalized usage record; those requests are marked
   incomplete instead of pretending they are exact.
5. Totals are "reported transcript tokens", not subscription quota consumption.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from claude_usage.constants import SESSION_IDLE_GAP_SECONDS
from claude_usage.models import RequestUsage, SessionSummary
from claude_usage.util import parse_ts, stable_hash


def safe_project_from_path(path: Path, projects_dir: Path) -> str:
    try:
        rel = path.relative_to(projects_dir)
        first = rel.parts[0] if rel.parts else path.parent.name
    except Exception:
        first = path.parent.name

    # Claude encodes path separators as "-". Preserve human-friendly path
    # where possible.
    if first.startswith("-Users-"):
        decoded = first.replace("-", "/")
        if decoded.startswith("/Users/"):
            return decoded
        # Fallback: strip leading username-ish encoding.
        return first
    return first

def is_subagent_path(path: Path) -> bool:
    return "subagents" in path.parts

def agent_id_from_path(path: Path) -> str | None:
    for part in path.parts:
        if part.startswith("agent-") and part.endswith(".jsonl"):
            return part[:-6]
    return None

def session_id_from_path(path: Path, projects_dir: Path) -> str | None:
    try:
        rel = path.relative_to(projects_dir)
    except Exception:
        return None
    parts = rel.parts
    for p in parts:
        if re.fullmatch(r"[0-9a-fA-F-]{36}", p):
            return p
    # Main session filenames are UUID.jsonl.
    stem = path.stem
    if re.fullmatch(r"[0-9a-fA-F-]{36}", stem):
        return stem
    return None

def discover_jsonl(projects_dir: Path) -> list[Path]:
    if not projects_dir.exists():
        return []
    return sorted(projects_dir.rglob("*.jsonl"))

def json_load_line(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None

def tool_names_from_content(content: Any) -> list[str]:
    names: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name")
                if isinstance(name, str):
                    names.append(name)
    elif isinstance(content, dict):
        if content.get("type") == "tool_use" and isinstance(content.get("name"), str):
            names.append(content["name"])
    return names

def usage_is_final(msg: dict[str, Any], usage: dict[str, Any]) -> bool:
    if usage.get("iterations"):
        return True
    stop_reason = msg.get("stop_reason")
    if stop_reason not in (None, "", "null"):
        return True
    # Some versions may use stop_details.
    if msg.get("stop_details"):
        return True
    return False

def extract_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    return usage

def choose_best_record(records: list[tuple[dict[str, Any], dict[str, Any]]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Pick the best usage record for one requestId.

    Preference:
      1. finalized record with iterations
      2. finalized record
      3. largest cache-read + cache-create + output
    """
    def score(item: tuple[dict[str, Any], dict[str, Any]]) -> tuple[int, int, int]:
        obj, usage = item
        msg = obj.get("message") or {}
        finalized = usage_is_final(msg, usage)
        iterations = 1 if usage.get("iterations") else 0
        size = sum(
            int(usage.get(k) or 0)
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
        return (int(finalized), iterations, size)

    return max(records, key=score)

def parse_requests(
    paths: Iterable[Path],
    projects_dir: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    surface: str = "claude_code",
) -> list[RequestUsage]:
    requests: list[RequestUsage] = []

    for path in paths:
        project = safe_project_from_path(path, projects_dir)
        if surface != "claude_code":
            project = f"{surface}/{project}"
        session_id = session_id_from_path(path, projects_dir) or f"file:{path.name}"
        subagent = is_subagent_path(path)
        agent_id = agent_id_from_path(path)
        if subagent and agent_id:
            # Subagent transcripts live under the parent session's folder; keep them as their own
            # sessions so subagent work is not folded into (and mislabeled as) the parent.
            session_id = f"{session_id}:{agent_id}"

        by_request: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        fallback_counter = 0
        file_model_hint = "unknown"
        # Line-level context is only on some records; carry the latest value forward.
        hints: dict[str, str] = {}

        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    obj = json_load_line(line)
                    if not obj:
                        continue

                    ts = parse_ts(obj.get("timestamp"))
                    if start and ts and ts < start:
                        continue
                    if end and ts and ts >= end:
                        continue

                    msg = obj.get("message")
                    if isinstance(msg, dict) and msg.get("model"):
                        file_model_hint = str(msg["model"])
                    for key in ("gitBranch", "entrypoint", "version"):
                        if obj.get(key):
                            hints[key] = str(obj[key])

                    usage = extract_usage(obj)
                    if usage is None:
                        continue

                    msg = obj.get("message") or {}
                    request_id = msg.get("id") or obj.get("requestId")
                    if not request_id:
                        # A tiny number of versions/events can omit ids.
                        request_id = f"fallback:{obj.get('uuid') or fallback_counter}"
                        fallback_counter += 1

                    by_request[str(request_id)].append(({**obj, "_hints": dict(hints)}, usage))
        except (OSError, UnicodeError):
            continue

        for request_id, records_for_request in by_request.items():
            obj, usage = choose_best_record(records_for_request)
            msg = obj.get("message") or {}
            ts = parse_ts(obj.get("timestamp"))
            timestamp = ts.isoformat() if ts else str(obj.get("timestamp") or "")

            cache = usage.get("cache_creation") or {}
            if not isinstance(cache, dict):
                cache = {}

            iterations = usage.get("iterations") or []
            if not isinstance(iterations, list):
                iterations = []

            iter_in = sum(int((x or {}).get("input_tokens") or 0) for x in iterations if isinstance(x, dict))
            iter_out = sum(int((x or {}).get("output_tokens") or 0) for x in iterations if isinstance(x, dict))
            iter_cr = sum(int((x or {}).get("cache_creation_input_tokens") or 0) for x in iterations if isinstance(x, dict))
            iter_rr = sum(int((x or {}).get("cache_read_input_tokens") or 0) for x in iterations if isinstance(x, dict))
            advisor_count = sum(
                1 for x in iterations
                if isinstance(x, dict) and x.get("type") == "advisor_message"
            )

            tools = tool_names_from_content(msg.get("content"))
            server = usage.get("server_tool_use") or {}
            if not isinstance(server, dict):
                server = {}

            request = RequestUsage(
                request_id=request_id,
                timestamp=timestamp,
                model=str(msg.get("model") or file_model_hint or "unknown"),
                file=str(path),
                project=project,
                session_id=session_id,
                is_subagent=subagent,
                agent_id=agent_id,
                finalized=usage_is_final(msg, usage),
                duplicate_records=len(records_for_request),
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
                cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                cache_5m_tokens=int(cache.get("ephemeral_5m_input_tokens") or 0),
                cache_1h_tokens=int(cache.get("ephemeral_1h_input_tokens") or 0),
                web_search_requests=int(server.get("web_search_requests") or 0),
                web_fetch_requests=int(server.get("web_fetch_requests") or 0),
                iteration_count=len(iterations),
                iteration_input_tokens=iter_in,
                iteration_output_tokens=iter_out,
                iteration_cache_read_tokens=iter_rr,
                iteration_cache_creation_tokens=iter_cr,
                advisor_iterations=advisor_count,
                tool_use_count=len(tools),
                tool_names=tuple(tools),
                stop_reason=str(msg.get("stop_reason")) if msg.get("stop_reason") is not None else None,
                parent_uuid=obj.get("parentUuid"),
                surface=surface,
                is_sidechain=bool(obj.get("isSidechain")),
                git_branch=obj.get("gitBranch") or obj["_hints"].get("gitBranch"),
                entrypoint=obj.get("entrypoint") or obj["_hints"].get("entrypoint"),
                claude_code_version=obj.get("version") or obj["_hints"].get("version"),
            )

            requests.append(request)
    return requests


def dedupe_requests(requests: list[RequestUsage]) -> list[RequestUsage]:
    """One request per response id across all files; copies in resumed sessions or sidechains are dropped."""
    mtimes: dict[str, float] = {}

    def mtime(file: str) -> float:
        if file not in mtimes:
            try:
                mtimes[file] = Path(file).stat().st_mtime
            except OSError:
                mtimes[file] = float("inf")
        return mtimes[file]

    def preference(r: RequestUsage) -> tuple:
        # The original copy: not a sidechain, finalized, largest, then from the older file.
        return (r.is_sidechain, not r.finalized, -r.reported_total, mtime(r.file), r.file)

    chosen: dict[str, RequestUsage] = {}
    copies: Counter = Counter()
    for request in requests:
        copies[request.request_id] += request.duplicate_records
        current = chosen.get(request.request_id)
        if current is None or preference(request) < preference(current):
            chosen[request.request_id] = request
    for request_id, request in chosen.items():
        request.duplicate_records = copies[request_id]
    return list(chosen.values())


def summarize_sessions(requests: list[RequestUsage]) -> dict[str, SessionSummary]:
    session_buckets: dict[str, list[RequestUsage]] = defaultdict(list)
    for request in requests:
        session_buckets[request.session_id].append(request)

    sessions: dict[str, SessionSummary] = {}
    for sid, reqs in session_buckets.items():
        reqs.sort(key=lambda r: parse_ts(r.timestamp) or datetime.min.replace(tzinfo=timezone.utc))
        first = reqs[0]
        last = reqs[-1]
        models = Counter(r.model for r in reqs)
        sessions[sid] = SessionSummary(
            session_id=sid,
            project=first.project,
            cwd="",
            file=first.file,
            is_subagent=first.is_subagent,
            agent_id=first.agent_id,
            first_ts=first.timestamp,
            last_ts=last.timestamp,
            requests=len(reqs),
            finalized_requests=sum(r.finalized for r in reqs),
            incomplete_requests=sum(not r.finalized for r in reqs),
            tool_calls=sum(r.tool_use_count for r in reqs),
            messages=len(reqs),
            input_tokens=sum(r.input_tokens for r in reqs),
            output_tokens=sum(r.output_tokens for r in reqs),
            cache_read=sum(r.cache_read_input_tokens for r in reqs),
            cache_creation=sum(r.cache_creation_input_tokens for r in reqs),
            cache_5m=sum(r.cache_5m_tokens for r in reqs),
            cache_1h=sum(r.cache_1h_tokens for r in reqs),
            reported_total=sum(r.reported_total for r in reqs),
            models=dict(models),
            surface=first.surface,
            git_branch=next((r.git_branch for r in reversed(reqs) if r.git_branch), None),
            entrypoints=tuple(sorted({r.entrypoint for r in reqs if r.entrypoint})),
            claude_code_version=max((r.claude_code_version for r in reqs if r.claude_code_version), key=version_key, default=None),
            active_spans=active_spans(reqs),
        )
    return sessions


def active_spans(requests: list[RequestUsage]) -> tuple[tuple[str, str], ...]:
    """Split time-ordered requests into stretches of work separated by idle gaps."""
    spans: list[tuple[str, str]] = []
    start = end = None
    for request in requests:
        ts = parse_ts(request.timestamp)
        if not ts:
            continue
        if end is not None and (ts - end).total_seconds() > SESSION_IDLE_GAP_SECONDS:
            spans.append((start.isoformat(), end.isoformat()))
            start = None
        if start is None:
            start = ts
        end = ts
    if start is not None:
        spans.append((start.isoformat(), end.isoformat()))
    return tuple(spans)


def version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-+]", version))


def parse_all(
    paths: Iterable[Path],
    projects_dir: Path,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    surface: str = "claude_code",
) -> tuple[list[RequestUsage], dict[str, SessionSummary]]:
    requests = dedupe_requests(parse_requests(paths, projects_dir, start, end, surface=surface))
    return requests, summarize_sessions(requests)


def stats_cache_daily(stats: dict[str, Any] | None, before_day: str | None) -> list[tuple[str, str, int]]:
    """(day, model, tokens) from Claude Code's stats cache for days older than the oldest transcript.

    Claude Code deletes transcripts after about 30 days; the stats cache is the only record of older usage.
    Days that transcripts still cover are skipped so they are never counted twice.
    """
    rows: list[tuple[str, str, int]] = []
    for entry in (stats or {}).get("dailyModelTokens") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("tokensByModel"), dict):
            continue
        day = str(entry.get("date") or "")
        if not day or (before_day and day >= before_day):
            continue
        for model, tokens in entry["tokensByModel"].items():
            if isinstance(tokens, int) and tokens > 0:
                rows.append((day, str(model), tokens))
    return rows


def load_stats_cache(stats_cache: Path) -> dict[str, Any] | None:
    if not stats_cache.exists():
        return None
    try:
        return json.loads(stats_cache.read_text(encoding="utf-8"))
    except Exception:
        return None


def transcript_digest(paths: list[Path]) -> str:
    """Hash of every transcript's (path hash, size, mtime): changes whenever any transcript changes."""
    inventory = []
    for path in paths:
        try:
            stat = path.stat()
            inventory.append({
                "path_hash": stable_hash(str(path)),
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
        except OSError:
            inventory.append({
                "path_hash": stable_hash(str(path)),
                "size": None,
                "mtime": None,
            })
    return stable_hash(inventory)
