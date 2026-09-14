#!/usr/bin/env sh
# Installs the Claude usage agent for the current user.
# Phases: check_system -> install_runtime -> preflight -> enroll_agent -> install_scheduler -> first_sync
set -eu

APP_DIR="${CLAUDE_USAGE_AGENT_DIR:-$HOME/.claude-usage-agent}"
BIN_PATH="$APP_DIR/claude-usage-agent"
SRC_DIR="$APP_DIR/src"
LOG_DIR="$APP_DIR/logs"
SYNC_INTERVAL_MINUTES="${SYNC_INTERVAL_MINUTES:-30}"
SYNC_DAYS="${SYNC_DAYS:-90}"
RELEASE_BASE_URL="${RELEASE_BASE_URL:-https://github.com/sarathkumar365/c-usage-anlyst/releases/latest/download}"
SOURCE_ARCHIVE_URL="${SOURCE_ARCHIVE_URL:-https://codeload.github.com/sarathkumar365/c-usage-anlyst/tar.gz/refs/heads/main}"
INGEST_URL="${INGEST_URL:-https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest}"
ENROLL_URL="${ENROLL_URL:-https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll}"
ORG_ID="${ORG_ID:-team-main}"
ACCOUNT_LABEL="${ACCOUNT_LABEL:-$(id -un 2>/dev/null || whoami 2>/dev/null || hostname)}"
COLLECTOR_LABEL="${COLLECTOR_LABEL:-$(hostname)}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
# Test hook: skip writing a LaunchAgent/systemd/cron entry.
SKIP_SCHEDULER="${SKIP_SCHEDULER:-}"

RUNTIME_MODE=""
PYTHON_BIN=""
PROBE_URL=""

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
  curl -fsSL --max-time 12 -X "${2:-GET}" "$1" >/dev/null 2>&1
}

run_agent() {
  if [ "$RUNTIME_MODE" = "binary" ]; then
    "$BIN_PATH" "$@"
  else
    "$PYTHON_BIN" "$SRC_DIR/claude_usage_analyzer.py" "$@"
  fi
}

# Space-joined command for schedulers that take a single command line.
agent_command_line() {
  if [ "$RUNTIME_MODE" = "binary" ]; then
    printf '%s' "$BIN_PATH"
  else
    printf '%s %s' "$PYTHON_BIN" "$SRC_DIR/claude_usage_analyzer.py"
  fi
}

check_system() {
  echo
  echo "SYSTEM CHECK"
  echo "------------------------------------"
  need_cmd curl
  need_cmd uname
  need_cmd tar

  OS_NAME="$(uname -s)"
  ARCH_NAME="$(uname -m)"
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

  if [ -z "${COLLECTOR_TOKEN:-}" ] && [ -z "${ENROLLMENT_SECRET:-}" ]; then
    block "ENROLLMENT_SECRET is not set" "Rerun as: curl -fsSL <install-url> | ENROLLMENT_SECRET=<secret> sh"
  fi
}

install_python_source() {
  command -v python3 >/dev/null 2>&1 || block "No native binary and python3 is missing" "Publish GitHub release assets or install Python 3.8+."
  PYTHON_BIN="$(command -v python3)"
  PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' || block "Python 3.8+ is required for fallback, found $PYTHON_VERSION" "Install Python 3.8+ or wait for native release assets."

  tmp="$(mktemp -d)"
  curl -fsSL "$SOURCE_ARCHIVE_URL" | tar -xzf - -C "$tmp" || block "Could not download agent source" "Check internet access and rerun."
  extracted=""
  for dir in "$tmp"/*/; do
    [ -f "${dir}claude_usage_analyzer.py" ] && extracted="$dir"
  done
  [ -n "$extracted" ] || block "Agent source archive is missing claude_usage_analyzer.py" "Check SOURCE_ARCHIVE_URL."
  rm -rf "$SRC_DIR" "$APP_DIR/claude_usage" "$APP_DIR/claude_usage_analyzer.py"
  mkdir -p "$SRC_DIR"
  cp "${extracted}claude_usage_analyzer.py" "$SRC_DIR/"
  cp -R "${extracted}claude_usage" "$SRC_DIR/"
  rm -rf "$tmp"
  RUNTIME_MODE="python"
  PROBE_URL="$SOURCE_ARCHIVE_URL"
  status "OK" "Python fallback ready: $PYTHON_VERSION"
}

install_runtime() {
  if curl -fsSL --max-time 60 "$RELEASE_ASSET_URL" -o "$BIN_PATH"; then
    chmod 700 "$BIN_PATH"
    RUNTIME_MODE="binary"
    PROBE_URL="$RELEASE_ASSET_URL"
    status "OK" "Downloaded native agent: $ASSET_NAME"
  else
    rm -f "$BIN_PATH"
    warn "Native release asset unavailable, trying Python fallback"
    install_python_source
  fi
}

preflight() {
  run_agent --preflight --no-color --release-asset-url "$PROBE_URL" --enroll-url "$ENROLL_URL" --ingest-url "$INGEST_URL" --claude-dir "$CLAUDE_DIR" \
    || block "Agent preflight failed" "Fix the blocked item above, then rerun."
}

enroll_agent() {
  set -- --org-id "$ORG_ID" --account-label "$ACCOUNT_LABEL" --collector-label "$COLLECTOR_LABEL" --claude-dir "$CLAUDE_DIR" --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
  if [ -n "${COLLECTOR_TOKEN:-}" ]; then
    run_agent --register --ingest-url "$INGEST_URL" --collector-token "$COLLECTOR_TOKEN" "$@"
  else
    run_agent --enroll --enroll-url "$ENROLL_URL" "$@"
  fi
}

install_launchagent() {
  plist="$HOME/Library/LaunchAgents/com.internal.claude-usage-agent.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  if [ "$RUNTIME_MODE" = "binary" ]; then
    program_args="<string>$BIN_PATH</string>"
  else
    program_args="<string>$PYTHON_BIN</string>
    <string>$SRC_DIR/claude_usage_analyzer.py</string>"
  fi
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.internal.claude-usage-agent</string>
  <key>ProgramArguments</key>
  <array>
    $program_args
    <string>--sync</string>
    <string>--days</string>
    <string>$SYNC_DAYS</string>
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
  launchctl unload "$plist" >/dev/null 2>&1 || true
  launchctl load "$plist" || block "LaunchAgent install failed" "Check macOS LaunchAgent permissions."
  status "OK" "Scheduler installed: LaunchAgent"
}

install_systemd_timer() {
  user_systemd="$HOME/.config/systemd/user"
  mkdir -p "$user_systemd"
  cat > "$user_systemd/claude-usage-agent.service" <<EOF
[Unit]
Description=Claude Usage Agent sync

[Service]
Type=oneshot
ExecStart=$(agent_command_line) --sync --days $SYNC_DAYS
StandardOutput=append:$LOG_DIR/sync.log
StandardError=append:$LOG_DIR/sync.err
EOF
  cat > "$user_systemd/claude-usage-agent.timer" <<EOF
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
}

install_cron() {
  cron_line="*/$SYNC_INTERVAL_MINUTES * * * * $(agent_command_line) --sync --days $SYNC_DAYS >> $LOG_DIR/sync.log 2>> $LOG_DIR/sync.err"
  (crontab -l 2>/dev/null | grep -v "claude-usage-agent\\|claude_usage_analyzer.py --sync" || true; echo "$cron_line") | crontab -
  status "OK" "Scheduler installed: cron"
}

install_scheduler() {
  if [ -n "$SKIP_SCHEDULER" ]; then
    warn "Scheduler install skipped (SKIP_SCHEDULER set)"
    return
  fi
  case "$OS_NAME" in
    Darwin) install_launchagent ;;
    Linux)
      if command -v systemctl >/dev/null 2>&1; then
        install_systemd_timer
      elif command -v crontab >/dev/null 2>&1; then
        install_cron
      else
        block "No supported scheduler found" "Install systemd user support or cron."
      fi
      ;;
  esac
}

first_sync() {
  run_agent --sync --days "$SYNC_DAYS" || true
  run_agent --status
}

check_system
install_runtime
preflight
enroll_agent
install_scheduler
first_sync
