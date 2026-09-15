# Claude Usage Analyzer

Per-person usage tracking for a Claude subscription that a whole team shares.

A small collector runs on each team member's machine, reads local Claude usage data, and uploads metrics only (never prompts or responses) to a shared dashboard.

- [Why this exists](#why-this-exists)
- [Install](#install) — [macOS](#macos) · [Linux](#linux) · [Windows](#windows)
- [After installing](#after-installing) — check, update, uninstall, troubleshooting
- [Admin setup](#admin-setup)
- [Dashboard](#dashboard)
- [Privacy](#privacy)
- [Development](#development)

## Why This Exists

Small companies often share one Claude subscription (for example a single Max plan) across the whole team, with members working from different machines and countries. Claude's own usage screen only shows how much of that one account has been used in total. It cannot say who used it, how much each person used, what they were working on, or which Claude product they used.

This project answers exactly those questions for a shared account:

- **Who** is using it: each person and machine, identified independently of the shared Claude login.
- **How much** each person uses: exact tokens where the machine records them, activity everywhere else.
- **What** they are working on: projects, models, tools, and sessions.
- **Where** the usage comes from: Claude Code, Claude Desktop, Cowork, extensions.

Every design choice should be judged against these goals.

### What each machine can and cannot see

| Signal | Source | Exact? |
|---|---|---|
| Tokens per request, session, project, model, branch | Claude Code transcripts (terminal, IDE, Desktop Code tab, Agent SDK, Cowork) in every Claude config dir | Exact |
| Which Claude account the machine is signed in to | `~/.claude.json`, Claude Desktop session folders | Exact (email only as a hash) |
| The account's 5-hour and 7-day usage % | Claude Desktop `plan-usage-history.json`, and Claude Code's statusline input captured by the collector | Account-wide, covers claude.ai web too |
| Desktop Code tab and Cowork sessions (model, effort, turns) | Claude Desktop session records | Exact metadata, no titles |
| Claude Desktop chat, IDE and Chrome extensions | Local files and folders | Activity or install evidence only |

claude.ai web and Desktop chat conversations live on Anthropic's servers, and Pro/Max accounts have no usage API, so their per-person tokens cannot be measured. The dashboard instead compares rises in the account's usage % with activity from tracked machines and shows the untracked share as an estimate.

## Install

Install once on **every machine and every OS user account** that uses the shared Claude account. You need the **enrollment secret** from your admin; the installer asks for it with hidden input. Never paste it into a command, chat, or ticket.

No admin rights are needed on any platform. Everything installs into your user account.

| Platform | Supported | Scheduler | Agent folder |
|---|---|---|---|
| macOS 11+ | Apple Silicon, Intel | LaunchAgent | `~/.claude-usage-agent` |
| Linux | x64, arm64 | systemd user timer, or cron | `~/.claude-usage-agent` |
| Windows 10/11 | x64 | Task Scheduler | `%LOCALAPPDATA%\ClaudeUsageAgent` |

### macOS

Open **Terminal** and download the installer:

```bash
curl --proto '=https' -fsSL https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/v0.8.0/install/install.sh -o /tmp/claude-usage-install.sh
```

Run it, and enter the enrollment secret when asked:

```bash
sh /tmp/claude-usage-install.sh
```

Downloading first, instead of `curl … | sh`, means a dropped connection can never run half a script.

- Needs `curl` and `tar`, both built into macOS.
- The sync runs every 30 minutes while you are logged in, from `~/Library/LaunchAgents/com.internal.claude-usage-agent.plist`.

### Linux

Same two commands in a terminal:

```bash
curl --proto '=https' -fsSL https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/v0.8.0/install/install.sh -o /tmp/claude-usage-install.sh
```

```bash
sh /tmp/claude-usage-install.sh
```

- Needs `curl`, `tar`, and `sha256sum` or `shasum`. On minimal images install them first, for example `sudo apt install curl tar coreutils`.
- Uses a systemd user timer (`~/.config/systemd/user/claude-usage-agent.timer`) when `systemctl` is available, otherwise a crontab entry.
- A systemd user timer only runs while you are logged in. For a server or a machine you use over SSH, keep it running after logout:

  ```bash
  loginctl enable-linger "$USER"
  ```

**WSL:** if you use Claude Code inside WSL on a Windows PC, install with the [Windows](#windows) installer only. It already reads Claude Code data from single-user WSL distros. Installing in both would count the same usage twice. One gap: the usage % statusline capture is not added inside WSL.

### Windows

Open **PowerShell** (not Command Prompt; Windows PowerShell 5.1 and PowerShell 7 both work) as your normal user, not as Administrator, and run:

```powershell
irm https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/v0.8.0/install/install.ps1 | iex
```

Enter the enrollment secret when asked.

- `irm` downloads the whole script before `iex` runs it, so no separate download step is needed. Execution policy does not block this.
- The sync runs every 30 minutes as the scheduled task **Claude Usage Agent**, while you are logged in.
- If the download fails with a TLS or "could not create SSL/TLS secure channel" error (old Windows PowerShell 5.1), run this first in the same window, then retry:

  ```powershell
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  ```

- If antivirus quarantines `claude-usage-agent.exe`, ask your admin. The installer only runs it after checking its SHA-256 against the release.

### What the installer does

1. Checks the system and that the server is reachable.
2. Downloads the agent for the pinned release and verifies it against that release's `SHA256SUMS`. It stops if the checksum does not match.
3. Enrolls the machine. Re-running it re-enrolls with the machine's existing token.
4. Adds the usage % statusline capture to Claude Code. Any statusline you already have keeps working, and `settings.json` is backed up first.
5. Syncs all available history once.
6. Schedules a sync every 30 minutes that resends the last 30 days.

When it finishes it prints the collector status. Every problem is printed as a `BLOCKED` line with a `FIX` line under it.

### Installer options

Set these before running the installer. On macOS/Linux put them in front of `sh`, for example `SKIP_STATUSLINE=1 sh /tmp/claude-usage-install.sh`. On Windows set them first, for example `$env:SKIP_STATUSLINE = '1'`, then run the `irm` command in the same window.

| Variable | Default | Effect |
|---|---|---|
| `SKIP_STATUSLINE` | unset | `1` skips the usage % statusline capture |
| `SKIP_SCHEDULER` | unset | `1` installs without scheduling syncs |
| `SYNC_INTERVAL_MINUTES` | `30` | How often the scheduled sync runs |
| `SYNC_DAYS` | `30` | Days each scheduled sync resends |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code config dir, if you moved it |
| `ACCOUNT_LABEL` / `COLLECTOR_LABEL` | OS username / hostname | Names shown for this machine in the dashboard |
| `AGENT_VERSION` | the version in the URL | Release to install |
| `ALLOW_SOURCE_FALLBACK` | unset | `1` runs the release's Python source (3.8+) if the binary can't be downloaded |
| `CLAUDE_USAGE_AGENT_DIR` | see table above | Agent folder (macOS/Linux only) |

## After Installing

### Check it is working

macOS/Linux:

```bash
~/.claude-usage-agent/claude-usage-agent --status
```

Windows PowerShell:

```powershell
& "$env:LOCALAPPDATA\ClaudeUsageAgent\claude-usage-agent.exe" --status
```

`Last sync` should be within the last 30 minutes. If it isn't, run a sync by hand to see the error: replace `--status` with `--sync --days 30` in the command above. On macOS/Linux, scheduled sync output is also in `~/.claude-usage-agent/logs/` (`sync.log`, `sync.err`).

### Update

Run the install command again with the new version in the URL. The machine keeps its identity and token.

### Uninstall

macOS:

```bash
~/.claude-usage-agent/claude-usage-agent --uninstall-statusline
```

```bash
launchctl unload ~/Library/LaunchAgents/com.internal.claude-usage-agent.plist && rm ~/Library/LaunchAgents/com.internal.claude-usage-agent.plist
```

```bash
rm -rf ~/.claude-usage-agent
```

Linux:

```bash
~/.claude-usage-agent/claude-usage-agent --uninstall-statusline
```

```bash
systemctl --user disable --now claude-usage-agent.timer; rm -f ~/.config/systemd/user/claude-usage-agent.service ~/.config/systemd/user/claude-usage-agent.timer
```

If cron was used instead, remove the `claude-usage-agent` line with `crontab -e`. Then:

```bash
rm -rf ~/.claude-usage-agent
```

Windows PowerShell:

```powershell
& "$env:LOCALAPPDATA\ClaudeUsageAgent\claude-usage-agent.exe" --uninstall-statusline
```

```powershell
Unregister-ScheduledTask -TaskName "Claude Usage Agent" -Confirm:$false
```

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\ClaudeUsageAgent"
```

Uninstalling stops new uploads. Data already uploaded stays in the dashboard; ask an admin to remove it.

### Troubleshooting

| Message | Meaning and fix |
|---|---|
| `Supabase enroll unreachable` | No route to the server. Check internet, VPN, proxy or firewall, then rerun. |
| `Enrollment failed` / `invalid enrollment secret` | Wrong or old secret. Get the current one from your admin and rerun. |
| `too many failed attempts` | 10 wrong secrets from your network in the last hour. Wait an hour. |
| `Checksum mismatch` | The download doesn't match the release. Don't run it; retry later and tell your admin if it repeats. |
| `Unsupported OS/CPU` | Only the platforms in the table above have binaries. |
| `Claude projects dir missing` (warning) | Claude Code hasn't been used by this OS user yet. Installing is still fine. |
| Sync fails with HTTP 403 | The machine's token was revoked, for example after a server reset. Rerun the install command. |
| Sync fails with HTTP 429 | A sync ran less than 10 seconds after the previous one. The next scheduled sync will succeed. |

## Admin Setup

Current project:

```text
Supabase project : c-usage-anlyst (yeokmzmmldqjngwtrfso)
Org ID           : team-main
Enroll           : https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll
Ingest           : https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest
Dashboard        : https://claude-usage-dashboard.netlify.app
```

### New server

1. Create a Supabase project and apply `supabase/schema.sql` (safe to re-run).
2. Deploy `supabase/functions/enroll` and `supabase/functions/ingest`, both with JWT verification off. They authenticate with the enrollment secret and collector tokens.
3. Create the organization:

   ```sql
   insert into organizations(id, name)
   values ('team-main', 'Team Main')
   on conflict (id) do nothing;
   ```

4. In Supabase Auth, turn off **Allow new users to sign up**. Dashboard sign-in is invite-only.
5. Host the dashboard (see [Dashboard](#dashboard)) and set the Supabase Auth Site URL to it.

### Enrollment secret

Only the secret's SHA-256 hash is stored, in `enrollment_secrets` (RLS on, no client access). Use at least 32 random bytes:

```bash
openssl rand -base64 32
```

```sql
insert into enrollment_secrets(secret_hash, org_id, label)
values (encode(extensions.digest('<secret>', 'sha256'), 'hex'), 'team-main', 'install secret');
```

Share it with installers privately. It is never stored on their machines. To rotate, insert a new row and set `revoked_at` on the old one; enrolled machines keep working, since they use their own tokens.

Enrollment allows 10 failed attempts per IP per hour and 25 enrollments per IP per day.

### Dashboard members

Invite each person from the Supabase Auth dashboard, then add them to the organization:

```sql
insert into org_members(org_id, user_id, role, can_view_history)
values ('team-main', '<auth user id>', 'member', false);
```

`role` is `member`, `admin` or `owner`. Admins manage the history permission in the dashboard under Collectors → Dashboard access.

### Releasing a new collector version

1. Bump `APP_VERSION` in `claude_usage/constants.py`, `AGENT_VERSION` in both installers, and the install URLs in this README.
2. Commit, then push a matching tag, for example `v0.8.1`.
3. GitHub Actions tests, builds the five binaries, and publishes them with `SHA256SUMS`. A tag cannot overwrite binaries that were already published.

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

## Privacy

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

It does not upload prompts, responses, raw transcript text, session titles, login emails, raw discovered paths, source file contents, browser history or cookies, Claude credentials, or API keys. It makes no calls to Anthropic's private usage endpoints, and it only reads the OS user it is installed for.

Agent files (`config.json` with the collector token, state, logs) are readable only by the installing user.

## Development

### Local report

Runs from a checkout with Python 3.9+ and no dependencies:

```bash
python3 claude_usage_analyzer.py
```

`--verbose` shows detailed tables and `--sources` lists the discovered Claude surfaces.

### Manual collector commands

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

### Tests

```bash
python3 -m unittest discover -s tests -t .
```

`tests/fixture_home.py` builds a deterministic fake home (Claude Code transcripts plus Desktop/Cowork data) used by the flow and CLI tests.

### How it is organized

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

Known limitations and the security audit are in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
