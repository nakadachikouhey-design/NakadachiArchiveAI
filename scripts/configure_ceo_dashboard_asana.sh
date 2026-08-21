#!/bin/zsh
set -e

ENV_DIR="$HOME/.config/kio-node"
ENV_PATH="$ENV_DIR/env"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ENV_DIR"

if [ ! -f "$ENV_PATH" ]; then
  touch "$ENV_PATH"
  chmod 600 "$ENV_PATH"
fi

print -n "Asana Personal Access Token: "
read -s ASANA_TOKEN
print ""

if [ -z "$ASANA_TOKEN" ]; then
  echo "Token was empty; no changes made." >&2
  exit 2
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

awk -v token="$ASANA_TOKEN" '
  BEGIN { replaced=0 }
  /^ASANA_ACCESS_TOKEN=/ {
    print "ASANA_ACCESS_TOKEN=\"" token "\""
    replaced=1
    next
  }
  { print }
  END {
    if (!replaced) print "ASANA_ACCESS_TOKEN=\"" token "\""
  }
' "$ENV_PATH" > "$TMP_FILE"

mv "$TMP_FILE" "$ENV_PATH"
chmod 600 "$ENV_PATH"
trap - EXIT

if ! grep -q '^KIO_CEO_DASHBOARD_ENABLED=' "$ENV_PATH"; then
  print 'KIO_CEO_DASHBOARD_ENABLED="1"' >> "$ENV_PATH"
fi
if ! grep -q '^KIO_CEO_DASHBOARD_REFRESH_SECONDS=' "$ENV_PATH"; then
  print 'KIO_CEO_DASHBOARD_REFRESH_SECONDS="1800"' >> "$ENV_PATH"
fi

set -a
source "$ENV_PATH"
set +a

cd "$PROJECT_DIR"
python3 -B scripts/sync_ceo_dashboard_asana.py

echo "CEO Dashboard Asana connection verified."
echo "Token stored only in: $ENV_PATH"
echo "Run scripts/install_kio_node_agent.sh to activate periodic refresh if needed."
