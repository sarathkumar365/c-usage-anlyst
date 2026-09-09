param(
  [string]$IngestUrl = $(if ($env:INGEST_URL) { $env:INGEST_URL } else { "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest" }),
  [string]$CollectorToken = $env:COLLECTOR_TOKEN,
  [string]$EnrollUrl = $(if ($env:ENROLL_URL) { $env:ENROLL_URL } else { "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll" }),
  [string]$OrgId = $(if ($env:ORG_ID) { $env:ORG_ID } else { "team-main" }),
  [string]$AccountLabel = $(if ($env:ACCOUNT_LABEL) { $env:ACCOUNT_LABEL } else { $env:USERNAME }),
  [string]$CollectorLabel = $env:COLLECTOR_LABEL,
  [string]$ClaudeDir = $(if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME ".claude" }),
  [int]$SyncIntervalMinutes = $(if ($env:SYNC_INTERVAL_MINUTES) { [int]$env:SYNC_INTERVAL_MINUTES } else { 30 }),
  [string]$ReleaseBaseUrl = $(if ($env:RELEASE_BASE_URL) { $env:RELEASE_BASE_URL } else { "https://github.com/sarathkumar365/c-usage-anlyst/releases/latest/download" }),
  [string]$CollectorUrl = $(if ($env:COLLECTOR_URL) { $env:COLLECTOR_URL } else { "https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/claude_usage_analyzer.py" })
)

$ErrorActionPreference = "Stop"

function Write-Check($Status, $Message) {
  "{0,-8} {1}" -f $Status, $Message | Write-Host
}

function Stop-Install($Message, $Fix) {
  Write-Check "BLOCKED" $Message
  if ($Fix) { Write-Check "FIX" $Fix }
  exit 2
}

function Test-Url($Url, $Method = "Get") {
  try {
    Invoke-WebRequest -Uri $Url -Method $Method -UseBasicParsing -TimeoutSec 12 | Out-Null
    return $true
  } catch {
    return $false
  }
}

Write-Host ""
Write-Host "SYSTEM CHECK"
Write-Host "------------------------------------"

$Arch = $env:PROCESSOR_ARCHITECTURE
if ($Arch -in @("AMD64", "x86_64")) {
  $AssetName = "claude-usage-agent-windows-x64.exe"
} else {
  Stop-Install "Unsupported OS/CPU: Windows $Arch" "Use Windows x64, or install manually with Python."
}
Write-Check "OK" "OS supported: Windows $Arch"

$AppDir = Join-Path $env:LOCALAPPDATA "ClaudeUsageAgent"
$BinPath = Join-Path $AppDir "claude-usage-agent.exe"
$ScriptPath = Join-Path $AppDir "claude_usage_analyzer.py"
$LogDir = Join-Path $AppDir "logs"
$ReleaseAssetUrl = "$ReleaseBaseUrl/$AssetName"

New-Item -ItemType Directory -Force -Path $AppDir, $LogDir | Out-Null
try {
  $TestPath = Join-Path $AppDir ".write-test"
  "ok" | Set-Content -Path $TestPath
  Remove-Item -Path $TestPath -Force
  Write-Check "OK" "Install dir writable: $AppDir"
} catch {
  Stop-Install "Install dir is not writable: $AppDir" "Check user permissions."
}

if (Test-Path (Join-Path $ClaudeDir "projects")) {
  Write-Check "OK" "Claude projects dir found: $(Join-Path $ClaudeDir "projects")"
} else {
  Write-Check "WARN" "Claude projects dir missing: $(Join-Path $ClaudeDir "projects")"
}

if (Test-Url $EnrollUrl "Options") {
  Write-Check "OK" "Supabase enroll reachable"
} else {
  Stop-Install "Supabase enroll unreachable" "Check internet/VPN/firewall and rerun."
}

$RuntimeMode = ""
if (Test-Url $ReleaseAssetUrl) {
  Invoke-WebRequest -Uri $ReleaseAssetUrl -OutFile $BinPath -UseBasicParsing
  $RuntimeMode = "binary"
  Write-Check "OK" "Downloaded native agent: $AssetName"
} else {
  Write-Check "WARN" "Native release asset unavailable, trying Python fallback"
  $Python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $Python) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
  }
  if (-not $Python) {
    Stop-Install "No native binary and Python is missing" "Publish GitHub release assets or install Python 3.8+."
  }
  $PyExe = $Python.Source
  $PyVersion = & $PyExe -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"
  if ($LASTEXITCODE -ne 0) {
    Stop-Install "Python 3.8+ is required for fallback, found $PyVersion" "Install Python 3.8+ or wait for native release assets."
  }
  Invoke-WebRequest -Uri $CollectorUrl -OutFile $ScriptPath -UseBasicParsing
  $RuntimeMode = "python"
  Write-Check "OK" "Python fallback ready: $PyVersion"
}

if (-not $CollectorLabel) {
  $CollectorLabel = $env:COMPUTERNAME
}

if ($RuntimeMode -eq "binary") {
  & $BinPath --preflight --no-color --release-asset-url $ReleaseAssetUrl --enroll-url $EnrollUrl --ingest-url $IngestUrl --claude-dir $ClaudeDir
  if ($LASTEXITCODE -ne 0) { Stop-Install "Agent preflight failed" "Fix the blocked item above, then rerun." }
  if ($CollectorToken) {
    & $BinPath --register --ingest-url $IngestUrl --collector-token $CollectorToken --org-id $OrgId --account-label $AccountLabel --collector-label $CollectorLabel --claude-dir $ClaudeDir --sync-interval-minutes $SyncIntervalMinutes
  } else {
    & $BinPath --enroll --enroll-url $EnrollUrl --org-id $OrgId --account-label $AccountLabel --collector-label $CollectorLabel --claude-dir $ClaudeDir --sync-interval-minutes $SyncIntervalMinutes
  }
  $Action = New-ScheduledTaskAction -Execute $BinPath -Argument "--sync --days 90"
} else {
  & $PyExe $ScriptPath --preflight --no-color --release-asset-url $CollectorUrl --enroll-url $EnrollUrl --ingest-url $IngestUrl --claude-dir $ClaudeDir
  if ($LASTEXITCODE -ne 0) { Stop-Install "Agent preflight failed" "Fix the blocked item above, then rerun." }
  if ($CollectorToken) {
    & $PyExe $ScriptPath --register --ingest-url $IngestUrl --collector-token $CollectorToken --org-id $OrgId --account-label $AccountLabel --collector-label $CollectorLabel --claude-dir $ClaudeDir --sync-interval-minutes $SyncIntervalMinutes
  } else {
    & $PyExe $ScriptPath --enroll --enroll-url $EnrollUrl --org-id $OrgId --account-label $AccountLabel --collector-label $CollectorLabel --claude-dir $ClaudeDir --sync-interval-minutes $SyncIntervalMinutes
  }
  $Action = New-ScheduledTaskAction -Execute $PyExe -Argument "`"$ScriptPath`" --sync --days 90"
}

$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $SyncIntervalMinutes)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
  -TaskName "Claude Usage Agent" `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Uploads metrics-only Claude Code usage aggregates." `
  -Force | Out-Null
Write-Check "OK" "Scheduler installed: Task Scheduler"

if ($RuntimeMode -eq "binary") {
  & $BinPath --sync --days 90
  & $BinPath --status
} else {
  & $PyExe $ScriptPath --sync --days 90
  & $PyExe $ScriptPath --status
}
