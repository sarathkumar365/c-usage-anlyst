from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from claude_usage.flows.collect import UsageQuery, collect_usage
from claude_usage.paths import ClaudePaths, resolve_claude_paths
from claude_usage.payload import build_sync_payload
from tests.fixture_home import build_fixture_home, fixture_env

REPO = Path(__file__).resolve().parents[1]


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "fixture"
        self.home = build_fixture_home(self.root)
        self.claude = ClaudePaths(self.home / ".claude", "default")

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(REPO / "claude_usage_analyzer.py"), "--no-color", *args],
            cwd=str(self.root),
            env=fixture_env(self.root, self.home),
            capture_output=True,
            text=True,
            timeout=120,
        )


class CollectTests(FixtureCase):
    def test_dedupes_streaming_records_and_keeps_finalized_snapshot(self):
        usage = collect_usage(self.claude, UsageQuery())
        by_id = {r.request_id: r for r in usage.requests}
        self.assertEqual(set(by_id), {"req_a", "req_b", "req_c", "req_sub"})
        self.assertEqual(by_id["req_a"].duplicate_records, 2)
        self.assertTrue(by_id["req_a"].finalized)
        self.assertEqual(by_id["req_a"].output_tokens, 340)
        self.assertFalse(by_id["req_sub"].finalized)
        self.assertTrue(by_id["req_sub"].is_subagent)

    def test_subagent_transcripts_are_separate_sessions(self):
        usage = collect_usage(self.claude, UsageQuery())
        subagents = [s for s in usage.sessions.values() if s.is_subagent]
        self.assertEqual([s.session_id for s in subagents], ["11111111-2222-3333-4444-555555555555:agent-abc123"])
        parent = usage.sessions["11111111-2222-3333-4444-555555555555"]
        self.assertFalse(parent.is_subagent)
        self.assertEqual(parent.requests, 2)

    def test_filters_are_applied(self):
        self.assertEqual({r.request_id for r in collect_usage(self.claude, UsageQuery(project="demo")).requests},
                         {"req_a", "req_b", "req_sub"})
        self.assertNotIn("req_sub", {r.request_id for r in collect_usage(self.claude, UsageQuery(main_only=True)).requests})
        self.assertEqual(collect_usage(self.claude, UsageQuery(surface="desktop")).requests, [])
        recent = collect_usage(self.claude, UsageQuery(days=1))
        self.assertEqual(recent.requests, [])
        self.assertIsNotNone(recent.period_start)


class PayloadTests(FixtureCase):
    def build(self, query: UsageQuery, period_end: datetime):
        usage = collect_usage(self.claude, query)
        return build_sync_payload(
            claude=self.claude,
            config={},
            identity={"collector_id": "c", "machine_id": "m", "user_id": "u"},
            requests=usage.requests,
            sessions=usage.sessions,
            stats=usage.stats,
            stats_cache_present=True,
            transcript_files=len(usage.jsonl_paths),
            transcript_digest="digest",
            period_start=period_end - timedelta(days=90),
            period_end=period_end,
            anomalies=[],
            sync_scope=query.scope(),
        )

    def test_idempotency_key_ignores_sync_window_but_not_filters(self):
        now = datetime.now(timezone.utc)
        first = self.build(UsageQuery(days=90), now)
        later = self.build(UsageQuery(days=90), now + timedelta(minutes=30))
        filtered = self.build(UsageQuery(days=7), now)
        self.assertEqual(first["idempotency_key"], later["idempotency_key"])
        self.assertNotEqual(first["idempotency_key"], filtered["idempotency_key"])

    def test_payload_has_no_raw_claude_paths(self):
        encoded = json.dumps(self.build(UsageQuery(), datetime.now(timezone.utc)))
        self.assertNotIn(str(self.claude.claude_dir), encoded)
        self.assertEqual(json.loads(encoded)["summary"]["requests"], 4)


class PathTests(unittest.TestCase):
    def test_precedence_is_argument_config_env_default(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/env"}):
            self.assertEqual(resolve_claude_paths("/arg", {"claude_config_dir": "/cfg"}).source, "argument")
            self.assertEqual(resolve_claude_paths(None, {"claude_config_dir": "/cfg"}).source, "collector_config")
            self.assertEqual(resolve_claude_paths(None, {}).claude_dir, Path("/env"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(resolve_claude_paths(None, None).source, "default")


class CliTests(FixtureCase):
    def test_sync_dry_run_end_to_end(self):
        proc = self.cli("--sync", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)["payload"]
        self.assertEqual(payload["summary"]["requests"], 4)
        self.assertTrue({"claude_code", "cowork", "desktop_chat"} <= {s["surface"] for s in payload["sources"]})
        self.assertTrue(payload["activity_daily"])
        self.assertNotIn(str(self.home), proc.stdout)
        self.assertTrue((self.root / "agent" / "state.json").exists())

    def test_sync_without_registration_records_error(self):
        proc = self.cli("--sync")
        self.assertEqual(proc.returncode, 2)
        state = json.loads((self.root / "agent" / "state.json").read_text())
        self.assertIn("not registered", state["last_error"])

    def test_enroll_without_secret_fails_cleanly(self):
        proc = self.cli("--enroll")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("ENROLLMENT_SECRET", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_register_writes_config_and_report_runs(self):
        proc = self.cli("--register", "--ingest-url", "https://example.invalid/i", "--collector-token", "tok")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        config = json.loads((self.root / "agent" / "config.json").read_text())
        self.assertEqual(config["claude_config_dir"], str(self.home / ".claude"))
        report = self.cli("--verbose")
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("TOTAL REPORTED TOKEN VOLUME", report.stdout)

    def test_stored_ids_survive_network_name_changes(self):
        self.cli("--register", "--ingest-url", "https://example.invalid/i", "--collector-token", "tok")
        config = json.loads((self.root / "agent" / "config.json").read_text())
        self.assertTrue(config["machine_id"] and config["user_id"])
        with mock.patch("socket.gethostname", return_value="Mac"), mock.patch("socket.getfqdn", return_value="38.2.168.192.in-addr.arpa"):
            from claude_usage.identity import collect_identity
            identity = collect_identity(config)
        self.assertEqual((identity["machine_id"], identity["user_id"]), (config["machine_id"], config["user_id"]))


if __name__ == "__main__":
    unittest.main()
