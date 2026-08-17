#!/bin/zsh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${NAKADACHI_PYTHON:-/opt/homebrew/bin/python3}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

BIN_DIR="$HOME/NakadachiArchiveAI/bin"
LOG_DIR="$HOME/NakadachiArchiveAI/logs"
PLIST_DIR="$HOME/Library/LaunchAgents"
ENV_DIR="$HOME/.config/kio-node"
ENV_PATH="$ENV_DIR/env"
WRAPPER_PATH="$BIN_DIR/kio_local_node_cycle.sh"
PLIST_PATH="$PLIST_DIR/com.kio.local-ai-node.plist"

mkdir -p "$BIN_DIR" "$LOG_DIR" "$PLIST_DIR" "$ENV_DIR"

if [ ! -f "$ENV_PATH" ]; then
  /bin/cat > "$ENV_PATH" <<'ENV'
# KIO Local AI Node v2
# Comma-separated GitHub repositories to monitor.
KIO_MONITORED_REPOS="nakadachikouhey-design/NakadachiArchiveAI"

# Local action retry policy.
KIO_ACTION_MAX_RETRIES="3"
KIO_RETRY_DELAY_SECONDS="10"

# Re-run each failed GitHub Actions workflow at most once per run id.
KIO_AUTO_RETRY_GITHUB_ACTIONS="1"

# Slack Incoming Webhook. Leave blank until configured.
KIO_SLACK_WEBHOOK_URL=""
ENV
  chmod 600 "$ENV_PATH"
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
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -B src/kio_node_agent.py cycle
WRAPPER
chmod +x "$WRAPPER_PATH"

/bin/cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.kio.local-ai-node</string>
  <key>ProgramArguments</key>
  <array>
    <string>$WRAPPER_PATH</string>
  </array>
  <key>StartInterval</key>
  <integer>600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/kio_local_node.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/kio_local_node.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/com.kio.local-ai-node"

echo "Installed KIO local AI node v2: $PLIST_PATH"
echo "Cycle wrapper: $WRAPPER_PATH"
echo "Runtime env: $ENV_PATH"
echo "Heartbeat: $HOME/NakadachiArchiveAI/agent_state/heartbeat.json"
