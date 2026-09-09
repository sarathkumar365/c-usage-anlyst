# Claude Usage Analyzer

Cross-platform Claude Code usage analyzer and metrics-only team collector.

## Local Report

```bash
python3 claude_usage_analyzer.py
```

The default output is a colored, plain-English terminal report. Use the old detailed tables with:

```bash
python3 claude_usage_analyzer.py --verbose
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

macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/install/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/install/install.ps1 | iex
```

The installer runs a preflight system check, downloads the native agent binary for the user's OS/CPU when a GitHub Release asset is available, enrolls the machine, stores user-level config, schedules a sync every 30 minutes, and runs one immediate sync. If release binaries are unavailable, it falls back to the Python script when Python 3.8+ is installed. If you need the old explicit-token install path, set `COLLECTOR_TOKEN` before running the installer.

Release binaries are built by GitHub Actions when a version tag is pushed:

```bash
git tag v0.4.2
git push github v0.4.2
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
```

## Dashboard

Host `dashboard/index.html` on any static host, including Cloudflare Pages, Vercel, Netlify, or Supabase Storage.

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
- machine/user identifiers
- tool names and counts
- anomaly flags

It does not upload prompts, responses, raw transcript text, source file contents, Claude credentials, or API keys.
