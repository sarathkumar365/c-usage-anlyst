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
export INGEST_URL="https://YOUR_PROJECT.supabase.co/functions/v1/ingest"
export COLLECTOR_TOKEN="replace-with-a-long-random-token"
export ORG_ID="team-main"
export ACCOUNT_LABEL="shared-claude-account"
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/YOUR_REPO/main/install/install.sh | sh
```

Windows PowerShell:

```powershell
$env:INGEST_URL="https://YOUR_PROJECT.supabase.co/functions/v1/ingest"
$env:COLLECTOR_TOKEN="replace-with-a-long-random-token"
$env:ORG_ID="team-main"
$env:ACCOUNT_LABEL="shared-claude-account"
irm https://raw.githubusercontent.com/YOUR_ORG/YOUR_REPO/main/install/install.ps1 | iex
```

The installer stores user-level config, schedules a sync every 30 minutes, and runs one immediate sync.

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
