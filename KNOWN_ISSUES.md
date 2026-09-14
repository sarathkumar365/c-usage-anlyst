# Known Issues

Findings from a code review on 2026-09-14 (working tree based on `b31a8ea` plus uncommitted changes). Ranked by severity.

Status values: `open`, `in-progress`, `fixed`, `deferred`, `wontfix`, `unverified`.

`deferred` = accepted while in development; revisit before onboarding real users. KI-07/08/15 skew dashboard totals upward — check them first if numbers look high.

| ID | Severity | Area | Title | Status |
|----|----------|------|-------|--------|
| KI-01 | Critical | Security | Unauthenticated enrollment + ingest trusts payload identity | fixed |
| KI-02 | High | Security | Enrollment can revoke another collector's token | deferred |
| KI-03 | High | Security | `create_collector_token` likely callable by anon | fixed |
| KI-04 | High | Data integrity | Idempotency key never repeats; `collector_runs` grows unbounded | fixed |
| KI-05 | High | Data integrity | Partial ingest failure permanently drops data | fixed |
| KI-06 | High | Data integrity | Hostname change forks machine/user IDs and double-counts | fixed |
| KI-07 | Medium | Data integrity | Activity rows accumulate across days | deferred |
| KI-08 | Medium | Discovery | False-positive sources from generic metadata names | deferred |
| KI-09 | High | Release | Untracked `claude_usage/` package breaks builds and Python fallback | fixed |
| KI-10 | Low | Collector | Preflight "Last sync" always shows "never" | fixed |
| KI-11 | Low | Database | `schema.sql` not re-runnable | fixed |
| KI-12 | Medium | Dashboard | Silent row truncation from PostgREST `max_rows` | fixed |
| KI-13 | Low | Ingest | Malformed JSON returns unhandled 500 | fixed |
| KI-14 | Low | Repo hygiene | Committed plist contains a developer-local path | fixed |
| KI-16 | Medium | Data integrity | Subagent transcripts merged into parent sessions | fixed |
| KI-15 | Unknown | Data integrity | Cross-file request duplication may double-count | unverified |

---

## KI-01 — Unauthenticated enrollment + ingest trusts payload identity

- **Files:** `supabase/functions/enroll/index.ts`, `supabase/functions/ingest/index.ts`
- **Problem:** `enroll` issues a valid collector token for `team-main` to any caller (only limited to 25/IP/day). `ingest` validates the token but never checks that `payload.identity.collector_id / machine_id / user_id` match the `collector_tokens` row.
- **Impact:** Anyone on the internet can obtain a token and upsert arbitrary usage rows, including overwriting another user's rows by spoofing their IDs (upsert keys include those IDs).
- **Fix direction:** Gate enrollment (org enrollment secret, invite code, or signed install link). In ingest, load `collector_id, machine_id, user_id` from the token row and reject or override mismatching payload identity.
- **Resolution (2026-09-14):** `enroll` now requires an `X-Enrollment-Secret` header whose SHA-256 matches an unrevoked row in `enrollment_secrets` (table-backed rather than a function env secret, so it can be set via SQL and rotated without redeploying). `ingest` rejects payloads whose `collector_id` differs from the token's. Only `collector_id` is bound, not `machine_id`/`user_id`, because those change with hostname (KI-06). Remaining gap: a token holder can still upsert `machines`/`machine_users` label rows for other machine IDs.

## KI-02 — Enrollment can revoke another collector's token

- **File:** `supabase/functions/enroll/index.ts` (revoke-by-`collector_id` update before insert)
- **Problem:** Enrolling with an existing `collector_id` revokes all active tokens for it. `collector_id` is visible to dashboard members.
- **Impact:** Denial of service against a specific user's collector.
- **Fix direction:** Only allow re-enrollment when authenticated with the current token (or an admin action); otherwise generate a new collector ID server-side.

## KI-03 — `create_collector_token` likely callable by anon

- **File:** `supabase/schema.sql` (`create_collector_token`)
- **Problem:** `security definer` function in `public` with no `revoke execute ... from public, anon, authenticated`. Postgres grants EXECUTE to PUBLIC by default, so it is reachable via PostgREST RPC. `on conflict` also clears `revoked_at`.
- **Impact:** Token minting and un-revoking by unauthenticated callers.
- **Fix direction:** `revoke execute on function create_collector_token(text, text, text) from public, anon, authenticated;` and grant only to `service_role`. Do the same review for `is_org_member` / `is_org_admin` (lower risk).
- **Verify:** Confirmed on the live DB 2026-09-14: `has_function_privilege('anon', ...)` returned `true`.
- **Resolution (2026-09-14):** `schema.sql` revokes EXECUTE from `public, anon, authenticated` and grants to `service_role`. Must be run against the live DB.

## KI-04 — Idempotency key never repeats; `collector_runs` grows unbounded

- **File:** `claude_usage_analyzer.py` `build_sync_payload` (idempotency key), `main` (`end = now + 1s`)
- **Problem:** `period_start` and `period_end` are derived from `now()` and are part of the idempotency key, so every run produces a new key. Dedup in ingest never fires.
- **Impact:** A new `collector_runs` row containing the full 90-day payload (JSONB) every 30 minutes per collector. Storage bloat; dedup is effectively disabled.
- **Fix direction:** Key on content digests only (transcript/source/activity digests + identity), or truncate the period to a stable boundary. Consider not storing the full `payload` or applying retention.
- **Resolution (2026-09-14):** Period bounds removed from the key (test: `test_idempotency_key_ignores_sync_window`). `collector_runs.payload` no longer stores `daily`/`sessions`/`sources`/`activity_daily`. A new row is still written whenever transcripts or desktop sources change, so active users still add rows; add retention later if needed.

## KI-05 — Partial ingest failure permanently drops data

- **File:** `supabase/functions/ingest/index.ts`
- **Problem:** `collector_runs` is inserted (errors ignored) before the usage upserts. If a later upsert fails, the collector's retry with the same key is treated as a duplicate. `machines` / `machine_users` / `collector_runs` errors are also ignored.
- **Impact:** Silent data loss for that snapshot (masked today only because KI-04 makes keys unique).
- **Fix direction:** Do all writes in one transaction (Postgres RPC function) or insert `collector_runs` last; check every error.
- **Resolution (2026-09-14):** Had to be fixed alongside KI-04, since stable keys would have exposed it. `collector_runs` is now inserted last and every write's error is checked. Not transactional: a failure mid-way leaves partial upserts, but the retry rewrites them.

## KI-06 — Hostname change forks machine/user IDs and double-counts

- **File:** `claude_usage_analyzer.py` `collect_identity`
- **Problem:** `machine_id` hashes `hostname`, `fqdn`, `node`, etc.; `user_id` derives from `machine_id`. macOS hostnames often change across networks.
- **Impact:** A new identity re-uploads the same 90 days of usage. Dashboard sums across identities double-count; attribution splits.
- **Fix direction:** Generate and persist a stable random `machine_id` / `user_id` in `config.json` at enrollment; use the fingerprint only as a secondary hint.
- **Seen live (2026-09-14):** on macOS `fqdn` is the reverse DNS of the DHCP address (`38.2.168.192.in-addr.arpa`), so cdhameja's Mac produced 7 machine IDs and gurpr's Windows PC a new one each day. cdhameja's usage for 2026-09-14 was stored 5 times (172.6M summed vs 40.0M real).
- **Fixed (v0.6.1):**
  - `ingest` replaces the payload's `machine_id` / `user_id` with the ones recorded on the enrolled token, which fixes existing installs without a reinstall.
  - The collector stores both IDs in `config.json` at enroll/register (or after the first successful sync for older configs) and reuses them. This covers legacy tokens that have no recorded IDs.
  - Live data was merged onto the token IDs, keeping the newest copy of each overlapping row. Pre-merge copies are in schema `backup_20260914`; drop it once the dashboard looks right.

## KI-07 — Activity rows accumulate across days

- **File:** `claude_usage_analyzer.py` `extract_activity_daily`
- **Problem:** Each source's entire cumulative activity is assigned to the day of its latest mtime. When the mtime advances, a new day row is upserted and the old row remains.
- **Impact:** `dashboard_activity_daily` totals inflate over time. Also: `messages` is actually a file count, and Cowork `tool_calls` is the count of *enabled* MCP tools, not calls.
- **Fix direction:** Bucket by per-file mtime (or per-session timestamps), or send snapshot semantics and replace rows per source. Rename or re-derive misleading metrics.

## KI-08 — False-positive sources from generic metadata names

- **File:** `claude_usage/discovery.py` `safe_metadata_file_matches`, `SAFE_METADATA_NAMES`
- **Problem:** `settings.json` and `preferences` match anywhere under the scanned roots (depth 5) without requiring a Claude-named ancestor.
- **Impact:** Unrelated apps (e.g. VS Code `settings.json`) are reported as `desktop_app` sources; inflates surface counts and activity.
- **Fix direction:** Require a Claude/Anthropic-named ancestor for generic names.

## KI-09 — Untracked `claude_usage/` package breaks builds and Python fallback

- **Files:** `claude_usage/` (untracked), `install/install.sh`, `install/install.ps1`, `.github/workflows/release.yml`
- **Problem:** The modified analyzer imports `claude_usage.discovery`. The package is not committed, so a release built from the repo produces a binary that fails on import. Separately, the installers' Python fallback downloads only `claude_usage_analyzer.py`, which fails on import even after the package is committed.
- **Fix direction:** Commit `claude_usage/` and `tests/` together with the analyzer changes. Make the fallback download the package (tarball/zip of the tag) or inline the module.
- **Progress (2026-09-14):** Both installers' Python fallback now downloads `claude_usage/__init__.py` and `discovery.py`. Committed with the analyzer changes and released as `v0.5.0`.

## KI-10 — Preflight "Last sync" always shows "never"

- **File:** `claude_usage_analyzer.py` `run_preflight` (reads `last_sync_at`)
- **Problem:** State stores `last_success_at`.
- **Fix direction:** Read `last_success_at`.
- **Resolution (2026-09-14):** Fixed.

## KI-11 — `schema.sql` not re-runnable

- **File:** `supabase/schema.sql`
- **Problem:** Early `create policy` statements lack `drop policy if exists`; re-applying the file (e.g. to pick up new tables/views) errors on the first existing policy.
- **Fix direction:** Add `drop policy if exists` before each, or move to versioned migrations.
- **Resolution (2026-09-14):** Every policy is preceded by `drop policy if exists`; `schema.sql` was applied twice in a row to a fresh Postgres 17 without errors. Incremental changes now live in `supabase/migrations/`.

## KI-12 — Silent row truncation from PostgREST `max_rows`

- **File:** `dashboard/index.html` `fetchView`
- **Problem:** Queries request `limit: 10000`, but Supabase's default API `max_rows` is 1000. No pagination or truncation warning.
- **Impact:** Totals and charts silently undercount once views exceed 1000 rows.
- **Fix direction:** Paginate with `.range()`, aggregate server-side, or detect `count` vs returned length.
- **Resolution (2026-09-14):** The rebuilt dashboard pages every view with `.range()` and a stable ordering.

## KI-13 — Malformed JSON returns unhandled 500

- **File:** `supabase/functions/ingest/index.ts` (`await req.json()`)
- **Fix direction:** Wrap in try/catch and return 400, matching `enroll`.
- **Resolution (2026-09-14):** Fixed in ingest.

## KI-14 — Committed plist contains a developer-local path

- **File:** `install/com.internal.claude-usage-agent.plist`
- **Problem:** Hardcodes `/Users/sarathkumar/...`; the installer generates its own plist, so this file is unused and misleading.
- **Fix direction:** Delete, or convert to a template with placeholders.
- **Resolution (2026-09-14):** Deleted; `install.sh` generates the plist.

## KI-16 — Subagent transcripts merged into parent sessions

- **File:** `claude_usage/transcripts.py` `parse_all`
- **Problem:** Subagent transcripts live under the parent session's folder, so `session_id_from_path` returned the parent's id and their requests were bucketed into the parent session; `is_subagent` came from whichever request sorted first. The live database had zero subagent sessions.
- **Impact:** Subagent work was invisible and parent sessions were inflated, so "why usage is high" could never cite subagents.
- **Resolution (2026-09-14):** Subagent sessions are keyed `<parent>:<agent-id>` (test: `test_subagent_transcripts_are_separate_sessions`). Existing merged parent rows correct themselves on the next sync; subagent rows appear alongside.

## KI-15 — Cross-file request duplication may double-count (unverified)

- **File:** `claude_usage_analyzer.py` `parse_all`
- **Problem:** Request IDs are deduplicated only within a single JSONL file. If resumed/forked sessions copy prior assistant messages into a new file, those tokens are counted twice.
- **Verify:** Check for repeated `message.id` across files in a real `~/.claude/projects` tree. If present, dedup globally by request ID.

---

## Deployment log for the 2026-09-14 fixes

All steps below were completed on 2026-09-14. Live checks afterwards: enroll returns 401 without a secret and 403 with a wrong one; ingest returns 403 for a bad token; a real sync uploaded sources and activity; posting the same payload twice returned `duplicate: true`; anon can no longer execute `create_collector_token` or read `enrollment_secrets`.

The live DB was checked on 2026-09-14. Besides KI-03 being confirmed, the uncommitted schema changes had never been applied there (`usage_sources`, `usage_activity_daily` and their views were missing). The new ingest would return 500 until they are.

Order matters:

1. Apply `supabase/migrations/20260914000000_surfaces_activity_and_enrollment_hardening.sql`. It adds the missing tables and views, recreates the three views whose columns changed, revokes the RPC grant, and creates `enrollment_secrets`.
2. Insert the enrollment secret hash. A generated secret and a hash-only insert are stored outside the repo at `~/.config/c-usage-anlyst/` (`enrollment_secret`, `insert_enrollment_secret.sql`).
3. Deploy the `enroll` and `ingest` functions, keeping `verify_jwt = false` as they are now.
4. Merge the branch to `main`, then tag a release (e.g. `v0.5.0`) so the agent binaries include `claude_usage/` and send the secret header.
5. Existing installs keep syncing with their current tokens. Only new enrollments need the secret.

## Structure refactor (2026-09-14, v0.6.0)

The 2,335-line `claude_usage_analyzer.py` was split into one module per flow (see README "How It Is Organized"). This makes the remaining deferred issues single-module changes:

- KI-06 (identity) -> `claude_usage/identity.py`
- KI-07 (activity rows) -> `claude_usage/activity.py`
- KI-08 (discovery false positives) -> `claude_usage/discovery.py`
- KI-12 (dashboard row cap) -> `dashboard/index.html` `fetchView`
- KI-15 (cross-file duplicates) -> `claude_usage/transcripts.py` `parse_all`

Behavior-preserving intent was verified by byte-identical CLI output (19 scenarios, Python source and PyInstaller binary) against the pre-refactor monolith, and row-identical dashboard views on the live database.

## Not reviewed

- `analyze` and `plain_usage_story` report rendering in `claude_usage_analyzer.py`
- Dashboard aggregation/rendering logic beyond data fetching and escaping (HTML output uses `esc()`)
