# Installs the Claude usage agent for the current Windows user.
# Phases: Test-System -> Install-Runtime -> Invoke-Preflight -> Register-Agent -> Install-Statusline -> Invoke-FirstSync -> Install-Scheduler
param(
  # Pinned to the release this installer belongs to; the binary is verified against that release's SHA256SUMS.
  [string]$AgentVersion = $(if ($env:AGENT_VERSION) { $env:AGENT_VERSION } else { "v0.8.0" }),
  [string]$IngestUrl = $(if ($env:INGEST_URL) { $env:INGEST_URL } else { "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest" }),
  [string]$EnrollUrl = $(if ($env:ENROLL_URL) { $env:ENROLL_URL } else { "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll" }),
  [string]$OrgId = $(if ($env:ORG_ID) { $env:ORG_ID } else { "team-main" }),
  [string]$AccountLabel = $(if ($env:ACCOUNT_LABEL) { $env:ACCOUNT_LABEL } else { $env:USERNAME }),
  [string]$CollectorLabel = $(if ($env:COLLECTOR_LABEL) { $env:COLLECTOR_LABEL } else { $env:COMPUTERNAME }),
  [string]$ClaudeDir = $(if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME ".claude" }),
  [int]$SyncIntervalMinutes = $(if ($env:SYNC_INTERVAL_MINUTES) { [int]$env:SYNC_INTERVAL_MINUTES } else { 30 }),
  # Scheduled syncs resend this many days; the first sync after install sends all history.
  [int]$SyncDays = $(if ($env:SYNC_DAYS) { [int]$env:SYNC_DAYS } else { 30 }),
  [string]$ReleaseBaseUrl = $(if ($env:RELEASE_BASE_URL) { $env:RELEASE_BASE_URL } else { "" }),
  [string]$SourceArchiveUrl = $(if ($env:SOURCE_ARCHIVE_URL) { $env:SOURCE_ARCHIVE_URL } else { "" })
)
if (-not $ReleaseBaseUrl) { $ReleaseBaseUrl = "https://github.com/sarathkumar365/c-usage-anlyst/releases/download/$AgentVersion" }
if (-not $SourceArchiveUrl) { $SourceArchiveUrl = "https://codeload.github.com/sarathkumar365/c-usage-anlyst/zip/refs/tags/$AgentVersion" }

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$AppDir = Join-Path $env:LOCALAPPDATA "ClaudeUsageAgent"
$BinPath = Join-Path $AppDir "claude-usage-agent.exe"
$SrcDir = Join-Path $AppDir "src"
$ScriptPath = Join-Path $SrcDir "claude_usage_analyzer.py"
$LogDir = Join-Path $AppDir "logs"
$AssetName = "claude-usage-agent-windows-x64.exe"
$ReleaseAssetUrl = "$ReleaseBaseUrl/$AssetName"

$script:RuntimeMode = ""
$script:PyExe = ""
$script:ProbeUrl = ""

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

# Deliberately not an advanced function: agent flags like --sync must reach $args untouched.
function Invoke-Agent {
  if ($script:RuntimeMode -eq "binary") {
    & $BinPath @args
  } else {
    & $script:PyExe $ScriptPath @args
  }
}

function Test-System {
  Write-Host ""
  Write-Host "SYSTEM CHECK"
  Write-Host "------------------------------------"

  $Arch = $env:PROCESSOR_ARCHITECTURE
  if ($Arch -notin @("AMD64", "x86_64")) {
    Stop-Install "Unsupported OS/CPU: Windows $Arch" "Use Windows x64, or install manually with Python."
  }
  Write-Check "OK" "OS supported: Windows $Arch"

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

  if (-not $env:ENROLLMENT_SECRET) {
    # Prompt instead of taking the secret on the command line, where it would land in PowerShell history.
    $Secure = Read-Host -Prompt "Enrollment secret (input hidden)" -AsSecureString
    $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure))
    if (-not $Plain) { Stop-Install "No enrollment secret provided" "Rerun the install command and enter the secret your admin gave you." }
    $env:ENROLLMENT_SECRET = $Plain
  }
}

function Install-PythonSource {
  $Python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $Python) { $Python = Get-Command py -ErrorAction SilentlyContinue }
  if (-not $Python) {
    Stop-Install "No native binary and Python is missing" "Publish GitHub release assets or install Python 3.8+."
  }
  $script:PyExe = $Python.Source
  $PyVersion = & $script:PyExe -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); raise SystemExit(0 if sys.version_info >= (3, 8) else 1)"
  if ($LASTEXITCODE -ne 0) {
    Stop-Install "Python 3.8+ is required for fallback, found $PyVersion" "Install Python 3.8+ or wait for native release assets."
  }

  $Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
  New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
  $Zip = Join-Path $Tmp "source.zip"
  Invoke-WebRequest -Uri $SourceArchiveUrl -OutFile $Zip -UseBasicParsing
  Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
  $Extracted = Get-ChildItem -Path $Tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "claude_usage_analyzer.py") } | Select-Object -First 1
  if (-not $Extracted) {
    Stop-Install "Agent source archive is missing claude_usage_analyzer.py" "Check SOURCE_ARCHIVE_URL."
  }
  foreach ($Old in @($SrcDir, (Join-Path $AppDir "claude_usage"), (Join-Path $AppDir "claude_usage_analyzer.py"))) {
    if (Test-Path $Old) { Remove-Item -Recurse -Force $Old }
  }
  New-Item -ItemType Directory -Force -Path $SrcDir | Out-Null
  Copy-Item (Join-Path $Extracted.FullName "claude_usage_analyzer.py") $SrcDir
  Copy-Item -Recurse (Join-Path $Extracted.FullName "claude_usage") $SrcDir
  Remove-Item -Recurse -Force $Tmp

  $script:RuntimeMode = "python"
  $script:ProbeUrl = $SourceArchiveUrl
  Write-Check "OK" "Python fallback ready: $PyVersion"
}

function Install-Runtime {
  $Download = Join-Path $AppDir (".download-" + [System.Guid]::NewGuid().ToString())
  try {
    Invoke-WebRequest -Uri $ReleaseAssetUrl -OutFile $Download -UseBasicParsing -TimeoutSec 120
    $Sums = (Invoke-WebRequest -Uri "$ReleaseBaseUrl/SHA256SUMS" -UseBasicParsing -TimeoutSec 30).Content
    if ($Sums -is [byte[]]) { $Sums = [Text.Encoding]::UTF8.GetString($Sums) }
  } catch {
    if (Test-Path $Download) { Remove-Item -Force $Download }
    if (-not $env:ALLOW_SOURCE_FALLBACK) {
      Stop-Install "Could not download the $AgentVersion agent" "Check internet access and rerun, or set `$env:ALLOW_SOURCE_FALLBACK = '1' to run the $AgentVersion source with Python."
    }
    Write-Check "WARN" "Native release asset unavailable, using the $AgentVersion source with Python (ALLOW_SOURCE_FALLBACK)"
    Install-PythonSource
    return
  }
  $Expected = ($Sums -split "`n" | ForEach-Object { $Parts = $_.Trim() -split "\s+"; if ($Parts.Count -ge 2 -and $Parts[1].TrimStart("*") -eq $AssetName) { $Parts[0] } } | Select-Object -First 1)
  $Actual = (Get-FileHash -Algorithm SHA256 -Path $Download).Hash.ToLowerInvariant()
  if (-not $Expected -or $Expected.ToLowerInvariant() -ne $Actual) {
    Remove-Item -Force $Download
    Stop-Install "Checksum mismatch for $AssetName ($AgentVersion)" "Do not run this binary. Retry later or contact your admin."
  }
  Move-Item -Force $Download $BinPath
  $script:RuntimeMode = "binary"
  $script:ProbeUrl = $ReleaseAssetUrl
  Write-Check "OK" "Downloaded and verified native agent: $AssetName $AgentVersion"
}

function Invoke-Preflight {
  Invoke-Agent --preflight --no-color --release-asset-url $script:ProbeUrl --enroll-url $EnrollUrl --ingest-url $IngestUrl --claude-dir $ClaudeDir
  if ($LASTEXITCODE -ne 0) { Stop-Install "Agent preflight failed" "Fix the blocked item above, then rerun." }
}

function Register-Agent {
  # The secret reaches the agent through the environment only, never as an argument visible in the process list.
  Invoke-Agent --enroll --enroll-url $EnrollUrl --org-id $OrgId --account-label $AccountLabel --collector-label $CollectorLabel --claude-dir $ClaudeDir --sync-interval-minutes "$SyncIntervalMinutes"
  if ($LASTEXITCODE -ne 0) { Stop-Install "Agent enrollment failed" "Fix the error above, then rerun." }
}

function Install-Statusline {
  if ($env:SKIP_STATUSLINE) {
    Write-Check "WARN" "Usage % capture skipped (SKIP_STATUSLINE set)"
    return
  }
  Invoke-Agent --install-statusline --claude-dir $ClaudeDir
}

function Install-Scheduler {
  if ($env:SKIP_SCHEDULER) {
    Write-Check "WARN" "Scheduler install skipped (SKIP_SCHEDULER set)"
    return
  }
  if ($script:RuntimeMode -eq "binary") {
    $Action = New-ScheduledTaskAction -Execute $BinPath -Argument "--sync --days $SyncDays"
  } else {
    $Action = New-ScheduledTaskAction -Execute $script:PyExe -Argument "`"$ScriptPath`" --sync --days $SyncDays"
  }
  $Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $SyncIntervalMinutes)
  $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  Register-ScheduledTask `
    -TaskName "Claude Usage Agent" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Uploads metrics-only Claude usage and activity aggregates." `
    -Force | Out-Null
  Write-Check "OK" "Scheduler installed: Task Scheduler"
}

function Invoke-FirstSync {
  # All available history once; scheduled runs then resend only the last $SyncDays days.
  Invoke-Agent --sync
  if ($LASTEXITCODE -ne 0) { Write-Check "WARN" "First sync failed; the scheduled sync will retry" }
}

Test-System
Install-Runtime
Invoke-Preflight
Register-Agent
Install-Statusline
# Before the scheduler, so the all-history sync goes first.
Invoke-FirstSync
Install-Scheduler
Invoke-Agent --status
