from __future__ import annotations

import os
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import kio_node_automation as automation

DEFAULT_MAX_RESULTS = 100
MAX_RESULTS_CAP = 500

# Local fallback search must remain broader than the archive index. In particular,
# Final Cut / Logic / GarageBand packages are valid evidence sources even when the
# normal archive scanner excludes them for performance reasons.
TECHNICAL_EXCLUDE_NAMES = {
    ".git",
    ".DS_Store",
    "node_modules",
    "__pycache__",
    ".Trash",
    ".Trashes",
    ".Spotlight-V100",
    ".fseventsd",
    ".TemporaryItems",
    ".DocumentRevisions-V100",
}


def normalise(value: str) -> str:
    """Normalise Japanese/macOS path text for resilient matching."""
    return unicodedata.normalize("NFKC", value).casefold().replace(" ", "")


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            key = str(path.expanduser().resolve(strict=False))
        except OSError:
            key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append(Path(key))
    return result


def discover_storage_roots() -> tuple[list[Path], list[str], list[str]]:
    """Return configured roots plus currently mounted local/cloud storage roots.

    Runtime enumeration is intentional: callers should never have to guess a disk
    spelling such as Transcend vs Trancend. Configured exclusions are returned only
    so absolute NakadachiArchiveAI runtime/output paths can be pruned; production
    media-package exclusions are not inherited by local fallback search.
    """
    configured, excludes, warnings = automation.configured_scan_roots()
    candidates: list[Path] = list(configured)

    volumes = Path("/Volumes")
    if volumes.is_dir():
        try:
            candidates.extend(p for p in volumes.iterdir() if p.is_dir())
        except OSError as exc:
            warnings.append(f"Could not enumerate /Volumes: {exc}")

    cloud_storage = Path.home() / "Library" / "CloudStorage"
    if cloud_storage.is_dir():
        try:
            candidates.extend(p for p in cloud_storage.iterdir() if p.is_dir())
        except OSError as exc:
            warnings.append(f"Could not enumerate CloudStorage: {exc}")

    for fallback in (Path.home() / "Documents", Path.home() / "Google Drive"):
        if fallback.exists():
            candidates.append(fallback)

    roots = [p for p in _unique_paths(candidates) if p.exists()]
    return roots, excludes, warnings


def _excluded(path: Path, excludes: list[str]) -> bool:
    if path.name in TECHNICAL_EXCLUDE_NAMES:
        return True

    # Only honor configured absolute/runtime exclusions. Named project/media
    # exclusions such as "Final Cut Backups.localized" are intentionally ignored.
    path_text = str(path)
    for raw in excludes:
        if not raw or not (raw.startswith("/") or raw.startswith("~")):
            continue
        expanded = os.path.expanduser(raw)
        if path_text == expanded or path_text.startswith(expanded.rstrip("/") + "/"):
            return True
    return False


def _query_tokens(query: str) -> list[str]:
    query = query.strip()
    if not query:
        return []
    parts = [part for part in query.split() if part]
    return [normalise(part) for part in parts] or [normalise(query)]


def _matches(path: Path, tokens: list[str]) -> tuple[bool, str]:
    name = normalise(path.name)
    full = normalise(str(path))
    if all(token in name for token in tokens):
        return True, "name"
    if all(token in full for token in tokens):
        return True, "path"
    return False, ""


def search_local_storage(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    include_files: bool = True,
    include_directories: bool = True,
    extensions: list[str] | None = None,
) -> dict[str, Any]:
    """Search actual local/cloud storage without arbitrary shell execution.

    The fallback searches directory names as well as files. Files with generic names
    still match when a parent directory contains the query, so a project folder such
    as "古い/防災博士" surfaces its IMG_0001.MOV material when extensions are filtered.

    Traversal is breadth-first by design. Project folders near a storage root must be
    discovered before the search descends into large Final Cut/Logic/GarageBand
    packages whose many matching media files could otherwise consume the result cap.
    """
    tokens = _query_tokens(query)
    if not tokens:
        return {"status": "rejected", "message": "query must not be empty", "results": []}

    limit = max(1, min(int(max_results), MAX_RESULTS_CAP))
    roots, excludes, warnings = discover_storage_roots()
    extension_filter = {
        ext.casefold() if str(ext).startswith(".") else f".{str(ext).casefold()}"
        for ext in (extensions or [])
        if str(ext).strip()
    }

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    scanned_entries = 0
    visited_roots: list[str] = []

    for root in roots:
        if len(results) >= limit:
            break
        visited_roots.append(str(root))
        queue = deque([root])
        while queue and len(results) < limit:
            current = queue.popleft()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if len(results) >= limit:
                            break
                        path = Path(entry.path)
                        if _excluded(path, excludes):
                            continue
                        scanned_entries += 1
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                            is_file = entry.is_file(follow_symlinks=False)
                        except OSError as exc:
                            if len(errors) < 30:
                                errors.append(f"{path}: {exc}")
                            continue

                        matched, match_scope = _matches(path, tokens)
                        if is_dir:
                            if include_directories and matched:
                                results.append({
                                    "path": str(path),
                                    "name": path.name,
                                    "type": "directory",
                                    "match_scope": match_scope,
                                    "source_root": str(root),
                                })
                            queue.append(path)
                            continue

                        if not is_file or not include_files:
                            continue
                        if extension_filter and path.suffix.casefold() not in extension_filter:
                            continue
                        if matched:
                            try:
                                stat = entry.stat(follow_symlinks=False)
                                size = stat.st_size
                            except OSError:
                                size = None
                            results.append({
                                "path": str(path),
                                "name": path.name,
                                "type": "file",
                                "extension": path.suffix.casefold(),
                                "size_bytes": size,
                                "match_scope": match_scope,
                                "source_root": str(root),
                            })
            except OSError as exc:
                if len(errors) < 30:
                    errors.append(f"{current}: {exc}")

    results.sort(
        key=lambda item: (
            0 if item.get("match_scope") == "name" else 1,
            0 if item.get("type") == "directory" else 1,
            len(item["path"]),
            item["path"],
        )
    )

    return {
        "status": "ok" if not errors else "partial",
        "query": query,
        "normalised_tokens": tokens,
        "result_count": len(results),
        "max_results": limit,
        "scanned_entries": scanned_entries,
        "roots": visited_roots,
        "warnings": warnings,
        "errors": errors,
        "results": results,
    }
