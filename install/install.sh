#!/usr/bin/env sh
set -eu

APP_DIR="${CLAUDE_USAGE_AGENT_DIR:-$HOME/.claude-usage-agent}"
SCRIPT_PATH="$APP_DIR/claude_usage_analyzer.py"
LOG_DIR="$APP_DIR/logs"
SYNC_INTERVAL_MINUTES="${SYNC_INTERVAL_MINUTES:-30}"
COLLECTOR_URL="${COLLECTOR_URL:-https://raw.githubusercontent.com/sarathkumar365/c-usage-anlyst/main/claude_usage_analyzer.py}"
INGEST_URL="${INGEST_URL:-https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest}"
ENROLL_URL="${ENROLL_URL:-https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll}"
ORG_ID="${ORG_ID:-team-main}"
ACCOUNT_LABEL="${ACCOUNT_LABEL:-$(id -un 2>/dev/null || whoami 2>/dev/null || hostname)}"

mkdir -p "$APP_DIR" "$LOG_DIR"

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$COLLECTOR_URL" -o "$SCRIPT_PATH"
elif command -v python3 >/dev/null 2>&1; then
  python3 -c "import urllib.request; urllib.request.urlretrieve('$COLLECTOR_URL', '$SCRIPT_PATH')"
else
  echo "curl or python3 is required to download the collector." >&2
  exit 2
fi

chmod 700 "$APP_DIR"
chmod 600 "$SCRIPT_PATH"

if [ -n "${COLLECTOR_TOKEN:-}" ]; then
  python3 "$SCRIPT_PATH" --register \
    --ingest-url "$INGEST_URL" \
    --collector-token "$COLLECTOR_TOKEN" \
    --org-id "${ORG_ID:-}" \
    --account-label "${ACCOUNT_LABEL:-}" \
    --collector-label "${COLLECTOR_LABEL:-$(hostname)}" \
    --claude-dir "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" \
    --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
else
  python3 "$SCRIPT_PATH" --enroll \
    --enroll-url "$ENROLL_URL" \
    --org-id "${ORG_ID:-}" \
    --account-label "${ACCOUNT_LABEL:-}" \
    --collector-label "${COLLECTOR_LABEL:-$(hostname)}" \
    --claude-dir "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" \
    --sync-interval-minutes "$SYNC_INTERVAL_MINUTES"
fi

case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.internal.claude-usage-agent.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.internal.claude-usage-agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$SCRIPT_PATH</string>
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
    launchctl load "$PLIST"
    ;;
  Linux)
    if command -v systemctl >/dev/null 2>&1; then
      USER_SYSTEMD="$HOME/.config/systemd/user"
      mkdir -p "$USER_SYSTEMD"
      cat > "$USER_SYSTEMD/claude-usage-agent.service" <<EOF
[Unit]
Description=Claude Usage Agent sync

[Service]
Type=oneshot
ExecStart=/usr/bin/env python3 $SCRIPT_PATH --sync --days 90
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
      systemctl --user enable --now claude-usage-agent.timer
    else
      CRON_LINE="*/$SYNC_INTERVAL_MINUTES * * * * /usr/bin/env python3 $SCRIPT_PATH --sync --days 90 >> $LOG_DIR/sync.log 2>> $LOG_DIR/sync.err"
      (crontab -l 2>/dev/null | grep -v "claude_usage_analyzer.py --sync" || true; echo "$CRON_LINE") | crontab -
    fi
    ;;
  *)
    echo "Unsupported Unix OS for automatic scheduling. Run manually: python3 $SCRIPT_PATH --sync" >&2
    ;;
esac

python3 "$SCRIPT_PATH" --sync --days 90 || true
python3 "$SCRIPT_PATH" --status
