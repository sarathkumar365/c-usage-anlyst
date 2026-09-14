from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_usage.discovery import (
    cache_discovery,
    discover_claude_sources,
    discovery_needs_full,
)
import claude_usage_analyzer as analyzer


class DiscoveryTests(unittest.TestCase):
    def test_macos_discovers_claude_surfaces_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude_dir = home / ".claude"
            (claude_dir / "projects" / "-Users-test-project").mkdir(parents=True)
            (claude_dir / "projects" / "-Users-test-project" / "session.jsonl").write_text("{}", encoding="utf-8")
            app_support = home / "Library" / "Application Support" / "Claude"
            (app_support / "claude-code-sessions" / "workspace" / "session").mkdir(parents=True)
            (app_support / "claude-code-sessions" / "workspace" / "session" / "local_1.json").write_text(
                json.dumps({"completedTurns": 3, "enabledMcpTools": {"tool": True}}),
                encoding="utf-8",
            )
            (app_support / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb").mkdir(parents=True)
            (home / "Library" / "Preferences").mkdir(parents=True)
            (home / "Library" / "Preferences" / "com.anthropic.claudefordesktop.plist").write_text(
                "plist",
                encoding="utf-8",
            )

            sources, _ = discover_claude_sources(claude_dir, system="Darwin", home=home, full=True)
            surfaces = {source.surface for source in sources}
            confidences = {source.confidence for source in sources}

            self.assertIn("claude_code", surfaces)
            self.assertIn("cowork", surfaces)
            self.assertIn("desktop_chat", surfaces)
            self.assertIn("desktop_app", surfaces)
            self.assertIn("exact", confidences)
            self.assertIn("derived", confidences)
            self.assertIn("evidence", confidences)

    def test_windows_discovers_appdata_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            appdata = home / "AppData" / "Roaming"
            localappdata = home / "AppData" / "Local"
            old_appdata = os.environ.get("APPDATA")
            old_localappdata = os.environ.get("LOCALAPPDATA")
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            try:
                (appdata / "Claude" / "Session Storage").mkdir(parents=True)
                (localappdata / "AnthropicClaude" / "logs").mkdir(parents=True)
                sources, _ = discover_claude_sources(home / ".claude", system="Windows", home=home, full=True)
            finally:
                if old_appdata is None:
                    os.environ.pop("APPDATA", None)
                else:
                    os.environ["APPDATA"] = old_appdata
                if old_localappdata is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_localappdata

            self.assertTrue(any(source.surface == "desktop_chat" for source in sources))
            self.assertTrue(any(source.surface == "desktop_app" for source in sources))

    def test_linux_discovers_config_and_cache_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".config" / "Claude" / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb").mkdir(parents=True)
            (home / ".cache" / "com.anthropic.claude" / "Cache").mkdir(parents=True)
            sources, _ = discover_claude_sources(home / ".claude", system="Linux", home=home, full=True)

            self.assertTrue(any(source.surface == "desktop_chat" for source in sources))
            self.assertTrue(any(source.surface == "desktop_app" for source in sources))

    def test_light_discovery_uses_cached_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude_dir = home / ".claude"
            target = home / "Library" / "Application Support" / "Claude"
            target.mkdir(parents=True)
            full_sources, cache = discover_claude_sources(claude_dir, system="Darwin", home=home, full=True)
            cached_sources = [source.to_cache() for source in full_sources]
            light_sources, _ = discover_claude_sources(
                claude_dir,
                system="Darwin",
                home=home,
                full=False,
                cached_sources=cached_sources,
            )

            self.assertEqual([s.path for s in full_sources], [s.path for s in light_sources])
            self.assertEqual(cache["source_count"] if "source_count" in cache else len(full_sources), len(light_sources))

    def test_rediscovery_full_triggers_on_stale_or_version_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            now = datetime.now(timezone.utc)
            cache = {
                "discovered_at": (now - timedelta(hours=25)).isoformat(),
                "collector_version": "1",
                "claude_config_dir": str(claude_dir),
                "roots": [],
            }
            state = {"discovery": cache}
            self.assertTrue(discovery_needs_full(state, claude_dir=claude_dir, collector_version="1", now=now))
            fresh_state = cache_discovery({}, {**cache, "discovered_at": now.isoformat()}, claude_dir=claude_dir, collector_version="1")
            self.assertFalse(discovery_needs_full(fresh_state, claude_dir=claude_dir, collector_version="1", now=now))
            self.assertTrue(discovery_needs_full(fresh_state, claude_dir=claude_dir, collector_version="2", now=now))

    def test_sync_payload_does_not_include_raw_source_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            projects_dir = claude_dir / "projects"
            projects_dir.mkdir(parents=True)
            old_dir = analyzer.CLAUDE_DIR
            try:
                analyzer.configure_claude_dir(str(claude_dir))
                sources, _ = discover_claude_sources(claude_dir, system="Darwin", home=Path(tmp), full=True)
                payload = analyzer.build_sync_payload(
                    [],
                    {},
                    None,
                    [],
                    None,
                    None,
                    sources=sources,
                    activity_daily=[],
                )
            finally:
                analyzer.configure_claude_dir(str(old_dir))

            encoded = json.dumps(payload)
            self.assertNotIn(str(claude_dir), encoded)
            self.assertNotIn(str(projects_dir), encoded)
            self.assertIn("config_dir_hash", payload["claude"])
            self.assertIn("projects_dir_hash", payload["claude"])

    def test_idempotency_key_ignores_sync_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            (claude_dir / "projects").mkdir(parents=True)
            old_dir = analyzer.CLAUDE_DIR
            try:
                analyzer.configure_claude_dir(str(claude_dir))
                now = datetime.now(timezone.utc)
                first = analyzer.build_sync_payload([], {}, None, [], now - timedelta(days=90), now)
                later = now + timedelta(minutes=30)
                second = analyzer.build_sync_payload([], {}, None, [], later - timedelta(days=90), later)
            finally:
                analyzer.configure_claude_dir(str(old_dir))

            self.assertEqual(first["idempotency_key"], second["idempotency_key"])


if __name__ == "__main__":
    unittest.main()
