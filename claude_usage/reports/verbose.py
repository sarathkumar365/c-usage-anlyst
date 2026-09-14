"""Detailed table-heavy report (--verbose)."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_usage.metrics import group_sum
from claude_usage.models import RequestUsage, SessionSummary
from claude_usage.ui import c, fmt_compact, fmt_duration, fmt_int, print_table, total_pct
from claude_usage.util import date_key


def analyze(requests: list[RequestUsage], sessions: dict[str, SessionSummary], stats: dict[str, Any] | None, claude_dir: Path):
    if not requests:
        print(c("No finalized/usage-bearing assistant records found.", "red"))
        return

    total = sum(r.reported_total for r in requests)
    inp = sum(r.input_tokens for r in requests)
    out = sum(r.output_tokens for r in requests)
    cr = sum(r.cache_creation_input_tokens for r in requests)
    rr = sum(r.cache_read_input_tokens for r in requests)
    cache_total = cr + rr
    tools = sum(r.tool_use_count for r in requests)
    finalized = sum(r.finalized for r in requests)
    incomplete = len(requests) - finalized
    advisor = sum(r.advisor_iterations for r in requests)
    web_search = sum(r.web_search_requests for r in requests)
    web_fetch = sum(r.web_fetch_requests for r in requests)
    duplicate_records = sum(max(0, r.duplicate_records - 1) for r in requests)

    print()
    print(c("═" * 90, "cyan"))
    print(c(" CLAUDE CODE LOCAL USAGE ANALYZER", "bold"))
    print(c("═" * 90, "cyan"))
    print(f"Claude directory : {claude_dir}")
    print(f"Transcript files : {len(set(r.file for r in requests)):,}")
    print(f"Deduped requests : {len(requests):,}")
    print(f"Finalized        : {finalized:,}")
    print(f"Incomplete       : {incomplete:,}")
    print(f"Duplicate records removed: {duplicate_records:,}")
    print()

    print(c("TOTAL REPORTED TOKEN VOLUME", "bold"))
    print(f"  Total reported : {fmt_int(total)} ({fmt_compact(total)})")
    print(f"  Input          : {fmt_int(inp)} ({total_pct(inp, total):.1f}%)")
    print(f"  Output         : {fmt_int(out)} ({total_pct(out, total):.1f}%)")
    print(f"  Cache create   : {fmt_int(cr)} ({total_pct(cr, total):.1f}%)")
    print(f"  Cache read     : {fmt_int(rr)} ({total_pct(rr, total):.1f}%)")
    if total:
        print(f"  Cache share    : {cache_total / total * 100:.1f}%")
    print(f"  5m cache       : {fmt_int(sum(r.cache_5m_tokens for r in requests))}")
    print(f"  1h cache       : {fmt_int(sum(r.cache_1h_tokens for r in requests))}")
    print()

    print(c("MODELS", "bold"))
    by_model = group_sum(requests, lambda r: r.model)
    model_rows = []
    for model, tokens in sorted(by_model.items(), key=lambda x: x[1], reverse=True):
        rows_req = [r for r in requests if r.model == model]
        model_rows.append([
            model,
            fmt_compact(tokens),
            f"{tokens / total * 100:.1f}%",
            fmt_int(len(rows_req)),
            fmt_int(sum(r.cache_read_input_tokens for r in rows_req)),
        ])
    print_table(["MODEL", "TOKENS", "SHARE", "REQ", "CACHE READ"], model_rows, 20)
    print()

    print(c("PROJECTS", "bold"))
    by_project = group_sum(requests, lambda r: r.project)
    project_rows = []
    for project, tokens in sorted(by_project.items(), key=lambda x: x[1], reverse=True):
        reqs = [r for r in requests if r.project == project]
        project_rows.append([
            project,
            fmt_compact(tokens),
            f"{tokens / total * 100:.1f}%",
            fmt_int(len(reqs)),
            len(set(r.session_id for r in reqs)),
        ])
    print_table(["PROJECT", "TOKENS", "SHARE", "REQ", "SESSIONS"], project_rows, 15)
    print()

    print(c("SUBAGENTS", "bold"))
    sub = [r for r in requests if r.is_subagent]
    main = [r for r in requests if not r.is_subagent]
    sub_total = sum(r.reported_total for r in sub)
    print(f"  Main sessions : {fmt_int(sum(r.reported_total for r in main))}")
    print(f"  Subagents     : {fmt_int(sub_total)} ({(sub_total / total * 100 if total else 0):.1f}%)")
    print(f"  Subagent reqs : {fmt_int(len(sub))}")
    if sub:
        by_agent = defaultdict(int)
        for r in sub:
            by_agent[(r.project, r.session_id, r.agent_id or "?")] += r.reported_total
        rows = []
        for (proj, sid, aid), tokens in sorted(by_agent.items(), key=lambda x: x[1], reverse=True)[:20]:
            rows.append([proj, sid[:12], aid or "?", fmt_compact(tokens), f"{tokens/total*100:.1f}%"])
        print_table(["PROJECT", "SESSION", "AGENT", "TOKENS", "TOTAL SHARE"], rows, 20)
    print()

    print(c("TOOL ACTIVITY", "bold"))
    tool_counter = Counter()
    for r in requests:
        for name in r.tool_names:
            tool_counter[name] += 1
    print(f"  Tool calls detected : {fmt_int(tools)}")
    print(f"  Server web searches : {fmt_int(web_search)}")
    print(f"  Server web fetches  : {fmt_int(web_fetch)}")
    print(f"  Advisor iterations  : {fmt_int(advisor)}")
    tool_rows = [[name, count] for name, count in tool_counter.most_common(25)]
    print_table(["TOOL", "CALLS"], tool_rows, 25)
    print()

    print(c("DAILY USAGE", "bold"))
    daily = group_sum(requests, lambda r: date_key(r.timestamp))
    daily_rows = []
    for day, tokens in sorted(daily.items(), key=lambda x: x[1], reverse=True)[:20]:
        day_reqs = [r for r in requests if date_key(r.timestamp) == day]
        daily_rows.append([
            day,
            fmt_compact(tokens),
            f"{tokens/total*100:.1f}%",
            fmt_int(len(day_reqs)),
            fmt_int(sum(r.tool_use_count for r in day_reqs)),
        ])
    print_table(["DATE", "TOKENS", "SHARE", "REQ", "TOOLS"], daily_rows, 20)
    print()

    print(c("TOP SESSIONS", "bold"))
    session_rows = []
    for s in sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True)[:25]:
        session_rows.append([
            s.project,
            s.session_id[:12],
            fmt_compact(s.reported_total),
            f"{s.reported_total/total*100:.1f}%",
            fmt_int(s.requests),
            fmt_duration(s.duration_seconds),
            f"{fmt_compact(s.tokens_per_hour)}/h" if s.tokens_per_hour else "n/a",
            "subagent" if s.is_subagent else "main",
        ])
    print_table(["PROJECT", "SESSION", "TOKENS", "SHARE", "REQ", "DURATION", "TOK/H", "TYPE"], session_rows, 25)
    print()

    print(c("USAGE / CONTEXT DIAGNOSTICS", "bold"))
    diagnostics: list[tuple[str, str, str]] = []

    # Cache dominance
    if total and rr / total > 0.60:
        diagnostics.append(("HIGH", "Cache-read dominated", f"Cache reads are {rr/total*100:.1f}% of reported transcript volume."))
    if total and cr / total > 0.15:
        diagnostics.append(("HIGH", "Large cache creation", f"Cache creation is {cr/total*100:.1f}% of reported volume."))

    # Session concentration
    if sessions:
        largest = max(sessions.values(), key=lambda s: s.reported_total)
        share = largest.reported_total / total if total else 0
        if share > 0.20:
            diagnostics.append(("HIGH", "Single-session concentration", f"{largest.session_id[:12]} accounts for {share*100:.1f}% of all reported tokens."))

    # Extremely large individual requests
    per_req = sorted(requests, key=lambda r: r.reported_total, reverse=True)
    med = statistics.median([r.reported_total for r in requests])
    if med > 0 and per_req[0].reported_total > med * 20:
        diagnostics.append(("HIGH", "Outlier request", f"Largest request is {per_req[0].reported_total/med:.1f}× the median request."))

    # Subagents
    if total and sub_total / total > 0.40:
        diagnostics.append(("HIGH", "Subagent-heavy workload", f"Subagents account for {sub_total/total*100:.1f}% of reported transcript tokens."))

    # Incomplete subagent accounting
    incomplete_sub = sum(1 for r in sub if not r.finalized)
    if incomplete_sub:
        diagnostics.append(("WARN", "Incomplete subagent records", f"{incomplete_sub:,} subagent requests have no obvious finalized usage snapshot; totals may undercount those requests."))

    # Advisor iterations
    if advisor:
        diagnostics.append(("WARN", "Advisor iterations detected", f"{advisor:,} advisor iterations were recorded. Do not add iterations to top-level usage; they may already be rolled up."))

    # Cache re-write pattern
    rewrite_candidates = 0
    for r in requests:
        if r.cache_5m_tokens > 0 and r.cache_read_input_tokens > 0 and r.cache_5m_tokens > max(50_000, r.cache_read_input_tokens * 0.25):
            rewrite_candidates += 1
    if rewrite_candidates:
        diagnostics.append(("WARN", "Possible cache churn", f"{rewrite_candidates:,} requests show substantial simultaneous 5m cache creation and cache read activity."))

    # Duplicate handling
    if duplicate_records:
        diagnostics.append(("INFO", "Streaming duplicates removed", f"{duplicate_records:,} repeated JSONL records were collapsed by requestId before summing."))

    if not diagnostics:
        diagnostics.append(("OK", "No major heuristic anomaly", "The analyzer did not find a strong usage anomaly from the local data."))

    for sev, name, detail in diagnostics:
        color = "red" if sev == "HIGH" else "yellow" if sev == "WARN" else "cyan" if sev == "INFO" else "green"
        print(f"  {c(sev, color)}  {name}: {detail}")
    print()

    print(c("CANDIDATE EXPENSIVE REQUESTS", "bold"))
    rows = []
    for r in per_req[:20]:
        rows.append([
            date_key(r.timestamp),
            r.project,
            r.model,
            r.request_id[:12],
            fmt_compact(r.reported_total),
            fmt_compact(r.cache_read_input_tokens),
            fmt_compact(r.cache_creation_input_tokens),
            r.stop_reason or "?",
            "final" if r.finalized else "partial",
            f"{len(r.tool_names)}",
        ])
    print_table(["DATE", "PROJECT", "MODEL", "REQUEST", "TOTAL", "CACHE READ", "CACHE CREATE", "STOP", "STATE", "TOOLS"], rows, 20)
    print()

    print(c("CROSS-CHECK: stats-cache.json", "bold"))
    if stats:
        cache_date = stats.get("lastComputedDate")
        print(f"  stats-cache lastComputedDate : {cache_date}")
        total_sessions = stats.get("totalSessions")
        total_messages = stats.get("totalMessages")
        print(f"  stats totalSessions           : {total_sessions}")
        print(f"  stats totalMessages           : {total_messages}")
        if stats.get("dailyModelTokens"):
            daily_model_total = 0
            for item in stats["dailyModelTokens"]:
                for v in (item.get("tokensByModel") or {}).values():
                    try:
                        daily_model_total += int(v)
                    except Exception:
                        pass
            print(f"  stats dailyModelTokens sum    : {fmt_int(daily_model_total)}")
        if cache_date and cache_date < datetime.now().strftime("%Y-%m-%d"):
            print(c("  NOTE: stats-cache is stale relative to current date; JSONL is used for current/historical detail.", "yellow"))
    else:
        print("  stats-cache.json not available or unreadable.")

    print()
    print(c("ACCURACY NOTES", "bold"))
    print("  • Counts are based on finalized/deduplicated JSONL usage snapshots when available.")
    print("  • Top-level usage is used for totals; iterations are diagnostics, not added again.")
    print("  • Subagent requests without a finalized snapshot are flagged as incomplete.")
    print("  • Local token totals are NOT the same thing as your Claude Pro/Max remaining quota.")
    print("  • Any dollar figure derived from model prices is an API-style estimate, not subscription billing.")
    print()
