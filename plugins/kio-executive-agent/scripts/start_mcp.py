#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


def find_archive_root() -> Path:
    configured = os.environ.get("NAKADACHI_ARCHIVE_AI_ROOT", "")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    marker = Path.home() / ".config" / "kio-executive-agent" / "archive-root"
    if marker.is_file():
        candidates.append(Path(marker.read_text(encoding="utf-8").strip()).expanduser())
    candidates.extend([
        Path(__file__).resolve().parents[3],
        Path.home() / "NakadachiArchiveAI",
        Path.home() / "Documents" / "NakadachiArchiveAI",
    ])
    for candidate in candidates:
        if (candidate / "src" / "kio_mcp_server.py").is_file():
            return candidate.resolve()
    raise SystemExit("NakadachiArchiveAI root not found. Run scripts/install_kio_plugin.sh from the repository.")


root = find_archive_root()
if "KPS_ROOT" not in os.environ:
    kps_marker = Path.home() / ".config" / "kio-executive-agent" / "kps-root"
    if kps_marker.is_file():
        os.environ["KPS_ROOT"] = kps_marker.read_text(encoding="utf-8").strip()
sys.path.insert(0, str(root / "src"))
from kio_mcp_server import serve

raise SystemExit(serve())
