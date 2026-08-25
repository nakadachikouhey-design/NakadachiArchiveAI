#!/bin/zsh
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DRY_RUN=0
for arg in "$@"; do
  if [ "$arg" = "--dry-run" ]; then
    DRY_RUN=1
  fi
done

python3 -B src/scan_archive.py "$@"
if [ "$DRY_RUN" = "1" ]; then
  exit 0
fi

OUTPUT_DIR="$HOME/NakadachiArchiveAI/output"
LATEST_DB="$(ls -1t "$OUTPUT_DIR"/archive_index*.sqlite 2>/dev/null | head -n 1)"
if [ -z "$LATEST_DB" ]; then
  echo "No archive SQLite database found in $OUTPUT_DIR" >&2
  exit 1
fi

# Merge Trancend_AI_Index as catalog-only fallback metadata. Existing records
# scanned directly from source files always win; source files are never changed.
python3 -B src/merge_external_catalog.py --db "$LATEST_DB"

# Historical Works Discovery uses the newest archive database automatically.
python3 -B src/historical_works_discovery.py
python3 -B src/knowledge_engine.py build --db "$LATEST_DB" --limit 50
python3 -B src/assistant_ai.py build-packs --db "$LATEST_DB" --task all --limit 50
