#!/bin/zsh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${NAKADACHI_PYTHON:-/opt/homebrew/bin/python3}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi
BIN_DIR="$HOME/NakadachiArchiveAI/bin"
WRAPPER_PATH="$BIN_DIR/nakadachi_archive_auto_update.sh"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/com.nakadachi.archive-ai.autoupdate.plist"

mkdir -p "$PLIST_DIR"
mkdir -p "$BIN_DIR"

/bin/cat > "$WRAPPER_PATH" <<WRAPPER
#!/bin/zsh
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -B src/auto_update.py run --once
WRAPPER

chmod +x "$WRAPPER_PATH"

/bin/cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.nakadachi.archive-ai.autoupdate</string>
  <key>ProgramArguments</key>
  <array>
    <string>$WRAPPER_PATH</string>
  </array>
  <key>StartInterval</key>
  <integer>21600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/NakadachiArchiveAI/logs/auto_update.out.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/NakadachiArchiveAI/logs/auto_update.err.log</string>
</dict>
</plist>
PLIST

mkdir -p "$HOME/NakadachiArchiveAI/logs"
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"
echo "Installed LaunchAgent: $PLIST_PATH"
echo "Installed wrapper: $WRAPPER_PATH"
