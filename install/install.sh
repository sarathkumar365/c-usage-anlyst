#!/usr/bin/env sh
set -eu

APP_DIR="${CLAUDE_USAGE_AGENT_DIR:-$HOME/.claude-usage-agent}"
BIN_PATH="$APP_DIR/claude-usage-agent"
SCRIPT_PATH="$APP_DIR/claude_usage_analyzer.py"
LOG_DIR="$APP_DIR/logs"
SYNC_INTERVAL_MINUTES="${SYNC_INTERVAL_MINUTES:-30}"
RELEASE_BASE_URL="${RELEASE_BASE_URL:-https://github.com/sarathkumar365/c-usage-anlyst/releases/latest/download}"
COLLECTOR_URL="${COLLECTOR_URL:-https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/claude_usage_analyzer.py}"
INGEST_URL="${INGEST_URL:-https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest}"
ENROLL_URL="${ENROLL_URL:-https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll}"
ORG_ID="${ORG_ID:-team-main}"
ACCOUNT_LABEL="${ACCOUNT_LABEL:-$(id -un 2>/dev/null || whoami 2>/dev/null || hostname)}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

status() {
  printf '%-8s %s\n' "$1" "$2"
}

block() {
  status "BLOCKED" "$1"
  if [ "${2:-}" ]; then
    status "FIX" "$2"
  fi
  exit 2
}

warn() {
  status "WARN" "$1"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || block "$1 is missing" "Install $1, then rerun the install command."
}

probe_url() {
  method="${2:-GET}"
  curl -fsSL --max-time 12 -X "$method" "$1" >/dev/null 2>&1
}

echo
echo "SYSTEM CHECK"
echo "------------------------------------"

need_cmd curl
need_cmd uname

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
ASSET_NAME=""
case "$OS_NAME:$ARCH_NAME" in
  Darwin:arm64|Darwin:aarch64) ASSET_NAME="claude-usage-agent-darwin-arm64" ;;
  Darwin:x86_64|Darwin:amd64) ASSET_NAME="claude-usage-agent-darwin-x64" ;;
  Linux:arm64|Linux:aarch64) ASSET_NAME="claude-usage-agent-linux-arm64" ;;
  Linux:x86_64|Linux:amd64) ASSET_NAME="claude-usage-agent-linux-x64" ;;
  *) block "Unsupported OS/CPU: $OS_NAME $ARCH_NAME" "Use macOS/Linux arm64/x64, or install manually with Python." ;;
esac
RELEASE_ASSET_URL="$RELEASE_BASE_URL/$ASSET_NAME"
status "OK" "OS supported: $OS_NAME $ARCH_NAME"

mkdir -p "$APP_DIR" "$LOG_DIR" || block "Cannot create install directory: $APP_DIR" "Use a writable home directory."
if (umask 077 && : > "$APP_DIR/.write-test") 2>/dev/null; then
  rm -f "$APP_DIR/.write-test"
  status "OK" "Install dir writable: $APP_DIR"
else
  block "Install dir is not writable: $APP_DIR" "Check user permissions."
fi

if [ -d "$CLAUDE_DIR/projects" ]; then
  status "OK" "Claude projects dir found: $CLAUDE_DIR/projects"
else
  warn "Claude projects dir missing: $CLAUDE_DIR/projects"
fi

if probe_url "$ENROLL_URL" OPTIONS; then
  status "OK" "Supabase enroll reachable"
else
  block "Supabase enroll unreachable" "Check internet/VPN/firewall and rerun."
fi

RUNTIME_PATH=""
RUNTIME_MODE=""
PYTHON_BIN=""
if curl -fsSL --max-time 60 "$RELEASE_ASSET_URL" -o "$BIN_PATH"; then
  chmod 700 "$BIN_PATH"
  RUNTIME_PATH="$BIN_PATH"
  RUNTIME_MODE="binary"
  status "OK" "Downloaded native agent: $ASSET_NAME"
else
  warn "Native release asset unavailable, trying Python fallback"
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
    PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' || block "Python 3.8+ is required for fallback, found $PYTHON_VERSION" "Install Python 3.8+ or wait for native release assets."
    curl -fsSL "$COLLECTOR_URL" -o "$SCRIPT_PATH"
    chmod 600 "$SCRIPT_PATH"
    RUNTIME_PATH="$PYTHON_BIN $SCRIPT_PATH"
    RUNTIME_MODE="python"
    status "OK" "Python fallback ready: $PYTHON_VERSION"
  else
    block "No native binary and python3 is missing" "Publish GitHub release assets or install Python 3.8+."
  fi
fi

if [ "$RUNTIME_MODE" = "binary" ]; then
  "$RUNTIME_PATH" --preflight --no-color --release-asset-url "$RELEASE_ASSET_URL" --enroll-url "$ENROLL_URL" --ingest-url "$INGEST_URL" --claude-dir "$CLAUDE_DIR" || block "Agent preflight failed" "Fix the blocked item above, then rerun."
  if [ -n "${COLLECTOR_TOKEN:-}" ]; then
    "$RUNTIME_PATH" --register --ingest-url "$INGEST_URL" --collector-token "$COLLECTOR_TOKEN" --org-id "$ORG_ID" --account-label "$ACCOUNT_LABEL" --collector-label "${COLLECTOR_LABEL:-$(hostname)}" --claude-dir "$CLAUDE_DIR" --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
  else
    "$RUNTIME_PATH" --enroll --enroll-url "$ENROLL_URL" --org-id "$ORG_ID" --account-label "$ACCOUNT_LABEL" --collector-label "${COLLECTOR_LABEL:-$(hostname)}" --claude-dir "$CLAUDE_DIR" --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
  fi
else
  "$PYTHON_BIN" "$SCRIPT_PATH" --preflight --no-color --release-asset-url "$COLLECTOR_URL" --enroll-url "$ENROLL_URL" --ingest-url "$INGEST_URL" --claude-dir "$CLAUDE_DIR" || block "Agent preflight failed" "Fix the blocked item above, then rerun."
  if [ -n "${COLLECTOR_TOKEN:-}" ]; then
    "$PYTHON_BIN" "$SCRIPT_PATH" --register --ingest-url "$INGEST_URL" --collector-token "$COLLECTOR_TOKEN" --org-id "$ORG_ID" --account-label "$ACCOUNT_LABEL" --collector-label "${COLLECTOR_LABEL:-$(hostname)}" --claude-dir "$CLAUDE_DIR" --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
  else
    "$PYTHON_BIN" "$SCRIPT_PATH" --enroll --enroll-url "$ENROLL_URL" --org-id "$ORG_ID" --account-label "$ACCOUNT_LABEL" --collector-label "${COLLECTOR_LABEL:-$(hostname)}" --claude-dir "$CLAUDE_DIR" --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
  fi
fi

case "$OS_NAME" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.internal.claude-usage-agent.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    if [ "$RUNTIME_MODE" = "binary" ]; then
      PROGRAM="$BIN_PATH"
      EXTRA_ARG=""
    else
      PROGRAM="$PYTHON_BIN"
      EXTRA_ARG="<string>$SCRIPT_PATH</string>"
    fi
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.internal.claude-usage-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROGRAM</string>
    $EXTRA_ARG
    <string>--sync</string>
    <string>--days</string>
    <string>90</string>
  </array>
  <key>StartInterval</key>
  <integer>$((SYNC_INTERVAL_MINUTES * 60))</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/sync.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/sync.err</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" >/dev/null 2>&1 || true
    launchctl load "$PLIST" || block "LaunchAgent install failed" "Check macOS LaunchAgent permissions."
    status "OK" "Scheduler installed: LaunchAgent"
    ;;
  Linux)
    if command -v systemctl >/dev/null 2>&1; then
      USER_SYSTEMD="$HOME/.config/systemd/user"
      mkdir -p "$USER_SYSTEMD"
      if [ "$RUNTIME_MODE" = "binary" ]; then
        EXEC_START="$BIN_PATH --sync --days 90"
      else
        EXEC_START="$PYTHON_BIN $SCRIPT_PATH --sync --days 90"
      fi
      cat > "$USER_SYSTEMD/claude-usage-agent.service" <<EOF
[Unit]
Description=Claude Usage Agent sync

[Service]
Type=oneshot
ExecStart=$EXEC_START
StandardOutput=append:$LOG_DIR/sync.log
StandardError=append:$LOG_DIR/sync.err
EOF
      cat > "$USER_SYSTEMD/claude-usage-agent.timer" <<EOF
[Unit]
Description=Run Claude Usage Agent every $SYNC_INTERVAL_MINUTES minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=${SYNC_INTERVAL_MINUTES}min
Persistent=true

[Install]
WantedBy=timers.target
EOF
      systemctl --user daemon-reload
      systemctl --user enable --now claude-usage-agent.timer || block "systemd timer install failed" "Check user systemd availability."
      status "OK" "Scheduler installed: systemd user timer"
    elif command -v crontab >/dev/null 2>&1; then
      if [ "$RUNTIME_MODE" = "binary" ]; then
        CRON_CMD="$BIN_PATH --sync --days 90"
      else
        CRON_CMD="$PYTHON_BIN $SCRIPT_PATH --sync --days 90"
      fi
      CRON_LINE="*/$SYNC_INTERVAL_MINUTES * * * * $CRON_CMD >> $LOG_DIR/sync.log 2>> $LOG_DIR/sync.err"
      (crontab -l 2>/dev/null | grep -v "claude-usage-agent\\|claude_usage_analyzer.py --sync" || true; echo "$CRON_LINE") | crontab -
      status "OK" "Scheduler installed: cron"
    else
      block "No supported scheduler found" "Install systemd user support or cron."
    fi
    ;;
esac

if [ "$RUNTIME_MODE" = "binary" ]; then
  "$BIN_PATH" --sync --days 90 || true
  "$BIN_PATH" --status
else
  "$PYTHON_BIN" "$SCRIPT_PATH" --sync --days 90 || true
  "$PYTHON_BIN" "$SCRIPT_PATH" --status
fi
