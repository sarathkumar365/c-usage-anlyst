# Claude Usage Analyzer

Cross-platform Claude usage and activity analyzer with a metrics-only team collector.

It reports exact token usage from Claude Code transcripts and derived/evidence activity from local Claude Desktop, Cowork, and extension metadata.

## How It Is Organized

Every piece of work belongs to one flow. Each flow is one module that receives already-resolved inputs and owns its own side effects.

| Flow | Agent code | Server code | Writes |
|---|---|---|---|
| Discover | `claude_usage/flows/discover.py` → `discovery.py` | – | `state.json` (`discovery`) |
| Collect | `claude_usage/flows/collect.py` → `transcripts.py` | – | nothing |
| Enroll / register | `claude_usage/flows/enroll.py` | `supabase/functions/enroll` | `config.json`, `collector_tokens` |
| Sync | `claude_usage/flows/sync.py` → `activity.py`, `anomalies.py`, `payload.py` | `supabase/functions/ingest` | `state.json`, usage tables |
| Preflight / status | `claude_usage/flows/preflight.py`, `status.py` | – | `state.json` (via discover) |
| Reports | `claude_usage/reports/` (plain, verbose, sources, export) | – | export files only |
| Dashboard | `dashboard/index.html` | `dashboard_*` views in `supabase/schema.sql` | `identity_aliases` (admins) |
| Install | `install/install.sh`, `install/install.ps1` | – | agent dir, OS scheduler |

Shared building blocks: `cli.py` (argument parsing and dispatch only), `paths.py` (the single Claude/agent directory resolver), `store.py` (the only reader/writer of `config.json`/`state.json`), `identity.py`, `transport.py` (HTTP), `models.py`, `metrics.py`, `ui.py`, `util.py`, `constants.py`. `claude_usage_analyzer.py` is only the entry point.

A sync runs: collect transcripts → discover sources → extract activity → build the payload (pure, no I/O) → upload → record the outcome in `state.json`. Ingest runs: authenticate token → validate payload → skip duplicates → write usage rows → record the run.

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

Create a Supabase project, apply `supabase/schema.sql`, deploy `supabase/functions/ingest`, then create an organization and collector token.

Current configured Supabase project:

```text
Project : c-usage-anlyst
Ref     : yeokmzmmldqjngwtrfso
URL     : https://yeokmzmmldqjngwtrfso.supabase.co
Org ID  : team-main
Ingest  : https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest
Dashboard: https://c-usage-anlyst.poetic-whale-8476.chatgpt.site
```

Example SQL for first setup:

```sql
insert into organizations(id, name)
values ('team-main', 'Team Main')
on conflict (id) do nothing;

select create_collector_token(
  'team-main',
  'default install token',
  'replace-with-a-long-random-token'
);
```

For the dashboard user, add their Supabase Auth user id:

```sql
insert into org_members(org_id, user_id, role)
values ('team-main', '00000000-0000-0000-0000-000000000000', 'admin');
```

## Install

Enrollment requires a shared secret. Only its SHA-256 hash is stored, in the `enrollment_secrets` table (RLS on, no client access):

```sql
insert into enrollment_secrets(secret_hash, org_id, label)
values (encode(digest('<secret>', 'sha256'), 'hex'), 'team-main', 'install secret');
```

Give the secret to installers out of band. It is sent only during enrollment and is not stored on the machine. Rotate by inserting a new row and setting `revoked_at` on the old one.

macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/install/install.sh | ENROLLMENT_SECRET='<secret>' sh
```

Windows PowerShell:

```powershell
$env:ENROLLMENT_SECRET = '<secret>'
irm https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/install/install.ps1 | iex
```

The installer runs a preflight system check, downloads the native agent binary for the user's OS/CPU when a GitHub Release asset is available, enrolls the machine, stores user-level config, schedules a sync every 30 minutes, and runs one immediate sync. If release binaries are unavailable, it downloads the source archive and runs it with Python 3.8+. If you need the old explicit-token install path, set `COLLECTOR_TOKEN` before running the installer.

Release binaries are built by GitHub Actions when a version tag is pushed:

```bash
git tag v0.4.3
git push github v0.4.3
```

## Manual Collector Commands

```bash
python3 claude_usage_analyzer.py --register \
  --ingest-url "https://YOUR_PROJECT.supabase.co/functions/v1/ingest" \
  --collector-token "replace-with-a-long-random-token" \
  --org-id "team-main" \
  --account-label "shared-claude-account"

python3 claude_usage_analyzer.py --status
python3 claude_usage_analyzer.py --sync --days 90
python3 claude_usage_analyzer.py --sync --dry-run --days 90
python3 claude_usage_analyzer.py --sync --surface desktop --days 90
```

## Dashboard

Host `dashboard/index.html` on any static host, including Cloudflare Pages, Vercel, Netlify, or Supabase Storage.

The dashboard opens on three views of the same question, who is using the most and why:

- **Brief** — a written summary: the top user as a headline, the ranking, and short stories for the next people.
- **Share** — a proportional field of people, projects, or models.
- **Work lanes** — the last 7 days of sessions per person (length is time, height is tokens).

Data pages (People, Projects & models, Sessions, Collectors) and a person panel sit alongside. "Why" reasons are derived in the page from session length, subagent share, context reuse, model mix, and top tools (`dashboard_person_tools`). All views are paged, so none is capped at PostgREST's row limit.

The dashboard asks for:

- Supabase URL
- Supabase publishable/anon key
- Org ID
- email for Supabase magic-link login

`dashboard/index.html` is prefilled for the `c-usage-anlyst` Supabase project.

## Privacy Defaults

The collector uploads metrics only:

- token counts
- model/project/session aggregates
- source confidence levels
- Claude surface activity counts
- machine/user identifiers
- tool names and counts
- anomaly flags

It does not upload prompts, responses, raw transcript text, raw discovered paths, source file contents, Claude credentials, or API keys.
