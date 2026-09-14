"""Terminal formatting shared by every command's output."""

from __future__ import annotations

from typing import Any

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
}


def disable_color():
    for key in ANSI:
        ANSI[key] = ""


def c(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return f"{ANSI[color]}{text}{ANSI['reset']}"

def fmt_int(n: int | float) -> str:
    try:
        n = int(round(n))
    except Exception:
        return "0"
    return f"{n:,}"

def fmt_compact(n: int | float) -> str:
    n = float(n)
    if abs(n) >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(int(n))

def fmt_money(n: float) -> str:
    if n <= 0:
        return "$0"
    if n >= 100:
        return f"${n:,.0f}"
    if n >= 10:
        return f"${n:,.1f}"
    return f"${n:,.2f}"

def fmt_pct(value: float) -> str:
    if 0 < value < 0.1:
        return "<0.1%"
    return f"{value:.1f}%"

def total_pct(a: int, b: int) -> float:
    return 0.0 if not b else a / b * 100.0

def fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "n/a"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m and len(parts) < 2:
        parts.append(f"{m}m")
    if not parts:
        parts.append(f"{s}s")
    return " ".join(parts)

def short_project_name(project: str) -> str:
    parts = [p for p in project.rstrip("/").split("/") if p]
    if not parts:
        return project or "unknown project"
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1]

def sentence_join(items: list[str]) -> str:
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"

def friendly_tool_name(name: str) -> str:
    if name.startswith("mcp__Claude_Browser__"):
        browser_tool = name[len("mcp__Claude_Browser__"):].replace("_", " ")
        browser_tool = browser_tool.replace("javascript", "JavaScript")
        return "Browser " + browser_tool
    if name.startswith("mcp__"):
        return "MCP " + name[len("mcp__"):].replace("__", " ").replace("_", " ")
    return name

def plain_section(title: str):
    print()
    print(c(title.upper(), "bold"))
    print(c("-" * min(80, max(36, len(title) + 12)), "cyan"))

def plain_status(label: str, message: str, severity: str = "info"):
    styles = {
        "good": ("GOOD", "green"),
        "ok": ("OK", "green"),
        "info": ("INFO", "cyan"),
        "warn": ("NEEDS WORK", "yellow"),
        "bad": ("HIGH", "red"),
        "action": ("FIX", "yellow"),
    }
    tag, color = styles.get(severity, styles["info"])
    print(f"{c(tag.ljust(10), color)} {c(label + ':', 'bold')} {message}")

def plain_metric(label: str, value: str, note: str = "", severity: str = "info"):
    color = {
        "good": "green",
        "ok": "green",
        "info": "cyan",
        "warn": "yellow",
        "bad": "red",
    }.get(severity, "cyan")
    line = f"  {label.ljust(18)} {c(value, color)}"
    if note:
        line += f"  {c(note, 'dim')}"
    print(line)

def print_table(headers: list[str], rows: list[list[Any]], max_rows: int = 20):
    if not rows:
        print("(none)")
        return
    rows = rows[:max_rows]
    converted = [[str(x) for x in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in converted:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = min(max(widths[i], len(cell)), 42)

    def fit(s: str, w: int) -> str:
        if len(s) <= w:
            return s
        return s[: max(1, w - 1)] + "…"

    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in converted:
        print("  ".join(fit(row[i], widths[i]).ljust(widths[i]) for i in range(len(headers))))
