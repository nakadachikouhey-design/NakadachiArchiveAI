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

# Historical Works Discovery reuses the same read-only scan roots and produces
# evidence candidates for representative-work / career-history reconstruction.
python3 -B src/historical_works_discovery.py
python3 -B src/knowledge_engine.py build --limit 50
python3 -B src/assistant_ai.py build-packs --task all --limit 50
