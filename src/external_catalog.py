from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Iterator

from classify_rules import classify_file


PATH_KEYS = ("full_path", "path", "file_path", "filepath", "absolute_path")
NAME_KEYS = ("file_name", "name", "filename")
SIZE_KEYS = ("size_bytes", "size", "bytes")
MODIFIED_KEYS = ("modified_at", "mtime", "modified", "last_modified")
CREATED_KEYS = ("created_at", "ctime", "created")
PARENT_KEYS = ("parent_folder", "parent", "folder")
EXT_KEYS = ("extension", "ext", "suffix")


def iter_external_catalog_records(catalog_path: Path, root_hint: str = "") -> Iterator[dict[str, Any]]:
    """Yield catalog-only records without touching source files.

    The bridge is intentionally tolerant of column naming differences because
    Trancend_AI_Index is generated independently. Catalog rows are used only as
    fallback evidence when the source path was not already scanned directly.
    """
    if not catalog_path.exists():
        logging.warning("External catalog not found: %s", catalog_path)
        return

    with catalog_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            full_path = _first(row, PATH_KEYS)
            if not full_path:
                continue
            path = Path(full_path)
            file_name = _first(row, NAME_KEYS) or path.name
            extension = (_first(row, EXT_KEYS) or path.suffix).lower()
            parent_folder = _first(row, PARENT_KEYS) or path.parent.name
            size_bytes = _safe_int(_first(row, SIZE_KEYS))

            record: dict[str, Any] = {
                "file_name": file_name,
                "extension": extension,
                "size_bytes": size_bytes,
                "created_at": _first(row, CREATED_KEYS),
                "modified_at": _first(row, MODIFIED_KEYS),
                "full_path": str(path),
                "parent_folder": parent_folder,
                "source_root": root_hint or str(path.anchor or "/"),
                "source_role": "index_catalog_fallback",
                "depth": max(0, len(path.parts) - 1),
                "sha256": "",
                "partial_hash": "",
                "duplicate_key": "",
                "duplicate_group": "",
                "project_candidates": [],
                "person_candidates": [],
                "organization_candidates": [],
                "event_candidates": [],
                "year_candidates": [],
                "media_type_candidate": "",
                "importance_candidate": "",
                "tag_candidates": [],
                "generated_tags": ["Trancend_AI_Index", "catalog_fallback"],
                "ai_category": "archive_index",
                "ai_subcategory": "external_catalog",
                "ai_confidence": 0.8,
                "ai_reason": "Imported from Trancend_AI_Index file_catalog.csv without reading or changing source file.",
                "classifier": "external_catalog_bridge",
                "text_excerpt": "",
                "ocr_text": "",
                "ocr_status": "catalog_only",
                "duration_seconds": "",
                "width": "",
                "height": "",
                "codec": "",
                "technical_metadata_json": "{}",
                "error": "",
            }
            classified = classify_file(record)
            for key, value in classified.items():
                if value not in (None, "", [], {}):
                    record[key] = value
            record["source_role"] = "index_catalog_fallback"
            tags = list(record.get("generated_tags") or [])
            for tag in ("Trancend_AI_Index", "catalog_fallback"):
                if tag not in tags:
                    tags.append(tag)
            record["generated_tags"] = tags
            yield record


def _first(row: dict[str, str], keys: tuple[str, ...]) -> str:
    normalized = {str(key).strip().casefold(): (value or "").strip() for key, value in row.items() if key is not None}
    for key in keys:
        value = normalized.get(key.casefold(), "")
        if value:
            return value
    return ""


def _safe_int(value: str) -> int:
    try:
        return int(float(value)) if value else 0
    except (TypeError, ValueError):
        return 0
