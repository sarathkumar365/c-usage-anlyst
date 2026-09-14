from __future__ import annotations

import json
import os
import shutil
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
from claude_usage.accounts import read_accounts, read_feature_usage
from claude_usage.anomalies import retention_anomalies
from claude_usage.desktop_sessions import desktop_session_activity, read_desktop_sessions
from claude_usage.flows import statusline as statusline_flow
from claude_usage.flows.sync import _advance_plan_usage_cursor
from claude_usage.paths import claude_config_dirs, desktop_data_dirs, statusline_samples_path
from claude_usage.plan_usage import read_desktop_samples, read_statusline_samples
from tests import fixture_home
from tests.fixture_home import build_fixture_home, fixture_env

REPO = Path(__file__).resolve().parents[1]


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "fixture"
        self.home = build_fixture_home(self.root)
        self.claude = ClaudePaths(self.home / ".claude", "default")
        # In-process flows resolve other Claude dirs from HOME, so point it at the fixture.
        self._env = mock.patch.dict(os.environ, fixture_env(self.root, self.home), clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
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
        self.assertEqual(set(by_id), {"req_a", "req_b", "req_c", "req_sub", "req_cowork"})
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
    def build(self, query: UsageQuery, period_end: datetime, **extra):
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
            **extra,
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
        self.assertEqual(json.loads(encoded)["summary"]["requests"], 5)


class TranscriptContextTests(FixtureCase):
    def test_sessions_carry_branch_entrypoint_and_version(self):
        session = collect_usage(self.claude, UsageQuery()).sessions[fixture_home.SESSION_ID]
        self.assertEqual(session.git_branch, "feature/login")
        self.assertEqual(session.entrypoints, ("cli",))
        self.assertEqual(session.claude_code_version, "2.1.210")

    def test_resumed_and_sidechain_copies_are_counted_once(self):
        usage = collect_usage(self.claude, UsageQuery())
        req_b = [r for r in usage.requests if r.request_id == "req_b"]
        self.assertEqual(len(req_b), 1)
        self.assertEqual(req_b[0].session_id, fixture_home.SESSION_ID)
        self.assertFalse(req_b[0].is_sidechain)

    def test_cowork_transcripts_are_exact_cowork_usage(self):
        usage = collect_usage(self.claude, UsageQuery())
        cowork = next(r for r in usage.requests if r.request_id == "req_cowork")
        self.assertEqual(cowork.surface, "cowork")
        self.assertTrue(cowork.project.startswith("cowork/"))
        self.assertEqual({r.request_id for r in collect_usage(self.claude, UsageQuery(surface="cowork")).requests}, {"req_cowork"})

    def test_extra_config_dirs_are_parsed_without_double_counting(self):
        extra = self.root / "extra-claude"
        shutil.copytree(self.home / ".claude", extra)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": f"{self.home / '.claude'},{extra}"}):
            self.assertEqual(len(claude_config_dirs(self.claude)), 2)
            self.assertEqual(len(collect_usage(self.claude, UsageQuery()).requests), 5)


class AccountTests(FixtureCase):
    def test_account_merges_sources_and_hides_email(self):
        accounts = read_accounts(claude_config_dirs(self.claude), desktop_data_dirs())
        self.assertEqual(len(accounts), 1)
        account = accounts[0]
        self.assertEqual((account.account_uuid, account.organization_uuid), (fixture_home.ACCOUNT_UUID, fixture_home.ORG_UUID))
        self.assertEqual(account.source, "claude_code,claude_desktop")
        self.assertEqual(account.organization_name, "Personal organization")
        self.assertEqual(len(account.email_hash), 64)
        self.assertNotIn("example.com", json.dumps(vars(account)).lower())

    def test_feature_usage_counts(self):
        self.assertEqual({(f.kind, f.name, f.count) for f in read_feature_usage(claude_config_dirs(self.claude))},
                         {("skill", "pdf", 3), ("plugin", "github", 2)})

    def test_desktop_sessions_join_without_titles(self):
        desktop = read_desktop_sessions(desktop_data_dirs())
        self.assertEqual({d.surface for d in desktop}, {"desktop_code", "cowork"})
        self.assertEqual({(a.surface, a.sessions) for a in desktop_session_activity(desktop)}, {("desktop_code", 1), ("cowork", 1)})
        payload = PayloadTests.build(self, UsageQuery(), datetime.now(timezone.utc), desktop_sessions=desktop)
        row = next(s for s in payload["sessions"] if s["session_id"] == fixture_home.SESSION_ID)
        self.assertEqual((row["desktop_surface"], row["desktop_effort"], row["completed_turns"]), ("desktop_code", "high", 4))
        self.assertNotIn(fixture_home.SECRET_TITLE, json.dumps(payload))

    def test_retention_warning(self):
        old = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
        self.assertEqual([a["code"] for a in retention_anomalies(claude_config_dirs(self.claude), old)], ["transcripts_may_expire"])
        self.assertEqual(retention_anomalies(claude_config_dirs(self.claude), datetime.now(timezone.utc).isoformat()), [])


class PlanUsageTests(FixtureCase):
    def test_desktop_samples_and_since(self):
        samples, unknown = read_desktop_samples(desktop_data_dirs())
        self.assertEqual(unknown, [])
        self.assertEqual([s.five_hour_pct for s in samples], [10.0, 25.0, 31.0])
        later, _ = read_desktop_samples(desktop_data_dirs(), since=datetime.fromisoformat(samples[0].sampled_at))
        self.assertEqual(len(later), 2)

    def test_statusline_samples_and_cursor_trim(self):
        path = statusline_samples_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        limits = {"five_hour": {"used_percentage": 12.5, "resets_at": 1_787_230_000}, "seven_day": {"used_percentage": 40}}
        path.write_text("\n".join([
            json.dumps({"t": 1_787_220_000, "input": {"rate_limits": limits}}),
            json.dumps({"t": 1_787_220_060, "input": {"model": {}}}),
            "garbage",
            json.dumps({"t": 1_787_220_120, "input": {"rate_limits": limits}}),
        ]) + "\n", encoding="utf-8")
        samples = read_statusline_samples(path, fixture_home.ORG_UUID)
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0].five_hour_resets_at[:10], "2026-08-20")
        _advance_plan_usage_cursor({"plan_usage": [vars(samples[0])]})
        self.assertEqual(len(read_statusline_samples(path, fixture_home.ORG_UUID)), 1)


class StatuslineTests(FixtureCase):
    def settings(self) -> dict:
        return json.loads((self.home / ".claude" / "settings.json").read_text())

    def test_install_chains_previous_statusline_and_uninstall_restores_it(self):
        previous = {"type": "command", "command": "echo mine", "padding": 1}
        (self.home / ".claude" / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 30, "statusLine": previous}))
        statusline_flow.install_statusline(self.claude)
        statusline_flow.install_statusline(self.claude)
        installed = self.settings()["statusLine"]
        self.assertIn("statusline", installed["command"])
        self.assertEqual(installed["padding"], 1)
        self.assertEqual(list(self.settings()), ["cleanupPeriodDays", "statusLine"])
        chain_files = list((self.root / "agent" / "statusline-chain").iterdir())
        self.assertEqual([f.read_text() for f in chain_files], ["echo mine"])
        self.assertTrue((self.home / ".claude" / "settings.json.claude-usage.bak").exists())
        statusline_flow.uninstall_statusline(self.claude)
        self.assertEqual(self.settings()["statusLine"], previous)

    def test_invalid_settings_are_left_alone(self):
        (self.home / ".claude" / "settings.json").write_text("{not json")
        self.assertEqual(statusline_flow.install_statusline(self.claude), 0)
        self.assertEqual((self.home / ".claude" / "settings.json").read_text(), "{not json")


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
        self.assertEqual(payload["summary"]["requests"], 5)
        self.assertTrue({"claude_code", "cowork", "desktop_code", "desktop_chat"} <= {s["surface"] for s in payload["sources"]})
        self.assertEqual(len(payload["plan_usage"]), 3)
        self.assertEqual(payload["accounts"][0]["organization_uuid"], fixture_home.ORG_UUID)
        self.assertNotIn(fixture_home.EMAIL.lower(), proc.stdout.lower())
        self.assertNotIn(fixture_home.SECRET_TITLE, proc.stdout)
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
