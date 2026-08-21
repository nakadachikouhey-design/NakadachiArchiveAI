#!/bin/zsh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${NAKADACHI_PYTHON:-/opt/homebrew/bin/python3}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

BIN_DIR="$HOME/NakadachiArchiveAI/bin"
LOG_DIR="$HOME/NakadachiArchiveAI/logs"
STATE_DIR="$HOME/NakadachiArchiveAI/agent_state"
PLIST_DIR="$HOME/Library/LaunchAgents"
ENV_DIR="$HOME/.config/kio-node"
ENV_PATH="$ENV_DIR/env"
WRAPPER_PATH="$BIN_DIR/kio_local_node_cycle.sh"
PLIST_PATH="$PLIST_DIR/com.kio.local-ai-node.plist"
HYBRID_MIGRATION_MARKER="$STATE_DIR/hybrid_cloud_control_v1"

mkdir -p "$BIN_DIR" "$LOG_DIR" "$STATE_DIR" "$PLIST_DIR" "$ENV_DIR"

if [ ! -f "$ENV_PATH" ]; then
  /bin/cat > "$ENV_PATH" <<'ENV'
# KIO Local AI Node v5 / Hybrid Cloud Control
# Cloud AI is the decision layer. The Mac mini monitors and executes allowlisted jobs.
KIO_MONITORED_REPOS="nakadachikouhey-design/NakadachiArchiveAI"
KIO_ACTION_MAX_RETRIES="3"
KIO_RETRY_DELAY_SECONDS="10"

# Hybrid defaults: observe locally, but do not perform autonomous mutating actions.
KIO_AUTO_RETRY_GITHUB_ACTIONS="0"
KIO_ENGINEERING_LOOP_ENABLED="0"
KIO_ENGINEERING_AUTO_REPAIR="0"

# Deterministic repair capability remains available only when explicitly enabled.
KIO_ENGINEERING_CODE_REPAIR_ENABLED="1"
KIO_ENGINEERING_MAX_CODE_REPAIRS_PER_CYCLE="1"
KIO_CODEX_REPAIR_TIMEOUT_SECONDS="1200"
KIO_SLACK_WEBHOOK_URL=""

# CEO Dashboard. Asana remains the source of truth.
ASANA_ACCESS_TOKEN=""
KIO_CEO_DASHBOARD_ENABLED="1"
KIO_CEO_DASHBOARD_REFRESH_SECONDS="1800"
ENV
  chmod 600 "$ENV_PATH"
else
  grep -q '^ASANA_ACCESS_TOKEN=' "$ENV_PATH" || print '\n# CEO Dashboard Asana token (never commit)\nASANA_ACCESS_TOKEN=""' >> "$ENV_PATH"
  grep -q '^KIO_CEO_DASHBOARD_ENABLED=' "$ENV_PATH" || print 'KIO_CEO_DASHBOARD_ENABLED="1"' >> "$ENV_PATH"
  grep -q '^KIO_CEO_DASHBOARD_REFRESH_SECONDS=' "$ENV_PATH" || print 'KIO_CEO_DASHBOARD_REFRESH_SECONDS="1800"' >> "$ENV_PATH"
  chmod 600 "$ENV_PATH"
fi

# One-time migration from previous autonomous defaults to hybrid cloud control.
# Preserve unrelated settings, including CEO Dashboard / Asana configuration.
if [ ! -f "$HYBRID_MIGRATION_MARKER" ]; then
  /usr/bin/sed -i '' 's/^KIO_AUTO_RETRY_GITHUB_ACTIONS=.*/KIO_AUTO_RETRY_GITHUB_ACTIONS="0"/' "$ENV_PATH" || true
  /usr/bin/sed -i '' 's/^KIO_ENGINEERING_LOOP_ENABLED=.*/KIO_ENGINEERING_LOOP_ENABLED="0"/' "$ENV_PATH" || true
  /usr/bin/sed -i '' 's/^KIO_ENGINEERING_AUTO_REPAIR=.*/KIO_ENGINEERING_AUTO_REPAIR="0"/' "$ENV_PATH" || true
  touch "$HYBRID_MIGRATION_MARKER"
fi

/bin/cat > "$WRAPPER_PATH" <<WRAPPER
#!/bin/zsh
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if [ -f "$ENV_PATH" ]; then
  set -a
  source "$ENV_PATH"
  set +a
fi

LOCK_DIR="$STATE_DIR/node_cycle.lock"
LOCK_PID_FILE="\$LOCK_DIR/pid"
DASHBOARD_STAMP="$STATE_DIR/ceo_dashboard_last_sync"

acquire_lock() {
  if mkdir "\$LOCK_DIR" 2>/dev/null; then
    echo "\$\$" > "\$LOCK_PID_FILE"
    return 0
  fi
  local existing_pid=""
  if [ -f "\$LOCK_PID_FILE" ]; then
    existing_pid="\$(cat "\$LOCK_PID_FILE" 2>/dev/null || true)"
  fi
  if [[ "\$existing_pid" == <-> ]] && kill -0 "\$existing_pid" 2>/dev/null; then
    echo "KIO local node cycle already running (pid=\$existing_pid); skipping overlapping run."
    exit 0
  fi
  rm -rf "\$LOCK_DIR"
  if mkdir "\$LOCK_DIR" 2>/dev/null; then
    echo "\$\$" > "\$LOCK_PID_FILE"
    return 0
  fi
  echo "KIO local node cycle lock could not be acquired; skipping run."
  exit 0
}

cleanup_lock() {
  if [ -f "\$LOCK_PID_FILE" ] && [ "\$(cat "\$LOCK_PID_FILE" 2>/dev/null || true)" = "\$\$" ]; then
    rm -rf "\$LOCK_DIR"
  fi
}

refresh_ceo_dashboard_if_due() {
  if [ "\${KIO_CEO_DASHBOARD_ENABLED:-1}" != "1" ] || [ -z "\${ASANA_ACCESS_TOKEN:-}" ]; then
    return 0
  fi
  local interval="\${KIO_CEO_DASHBOARD_REFRESH_SECONDS:-1800}"
  local now="\$(date +%s)"
  local last="0"
  if [ -f "\$DASHBOARD_STAMP" ]; then
    last="\$(cat "\$DASHBOARD_STAMP" 2>/dev/null || echo 0)"
  fi
  if ! [[ "\$last" == <-> ]]; then last="0"; fi
  if (( now - last < interval )); then return 0; fi

  echo "Refreshing KIO CEO Dashboard from Asana..."
  set +e
  "$PYTHON_BIN" -B scripts/sync_ceo_dashboard_asana.py
  local dashboard_status=\$?
  set -e
  if [ "\$dashboard_status" -eq 0 ]; then
    echo "\$now" > "\$DASHBOARD_STAMP"
  else
    echo "CEO Dashboard refresh failed (status=\$dashboard_status); local node continues." >&2
  fi
  return 0
}

acquire_lock
trap cleanup_lock EXIT INT TERM HUP
cd "$PROJECT_DIR"
set +e
"$PYTHON_BIN" -B src/kio_node_agent.py cycle
status=\$?
set -e
refresh_ceo_dashboard_if_due
cleanup_lock
trap - EXIT INT TERM HUP
exit \$status
WRAPPER
chmod +x "$WRAPPER_PATH"

/bin/cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.kio.local-ai-node</string>
  <key>ProgramArguments</key><array><string>$WRAPPER_PATH</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/kio_local_node.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/kio_local_node.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/com.kio.local-ai-node"

echo "Installed KIO local AI node v5 / Hybrid Cloud Control: $PLIST_PATH"
echo "Cycle wrapper: $WRAPPER_PATH"
echo "Runtime env: $ENV_PATH"
echo "Heartbeat: $HOME/NakadachiArchiveAI/agent_state/heartbeat.json"
echo "Autonomous CI retry / engineering repair defaults: disabled"
echo "CEO Dashboard refresh: every 30 minutes when ASANA_ACCESS_TOKEN is configured"
if grep -q '^ASANA_ACCESS_TOKEN=""' "$ENV_PATH"; then
  echo "ACTION REQUIRED ONCE: zsh scripts/configure_ceo_dashboard_asana.sh"
fi
