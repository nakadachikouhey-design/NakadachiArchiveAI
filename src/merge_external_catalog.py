from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import search_archive
from classify_rules import classify_file


DEFAULT_CATALOG = "/Users/phikohey/Documents/AI・自動化研究所/Trancend_AI_Index/file_catalog.csv"
DEFAULT_ROOT = "/Volumes/Trancend"

PATH_KEYS = ("full_path", "path", "file_path", "filepath", "absolute_path")
NAME_KEYS = ("file_name", "name", "filename")
SIZE_KEYS = ("size_bytes", "size", "bytes")
MODIFIED_KEYS = ("modified_at", "mtime", "modified", "last_modified")
CREATED_KEYS = ("created_at", "ctime", "created")
PARENT_KEYS = ("parent_folder", "parent", "folder")
EXT_KEYS = ("extension", "ext", "suffix")


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge a read-only external file catalog into the latest archive SQLite index.")
    parser.add_argument("--db", default=search_archive.DEFAULT_DB)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--source-root", default=DEFAULT_ROOT)
    args = parser.parse_args()

    db_path = search_archive.resolve_db_path(args.db)
    catalog_path = Path(args.catalog).expanduser()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1
    if not catalog_path.exists():
        print(f"External catalog not found; skipped: {catalog_path}")
        return 0

    added = merge_catalog(db_path, catalog_path, args.source_root)
    print(f"External catalog merged: {added} fallback records added from {catalog_path}")
    return 0


def merge_catalog(db_path: Path, catalog_path: Path, source_root: str) -> int:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    added = 0
    try:
        with catalog_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                record = catalog_record(row, source_root)
                if not record:
                    continue
                exists = db.execute("SELECT 1 FROM files WHERE full_path = ? LIMIT 1", (record["full_path"],)).fetchone()
                if exists:
                    continue
                insert_record(db, record)
                added += 1
                if added % 1000 == 0:
                    db.commit()
        db.commit()
        rebuild_fts(db)
        db.commit()
    finally:
        db.close()
    return added


def catalog_record(row: dict[str, str], source_root: str) -> dict[str, Any] | None:
    full_path = first(row, PATH_KEYS)
    if not full_path:
        return None
    path = Path(full_path)
    record: dict[str, Any] = {
        "file_name": first(row, NAME_KEYS) or path.name,
        "extension": (first(row, EXT_KEYS) or path.suffix).lower(),
        "size_bytes": safe_int(first(row, SIZE_KEYS)),
        "created_at": first(row, CREATED_KEYS),
        "modified_at": first(row, MODIFIED_KEYS),
        "full_path": str(path),
        "parent_folder": first(row, PARENT_KEYS) or path.parent.name,
        "source_root": source_root,
        "source_role": "index_catalog_fallback",
        "depth": max(0, len(path.parts) - len(Path(source_root).parts)),
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
        "ai_reason": "Catalog fallback imported from Trancend_AI_Index; source file was not read or changed.",
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
    return record


def insert_record(db: sqlite3.Connection, record: dict[str, Any]) -> None:
    columns = [row[1] for row in db.execute("PRAGMA table_info(files)") if row[1] != "id"]
    values = [db_value(record.get(column)) for column in columns]
    placeholders = ", ".join("?" for _ in columns)
    db.execute(f"INSERT INTO files ({', '.join(columns)}) VALUES ({placeholders})", values)


def rebuild_fts(db: sqlite3.Connection) -> None:
    try:
        db.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
    except sqlite3.OperationalError as exc:
        logging.debug("FTS rebuild unavailable: %s", exc)


def db_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value if value is not None else ""


def first(row: dict[str, str], keys: tuple[str, ...]) -> str:
    normalized = {str(key).strip().casefold(): (value or "").strip() for key, value in row.items() if key is not None}
    for key in keys:
        value = normalized.get(key.casefold(), "")
        if value:
            return value
    return ""


def safe_int(value: str) -> int:
    try:
        return int(float(value)) if value else 0
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
