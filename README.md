# Claude Usage Analyzer

Cross-platform Claude usage and activity analyzer with a metrics-only team collector.

It reports exact token usage from Claude Code transcripts and derived/evidence activity from local Claude Desktop, Cowork, and extension metadata.

## Why This Exists

Small companies often share one Claude subscription (for example a single Max plan) across the whole team, with members working from different machines and countries. Claude's own usage screen only shows how much of that one account has been used in total. It cannot say who used it, how much each person used, what they were working on, or which Claude product they used.

This project answers exactly those questions for a shared account:

- **Who** is using it: each person and machine, identified independently of the shared Claude login.
- **How much** each person uses: exact tokens where the machine records them, activity everywhere else.
- **What** they are working on: projects, models, tools, and sessions.
- **Where** the usage comes from: Claude Code, Claude Desktop, Cowork, extensions.

Every design choice should be judged against these goals. The collector sends metrics only, never prompt or response content.

### What each machine can and cannot see

| Signal | Source | Exact? |
|---|---|---|
| Tokens per request, session, project, model, branch | Claude Code transcripts (terminal, IDE, Desktop Code tab, Agent SDK, Cowork) in every Claude config dir | Exact |
| Which Claude account the machine is signed in to | `~/.claude.json`, Claude Desktop session folders | Exact (email only as a hash) |
| The account's 5-hour and 7-day usage % | Claude Desktop `plan-usage-history.json`, and Claude Code's statusline input captured by the collector | Account-wide, covers claude.ai web too |
| Desktop Code tab and Cowork sessions (model, effort, turns) | Claude Desktop session records | Exact metadata, no titles |
| Claude Desktop chat, IDE and Chrome extensions | Local files and folders | Activity or install evidence only |

claude.ai web and Desktop chat conversations live on Anthropic's servers, and Pro/Max accounts have no usage API, so their per-person tokens cannot be measured. The dashboard instead compares rises in the account's usage % with activity from tracked machines and shows the untracked share as an estimate.

Not collected on purpose: prompts and responses, session titles, browser history or cookies, Claude credentials (so no calls to Anthropic's private usage endpoints), and other OS users on the same machine (install once per user).

## How It Is Organized

Every piece of work belongs to one flow. Each flow is one module that receives already-resolved inputs and owns its own side effects.

| Flow | Agent code | Server code | Writes |
|---|---|---|---|
| Discover | `claude_usage/flows/discover.py` → `discovery.py` | – | `state.json` (`discovery`) |
| Collect | `claude_usage/flows/collect.py` → `transcripts.py` | – | nothing |
| Enroll | `claude_usage/flows/enroll.py` | `supabase/functions/enroll` | `config.json`, `collector_tokens`, `enrollment_attempts` |
| Sync | `claude_usage/flows/sync.py` → `accounts.py`, `plan_usage.py`, `desktop_sessions.py`, `activity.py`, `anomalies.py`, `payload.py` | `supabase/functions/ingest` | `state.json`, usage, account and plan usage tables |
| Statusline capture | `claude_usage/flows/statusline.py` | – | Claude `settings.json` (`statusLine`), agent `config.json`, `plan-samples.jsonl` |
| Preflight / status | `claude_usage/flows/preflight.py`, `status.py` | – | `state.json` (via discover) |
| Reports | `claude_usage/reports/` (plain, verbose, sources, export) | – | export files only |
| Dashboard | `dashboard/index.html` | `dashboard_*` views in `supabase/schema.sql` | `identity_aliases` (admins) |
| Install | `install/install.sh`, `install/install.ps1` | – | agent dir, OS scheduler |

Shared building blocks: `cli.py` (argument parsing and dispatch only), `paths.py` (the single Claude/agent directory resolver), `store.py` (the only reader/writer of `config.json`/`state.json`), `identity.py`, `transport.py` (HTTP), `models.py`, `metrics.py`, `ui.py`, `util.py`, `constants.py`. `claude_usage_analyzer.py` is only the entry point.

A sync runs: collect transcripts from every Claude dir → discover sources → read accounts, plan usage and Desktop sessions → extract activity → build the payload (pure, no I/O) → upload → record the outcome and plan usage cursor in `state.json`. Ingest runs: authenticate token → validate payload → skip duplicates → write usage rows → record the run.

Database changes go in `supabase/migrations/` and are folded into `supabase/schema.sql`, which stays safe to re-run.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

`tests/fixture_home.py` builds a deterministic fake home (Claude Code transcripts plus Desktop/Cowork data) used by the flow and CLI tests.

## Local Report

```bash
python3 claude_usage_analyzer.py
```

The default output is a colored, plain-English terminal report. Use the old detailed tables with:

```bash
python3 claude_usage_analyzer.py --verbose
```

Inspect discovered Claude surfaces with:

```bash
python3 claude_usage_analyzer.py --sources
```

## Collector Setup

Create a Supabase project, apply `supabase/schema.sql`, and deploy `supabase/functions/enroll` and `supabase/functions/ingest` (both with JWT verification off; they authenticate with the enrollment secret and collector tokens).

Current configured Supabase project:

```text
Project : c-usage-anlyst
Ref     : yeokmzmmldqjngwtrfso
URL     : https://yeokmzmmldqjngwtrfso.supabase.co
Org ID  : team-main
Ingest  : https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest
Dashboard: https://claude-usage-dashboard.netlify.app
```

First setup:

```sql
insert into organizations(id, name)
values ('team-main', 'Team Main')
on conflict (id) do nothing;
```

Dashboard sign-in is invite-only: turn off "Allow new users to sign up" in Supabase Auth, invite each person from the Supabase Auth dashboard, then add them to the organization:

```sql
insert into org_members(org_id, user_id, role, can_view_history)
values ('team-main', '00000000-0000-0000-0000-000000000000', 'admin', true);
```

## Install

Enrollment requires a shared secret. Only its SHA-256 hash is stored, in the `enrollment_secrets` table (RLS on, no client access). Use at least 32 random bytes:

```sql
insert into enrollment_secrets(secret_hash, org_id, label)
values (encode(extensions.digest('<secret>', 'sha256'), 'hex'), 'team-main', 'install secret');
```

Give the secret to installers out of band. The installer asks for it with hidden input, so it never lands in shell history or the process list, and it is not stored on the machine. Rotate by inserting a new row and setting `revoked_at` on the old one. Enrollment allows 10 failed attempts per IP per hour.

macOS/Linux:

```bash
curl --proto '=https' -fsSL https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/v0.8.0/install/install.sh -o /tmp/claude-usage-install.sh && sh /tmp/claude-usage-install.sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/v0.8.0/install/install.ps1 | iex
```

The installer:

1. Checks the system, then downloads the agent binary for the pinned release and verifies it against that release's `SHA256SUMS` before running it. It stops if the checksum does not match. Running the tag's Python source instead is opt-in with `ALLOW_SOURCE_FALLBACK=1`.
2. Enrolls the machine. Re-running it re-enrolls with the machine's current token; a collector ID that is already enrolled cannot be claimed without that token.
3. Installs the usage % statusline capture (keeps any statusline the member already has and backs up `settings.json`; `SKIP_STATUSLINE=1` skips it, `--uninstall-statusline` removes it).
4. Syncs all available history once: transcripts, plus older days from Claude Code's stats cache.
5. Schedules a sync every 30 minutes that resends the last `SYNC_DAYS` (default 30) days.

Agent files (`config.json` with the collector token, state, logs) are readable only by the installing user.

Release binaries are built by GitHub Actions when a version tag matching `APP_VERSION` is pushed. The release gets `SHA256SUMS`, and a tag cannot overwrite binaries that were already published. Bump `AGENT_VERSION` in both installers and the install URLs above with each release.

## Manual Collector Commands

```bash
python3 claude_usage_analyzer.py --status
python3 claude_usage_analyzer.py --enroll          # reads ENROLLMENT_SECRET from the environment
python3 claude_usage_analyzer.py --sync            # all history
python3 claude_usage_analyzer.py --sync --days 30
python3 claude_usage_analyzer.py --sync --dry-run --days 30
python3 claude_usage_analyzer.py --sync --surface desktop --days 30
python3 claude_usage_analyzer.py --install-statusline
python3 claude_usage_analyzer.py --uninstall-statusline
```

## Dashboard

Host `dashboard/index.html` on any static host. On Netlify, `netlify.toml` adds a Content Security Policy and other security headers; supabase-js is pinned with Subresource Integrity.

The dashboard opens on three views of the same question, who is using the most and why:

- **Brief** — a written summary: the top user as a headline, the ranking, and short stories for the next people.
- **Share** — a proportional field of people, projects, or models.
- **Work lanes** — the last 7 days of sessions per person (length is time, height is tokens).

The Brief also shows the shared account: its latest 5-hour and 7-day usage %, a sparkline for the range, and the estimated share of usage that happened while no tracked machine was active. Collectors show which Claude account each machine is signed in to and flag machines on a different account.

Data pages (People, Projects & models, Sessions, Collectors) and a person panel sit alongside. "Why" reasons are derived in the page from session length, subagent share, context reuse, model mix, and top tools (`dashboard_person_tools`). Collectors come from each identity's latest sync (`dashboard_collectors`), so a machine that syncs but finds no Claude data still appears. All views are paged, so none is capped at PostgREST's row limit.

### Who sees what

- Every organization member sees the last 30 days (7d and 30d ranges).
- Members with the history permission (`org_members.can_view_history`) also see older data (90d and All). This is enforced by row level security on every collected-data table, not just hidden in the page.
- Admins (`role` admin or owner) manage the history permission in Collectors → Dashboard access.
- Sign-in uses a magic link with PKCE, so open the link in the same browser that requested it.

## Privacy Defaults## Privacy Defaults

The collector uploads metrics only:

- token counts
- model/project/session aggregates
- source confidence levels
- Claude surface activity counts
- machine/user identifiers
- tool names and counts
- anomaly flags
- Claude account and organization IDs, plan tier, and a hash of the login email
- the account's usage percentages
- git branch names, Claude Code version and install method, skill and plugin usage counts

Project paths, which contain the OS username, are sent only as a salted hash plus their last two folder names. The machine's network name (FQDN) is not sent, and home-path and email hashes are salted per organization.

It does not upload prompts, responses, raw transcript text, session titles, login emails, raw discovered paths, source file contents, Claude credentials, or API keys.
