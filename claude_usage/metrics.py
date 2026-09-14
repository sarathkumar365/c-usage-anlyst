from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from claude_usage.constants import MODEL_PRICES_USD_PER_M
from claude_usage.models import RequestUsage


def calculate_estimated_cost(r: RequestUsage) -> float:
    prices = MODEL_PRICES_USD_PER_M.get(r.model)
    if not prices:
        return 0.0
    in_price, out_price = prices
    # Conservative API-style estimate:
    # cache reads are charged differently than normal input; exact pricing varies.
    # We therefore include them at 10% of input price as a rough diagnostic, not a bill.
    normal_input = r.input_tokens + r.cache_creation_input_tokens
    cache_input = r.cache_read_input_tokens
    return (
        normal_input / 1_000_000 * in_price
        + cache_input / 1_000_000 * in_price * 0.10
        + r.output_tokens / 1_000_000 * out_price
    )

def group_sum(rows: Iterable[RequestUsage], key_fn, value_fn=lambda r: r.reported_total):
    out = defaultdict(int)
    for r in rows:
        out[key_fn(r)] += value_fn(r)
    return out

def token_breakdown(rows: Iterable[RequestUsage]) -> dict[str, int]:
    rows = list(rows)
    return {
        "input_tokens": sum(r.input_tokens for r in rows),
        "output_tokens": sum(r.output_tokens for r in rows),
        "cache_read_input_tokens": sum(r.cache_read_input_tokens for r in rows),
        "cache_creation_input_tokens": sum(r.cache_creation_input_tokens for r in rows),
        "cache_5m_tokens": sum(r.cache_5m_tokens for r in rows),
        "cache_1h_tokens": sum(r.cache_1h_tokens for r in rows),
        "reported_total": sum(r.reported_total for r in rows),
        "tool_calls": sum(r.tool_use_count for r in rows),
        "web_search_requests": sum(r.web_search_requests for r in rows),
        "web_fetch_requests": sum(r.web_fetch_requests for r in rows),
    }
