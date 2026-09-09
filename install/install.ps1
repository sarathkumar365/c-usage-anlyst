param(
  [string]$IngestUrl = $(if ($env:INGEST_URL) { $env:INGEST_URL } else { "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest" }),
  [string]$CollectorToken = $env:COLLECTOR_TOKEN,
  [string]$EnrollUrl = $(if ($env:ENROLL_URL) { $env:ENROLL_URL } else { "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll" }),
  [string]$OrgId = $(if ($env:ORG_ID) { $env:ORG_ID } else { "team-main" }),
  [string]$AccountLabel = $(if ($env:ACCOUNT_LABEL) { $env:ACCOUNT_LABEL } else { $env:USERNAME }),
  [string]$CollectorLabel = $env:COLLECTOR_LABEL,
  [string]$ClaudeDir = $(if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME ".claude" }),
  [int]$SyncIntervalMinutes = $(if ($env:SYNC_INTERVAL_MINUTES) { [int]$env:SYNC_INTERVAL_MINUTES } else { 30 }),
  [string]$CollectorUrl = $(if ($env:COLLECTOR_URL) { $env:COLLECTOR_URL } else { "https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/claude_usage_analyzer.py" })
)

$ErrorActionPreference = "Stop"

$AppDir = Join-Path $env:LOCALAPPDATA "ClaudeUsageAgent"
$ScriptPath = Join-Path $AppDir "claude_usage_analyzer.py"
$LogDir = Join-Path $AppDir "logs"
New-Item -ItemType Directory -Force -Path $AppDir, $LogDir | Out-Null

Invoke-WebRequest -Uri $CollectorUrl -OutFile $ScriptPath

if (-not $CollectorLabel) {
  $CollectorLabel = $env:COMPUTERNAME
}

if ($CollectorToken) {
  python $ScriptPath --register `
    --ingest-url $IngestUrl `
    --collector-token $CollectorToken `
    --org-id $OrgId `
    --account-label $AccountLabel `
    --collector-label $CollectorLabel `
    --claude-dir $ClaudeDir `
    --sync-interval-minutes $SyncIntervalMinutes
} else {
  python $ScriptPath --enroll `
    --enroll-url $EnrollUrl `
    --org-id $OrgId `
    --account-label $AccountLabel `
    --collector-label $CollectorLabel `
    --claude-dir $ClaudeDir `
    --sync-interval-minutes $SyncIntervalMinutes
}

$Action = New-ScheduledTaskAction -Execute "python" -Argument "`"$ScriptPath`" --sync --days 90"
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $SyncIntervalMinutes)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
  -TaskName "Claude Usage Agent" `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Uploads metrics-only Claude Code usage aggregates." `
  -Force | Out-Null

python $ScriptPath --sync --days 90
python $ScriptPath --status
