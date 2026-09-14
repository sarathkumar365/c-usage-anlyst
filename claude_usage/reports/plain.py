"""Default plain-English usage report."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from claude_usage.metrics import calculate_estimated_cost, group_sum
from claude_usage.models import RequestUsage, SessionSummary
from claude_usage.ui import (
    c,
    fmt_compact,
    fmt_duration,
    fmt_int,
    fmt_money,
    fmt_pct,
    friendly_tool_name,
    plain_metric,
    plain_section,
    plain_status,
    sentence_join,
    short_project_name,
    total_pct,
)
from claude_usage.util import date_key


def plain_usage_story(
    requests: list[RequestUsage],
    sessions: dict[str, SessionSummary],
    stats: dict[str, Any] | None,
):
    if not requests:
        print(c("No usage-bearing Claude Code records found for this filter.", "red"))
        return

    total = sum(r.reported_total for r in requests)
    inp = sum(r.input_tokens for r in requests)
    out = sum(r.output_tokens for r in requests)
    cr = sum(r.cache_creation_input_tokens for r in requests)
    rr = sum(r.cache_read_input_tokens for r in requests)
    cache_total = cr + rr
    finalized = sum(r.finalized for r in requests)
    incomplete = len(requests) - finalized
    duplicate_records = sum(max(0, r.duplicate_records - 1) for r in requests)
    tool_calls = sum(r.tool_use_count for r in requests)
    estimated_cost = sum(calculate_estimated_cost(r) for r in requests)

    by_model = group_sum(requests, lambda r: r.model)
    top_models = sorted(by_model.items(), key=lambda x: x[1], reverse=True)

    by_project = group_sum(requests, lambda r: r.project)
    top_projects = sorted(by_project.items(), key=lambda x: x[1], reverse=True)

    daily = group_sum(requests, lambda r: date_key(r.timestamp))
    top_days = sorted(daily.items(), key=lambda x: x[1], reverse=True)

    top_sessions = sorted(sessions.values(), key=lambda x: x.reported_total, reverse=True)
    sub_total = sum(r.reported_total for r in requests if r.is_subagent)
    main_total = total - sub_total

    cache_pct = cache_total / total * 100 if total else 0.0
    cache_read_pct = rr / total * 100 if total else 0.0
    output_pct = total_pct(out, total)
    input_pct = total_pct(inp, total)
    sub_pct = sub_total / total * 100 if total else 0.0
    largest_session_pct = (
        top_sessions[0].reported_total / total * 100
        if top_sessions and total
        else 0.0
    )
    top_project_pct = top_projects[0][1] / total * 100 if top_projects and total else 0.0
    top_model_pct = top_models[0][1] / total * 100 if top_models and total else 0.0

    health_score = 0
    if cache_read_pct > 80:
        health_score += 2
    elif cache_read_pct > 60:
        health_score += 1
    if largest_session_pct > 25:
        health_score += 2
    elif largest_session_pct > 15:
        health_score += 1
    if top_project_pct > 75:
        health_score += 1
    if top_model_pct > 75 and top_models and top_models[0][0].startswith("claude-opus"):
        health_score += 1
    if sub_pct > 25:
        health_score += 1

    if health_score >= 5:
        verdict = ("High usage pressure", "bad")
    elif health_score >= 2:
        verdict = ("Improvement needed", "warn")
    else:
        verdict = ("Looks manageable", "good")

    print()
    print(c("=" * 80, "cyan"))
    print(c(" CLAUDE USAGE - PLAIN ENGLISH REPORT", "bold"))
    print(c("=" * 80, "cyan"))

    plain_section("Overall")
    plain_metric("Verdict", verdict[0], severity=verdict[1])
    plain_metric("Total usage", f"{fmt_compact(total)} tokens", f"{fmt_int(len(requests))} requests", "info")
    plain_metric("Context reuse", fmt_pct(cache_pct), "cache read + cache create", "bad" if cache_pct > 90 else "warn")
    plain_metric("Model output", fmt_pct(output_pct), "actual answer text", "ok" if output_pct < 10 else "warn")
    plain_metric("Typed/new input", fmt_pct(input_pct), "your direct prompt/input share", "ok" if input_pct < 10 else "warn")
    if estimated_cost:
        plain_metric("API estimate", fmt_money(estimated_cost), "not your subscription bill", "warn")

    plain_section("Health Check")
    if cache_total and total:
        if cache_read_pct > 80:
            plain_status(
                "Cache usage",
                f"Very high. {fmt_pct(cache_read_pct)} is cached context being reread.",
                "bad",
            )
            plain_status(
                "Meaning",
                "The big number is mostly Claude carrying a large session/project context forward.",
                "info",
            )
        elif cache_read_pct > 60:
            plain_status(
                "Cache usage",
                f"Elevated. {fmt_pct(cache_read_pct)} is cached context being reread.",
                "warn",
            )
        else:
            plain_status("Cache usage", "Reasonable. Cached context is not dominating the report.", "good")

    if largest_session_pct > 25:
        plain_status(
            "Session size",
            f"One session is very large at {fmt_pct(largest_session_pct)} of total usage.",
            "bad",
        )
    elif largest_session_pct > 15:
        plain_status(
            "Session size",
            f"One session is doing a lot of work at {fmt_pct(largest_session_pct)} of total usage.",
            "warn",
        )
    else:
        plain_status("Session size", "Usage is not dominated by one session.", "good")

    if sub_pct > 25:
        plain_status("Subagents", f"High at {fmt_pct(sub_pct)} of total usage.", "warn")
    elif sub_total:
        plain_status("Subagents", f"Low impact at {fmt_pct(sub_pct)} of total usage.", "good")
    else:
        plain_status("Subagents", "No subagent usage found in this filter.", "good")

    if incomplete:
        plain_status(
            "Data quality",
            f"{fmt_int(incomplete)} requests were incomplete, so usage may be slightly undercounted.",
            "warn",
        )
    else:
        plain_status("Data quality", "All usage records in this filter look finalized.", "good")

    plain_section("Main Drivers")

    if top_projects:
        for i, (project, tokens) in enumerate(top_projects[:3], start=1):
            share = tokens / total * 100 if total else 0.0
            severity = "bad" if i == 1 and share > 75 else "warn" if i == 1 and share > 50 else "info"
            plain_metric(f"Project #{i}", short_project_name(project), f"{fmt_compact(tokens)} / {fmt_pct(share)}", severity)

    if top_models:
        for i, (model, tokens) in enumerate(top_models[:3], start=1):
            share = tokens / total * 100 if total else 0.0
            severity = "bad" if i == 1 and model.startswith("claude-opus") and share > 75 else "warn" if i == 1 and share > 50 else "info"
            plain_metric(f"Model #{i}", model, f"{fmt_compact(tokens)} / {fmt_pct(share)}", severity)

    if top_days:
        day, tokens = top_days[0]
        share = tokens / total * 100 if total else 0.0
        plain_metric("Heaviest day", day, f"{fmt_compact(tokens)} / {fmt_pct(share)}", "warn" if share > 25 else "info")

    if top_sessions:
        largest = top_sessions[0]
        plain_metric(
            "Largest session",
            largest.session_id[:12],
            (
                f"{short_project_name(largest.project)}, {fmt_pct(largest_session_pct)}, "
                f"{fmt_duration(largest.duration_seconds)}, {fmt_compact(largest.tokens_per_hour)}/hour"
            ),
            "bad" if largest_session_pct > 25 else "warn" if largest_session_pct > 15 else "info",
        )

    if sub_total:
        plain_metric("Main sessions", fmt_compact(main_total), "normal chat/tool sessions", "info")
        plain_metric("Subagents", fmt_compact(sub_total), fmt_pct(sub_pct), "warn" if sub_pct > 25 else "ok")

    tool_counter = Counter()
    for r in requests:
        for name in r.tool_names:
            tool_counter[name] += 1
    if tool_counter:
        tool_phrases = [f"{friendly_tool_name(name)} ({count})" for name, count in tool_counter.most_common(3)]
        plain_metric("Top tools", sentence_join(tool_phrases), "file/code inspection activity", "info")
    elif tool_calls:
        plain_metric("Tool calls", fmt_int(tool_calls), "", "info")

    reasons: list[str] = []
    if total and rr / total > 0.60:
        reasons.append("large cached context being replayed across requests")
    if top_sessions and total and top_sessions[0].reported_total / total > 0.20:
        reasons.append("one very large session dominating the totals")
    if top_projects and total and top_projects[0][1] / total > 0.50:
        reasons.append(f"most work happening in {short_project_name(top_projects[0][0])}")
    if top_models and top_models[0][0] != "<synthetic>" and total and top_models[0][1] / total > 0.50:
        reasons.append(f"most requests using {top_models[0][0]}")
    if sub_total and total and sub_total / total > 0.25:
        reasons.append("subagent or parallel task usage")

    plain_section("Why Usage Is High")
    if reasons:
        for reason in reasons:
            plain_status("Driver", reason + ".", "bad" if "large cached context" in reason or "one very large session" in reason else "warn")
    else:
        plain_status("Driver", "Usage looks spread out; there is no single obvious driver.", "good")

    recommendations: list[str] = []
    if total and rr / total > 0.60:
        recommendations.append("Start a fresh session after a task is done, especially after large file reads or long debugging runs.")
    if top_sessions and total and top_sessions[0].reported_total / total > 0.20:
        recommendations.append("Break very long sessions into smaller task-focused chats.")
    if tool_counter.get("Read", 0) + tool_counter.get("Bash", 0) > max(20, len(requests) * 0.20):
        recommendations.append("Ask Claude to inspect only the files needed for the current change.")
    if sub_total and total and sub_total / total > 0.25:
        recommendations.append("Use subagents for broad searches, but avoid them for small direct edits.")
    if top_models and top_models[0][0].startswith("claude-opus"):
        recommendations.append("Use a cheaper/faster model for routine edits when quality requirements allow it.")

    plain_section("Recommended Fixes")
    if recommendations:
        for rec in recommendations[:5]:
            plain_status("Next step", rec, "action")
    else:
        plain_status("Next step", "Keep using shorter, task-specific sessions; no major waste pattern stands out.", "good")

    notes: list[str] = []
    if incomplete:
        notes.append(f"{fmt_int(incomplete)} requests were incomplete and may slightly undercount usage")
    if duplicate_records:
        notes.append(f"{fmt_int(duplicate_records)} streaming duplicate records were removed before counting")
    if stats and stats.get("lastComputedDate") and stats.get("lastComputedDate") < datetime.now().strftime("%Y-%m-%d"):
        notes.append(f"stats-cache.json is stale as of {stats.get('lastComputedDate')}, so JSONL transcripts were used")

    if notes:
        plain_section("Accounting Notes")
        for note in notes:
            plain_status("Note", note.replace("jsonl", "JSONL") + ".", "info")
    print()
