"""--sources: table of discovered Claude surfaces."""

from __future__ import annotations

from claude_usage.discovery import SourceRecord
from claude_usage.ui import c, fmt_int, print_table


def confidence_rank(value: str) -> int:
    return {"exact": 3, "derived": 2, "evidence": 1}.get(value, 0)

def print_sources(sources: list[SourceRecord]):
    print()
    print(c("=" * 80, "cyan"))
    print(c(" CLAUDE SURFACE DISCOVERY", "bold"))
    print(c("=" * 80, "cyan"))
    if not sources:
        print(c("No Claude-related local sources were discovered.", "yellow"))
        return
    rows = []
    for source in sorted(sources, key=lambda s: (s.surface, -confidence_rank(s.confidence), s.path)):
        rows.append([
            source.surface,
            source.confidence,
            source.status,
            fmt_int(source.file_count),
            source.latest_activity_at or "n/a",
            source.extractor,
            source.path,
        ])
    print_table(["SURFACE", "CONF", "STATUS", "FILES", "LATEST", "EXTRACTOR", "PATH"], rows, 100)
    print()
