from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from private_storage import ensure_private_directory, harden_private_tree, secure_file, set_private_umask


CSV_FIELDS = [
    "file_name",
    "extension",
    "size_bytes",
    "created_at",
    "modified_at",
    "full_path",
    "parent_folder",
    "source_root",
    "source_role",
    "depth",
    "sha256",
    "partial_hash",
    "duplicate_key",
    "duplicate_group",
    "project_candidates",
    "person_candidates",
    "organization_candidates",
    "event_candidates",
    "year_candidates",
    "media_type_candidate",
    "importance_candidate",
    "tag_candidates",
    "generated_tags",
    "ai_category",
    "ai_subcategory",
    "ai_confidence",
    "ai_reason",
    "classifier",
    "text_excerpt",
    "ocr_text",
    "ocr_status",
    "duration_seconds",
    "width",
    "height",
    "codec",
    "technical_metadata_json",
    "error",
]


LIST_FIELDS = {
    "project_candidates",
    "person_candidates",
    "organization_candidates",
    "event_candidates",
    "year_candidates",
    "tag_candidates",
    "generated_tags",
}


class ArchiveExporter:
    def __init__(self, output_dir: Path, started_at: datetime, config: dict[str, Any]) -> None:
        set_private_umask()
        self.output_dir = output_dir
        self.started_at = started_at
        self.config = config
        ensure_private_directory(self.output_dir, harden_existing=True)

        self.csv_path, self.json_path, self.summary_path, self.sqlite_path = _output_paths(output_dir, started_at)
        self.duplicates_csv_path = self.csv_path.with_name(self.csv_path.stem.replace("archive_index", "duplicate_report") + ".csv")
        self.duplicates_json_path = self.json_path.with_name(self.json_path.stem.replace("archive_index", "duplicate_report") + ".json")

        self.file_count = 0
        self.total_bytes = 0
        self.error_count = 0
        self.media_counter: Counter[str] = Counter()
        self.extension_counter: Counter[str] = Counter()
        self.project_counter: Counter[str] = Counter()
        self.year_counter: Counter[str] = Counter()
        self.source_role_counter: Counter[str] = Counter()
        self.ai_category_counter: Counter[str] = Counter()
        self.ocr_status_counter: Counter[str] = Counter()

        self._csv_handle = self.csv_path.open("w", newline="", encoding="utf-8-sig")
        secure_file(self.csv_path)
        self._csv_writer = csv.DictWriter(self._csv_handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        self._csv_writer.writeheader()

        self._json_handle = self.json_path.open("w", encoding="utf-8")
        secure_file(self.json_path)
        self._json_handle.write("[\n")
        self._first_json_record = True

        self._db = sqlite3.connect(self.sqlite_path)
        secure_file(self.sqlite_path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA temp_store=MEMORY")
        self._setup_db()

    def write_record(self, record: dict[str, Any]) -> None:
        normalized = normalize_record(record)
        self._csv_writer.writerow({key: _csv_value(normalized.get(key)) for key in CSV_FIELDS})

        if not self._first_json_record:
            self._json_handle.write(",\n")
        json.dump(normalized, self._json_handle, ensure_ascii=False, indent=2)
        self._first_json_record = False

        self._insert_db(normalized)
        self._update_counters(normalized)

        if self.file_count % int(self.config.get("commit_every") or 1000) == 0:
            self._db.commit()

    def finish(
        self,
        finished_at: datetime,
        errors: list[str],
        warnings: list[str] | None = None,
    ) -> dict[str, Path]:
        self._json_handle.write("\n]\n")
        self._json_handle.close()
        self._csv_handle.close()
        self._db.commit()

        duplicate_count, duplicate_group_count = self._finalize_duplicates()
        self._write_duplicate_reports()
        self._rewrite_index_exports_from_db()
        self._db.commit()
        self._db.close()

        write_summary(
            self.summary_path,
            self.started_at,
            finished_at,
            self.config,
            errors,
            warnings or [],
            file_count=self.file_count,
            total_bytes=self.total_bytes,
            error_count=self.error_count,
            duplicate_count=duplicate_count,
            duplicate_group_count=duplicate_group_count,
            media_counter=self.media_counter,
            extension_counter=self.extension_counter,
            project_counter=self.project_counter,
            year_counter=self.year_counter,
            source_role_counter=self.source_role_counter,
            ai_category_counter=self.ai_category_counter,
            ocr_status_counter=self.ocr_status_counter,
            sqlite_path=self.sqlite_path,
        )
        harden_private_tree(self.output_dir)

        return {
            "csv": self.csv_path,
            "json": self.json_path,
            "sqlite": self.sqlite_path,
            "duplicates_csv": self.duplicates_csv_path,
            "duplicates_json": self.duplicates_json_path,
            "summary": self.summary_path,
        }

    def _setup_db(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT,
                extension TEXT,
                size_bytes INTEGER,
                created_at TEXT,
                modified_at TEXT,
                full_path TEXT UNIQUE,
                parent_folder TEXT,
                source_root TEXT,
                source_role TEXT,
                depth INTEGER,
                sha256 TEXT,
                partial_hash TEXT,
                duplicate_key TEXT,
                duplicate_group TEXT,
                project_candidates TEXT,
                person_candidates TEXT,
                organization_candidates TEXT,
                event_candidates TEXT,
                year_candidates TEXT,
                media_type_candidate TEXT,
                importance_candidate TEXT,
                tag_candidates TEXT,
                generated_tags TEXT,
                ai_category TEXT,
                ai_subcategory TEXT,
                ai_confidence REAL,
                ai_reason TEXT,
                classifier TEXT,
                text_excerpt TEXT,
                ocr_text TEXT,
                ocr_status TEXT,
                duration_seconds TEXT,
                width TEXT,
                height TEXT,
                codec TEXT,
                technical_metadata_json TEXT,
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_files_path ON files(full_path);
            CREATE INDEX IF NOT EXISTS idx_files_duplicate_key ON files(duplicate_key);
            CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
            CREATE INDEX IF NOT EXISTS idx_files_media ON files(media_type_candidate);
            CREATE INDEX IF NOT EXISTS idx_files_category ON files(ai_category);
            CREATE TABLE IF NOT EXISTS duplicate_groups (
                duplicate_group TEXT PRIMARY KEY,
                duplicate_key TEXT,
                file_count INTEGER,
                total_bytes INTEGER,
                confidence TEXT
            );
            CREATE VIEW IF NOT EXISTS ai_search_documents AS
            SELECT
                id,
                file_name,
                full_path,
                parent_folder,
                source_role,
                media_type_candidate,
                ai_category,
                ai_subcategory,
                generated_tags,
                project_candidates,
                person_candidates,
                organization_candidates,
                event_candidates,
                year_candidates,
                importance_candidate,
                duplicate_group,
                modified_at,
                ocr_status,
                substr(text_excerpt, 1, 2000) AS text_excerpt,
                substr(ocr_text, 1, 2000) AS ocr_text,
                (
                    file_name || ' ' ||
                    full_path || ' ' ||
                    parent_folder || ' ' ||
                    ai_category || ' ' ||
                    generated_tags || ' ' ||
                    ifnull(text_excerpt, '') || ' ' ||
                    ifnull(ocr_text, '')
                ) AS search_text
            FROM files;
            """
        )
        try:
            self._db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    file_name,
                    full_path,
                    parent_folder,
                    project_candidates,
                    person_candidates,
                    organization_candidates,
                    event_candidates,
                    generated_tags,
                    ai_category,
                    text_excerpt,
                    ocr_text,
                    content='files',
                    content_rowid='id'
                )
                """
            )
        except sqlite3.OperationalError:
            pass

    def _insert_db(self, record: dict[str, Any]) -> None:
        values = [_db_value(record.get(field)) for field in CSV_FIELDS]
        placeholders = ", ".join("?" for _ in CSV_FIELDS)
        fields = ", ".join(CSV_FIELDS)
        self._db.execute(f"INSERT OR REPLACE INTO files ({fields}) VALUES ({placeholders})", values)
        row_id = self._db.execute("SELECT id FROM files WHERE full_path = ?", (record.get("full_path"),)).fetchone()
        if row_id:
            try:
                self._db.execute(
                    """
                    INSERT OR REPLACE INTO files_fts(
                        rowid, file_name, full_path, parent_folder, project_candidates,
                        person_candidates, organization_candidates, event_candidates,
                        generated_tags, ai_category, text_excerpt, ocr_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id[0],
                        record.get("file_name", ""),
                        record.get("full_path", ""),
                        record.get("parent_folder", ""),
                        _db_value(record.get("project_candidates")),
                        _db_value(record.get("person_candidates")),
                        _db_value(record.get("organization_candidates")),
                        _db_value(record.get("event_candidates")),
                        _db_value(record.get("generated_tags")),
                        record.get("ai_category", ""),
                        record.get("text_excerpt", ""),
                        record.get("ocr_text", ""),
                    ),
                )
            except sqlite3.OperationalError:
                pass

    def _update_counters(self, record: dict[str, Any]) -> None:
        self.file_count += 1
        self.total_bytes += int(record.get("size_bytes") or 0)
        self.error_count += 1 if record.get("error") else 0
        self.media_counter.update([record.get("media_type_candidate", "unknown")])
        self.extension_counter.update([record.get("extension") or "[no extension]"])
        self.project_counter.update(record.get("project_candidates") or [])
        self.year_counter.update(record.get("year_candidates") or [])
        self.source_role_counter.update([record.get("source_role") or "unknown"])
        self.ai_category_counter.update([record.get("ai_category") or "unknown"])
        self.ocr_status_counter.update([record.get("ocr_status") or "unknown"])

    def _finalize_duplicates(self) -> tuple[int, int]:
        rows = self._db.execute(
            """
            SELECT duplicate_key, COUNT(*) AS file_count, SUM(size_bytes) AS total_bytes
            FROM files
            WHERE duplicate_key != ''
            GROUP BY duplicate_key
            HAVING COUNT(*) > 1
            ORDER BY file_count DESC, total_bytes DESC
            """
        ).fetchall()

        duplicate_count = 0
        for index, (duplicate_key, file_count, total_bytes) in enumerate(rows, start=1):
            group_id = f"dup-{index:08d}"
            confidence = "exact" if str(duplicate_key).startswith("sha256:") else "probable_partial_hash"
            self._db.execute(
                """
                INSERT OR REPLACE INTO duplicate_groups(
                    duplicate_group, duplicate_key, file_count, total_bytes, confidence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, duplicate_key, file_count, total_bytes or 0, confidence),
            )
            self._db.execute(
                "UPDATE files SET duplicate_group = ? WHERE duplicate_key = ?",
                (group_id, duplicate_key),
            )
            duplicate_count += int(file_count)

        return duplicate_count, len(rows)

    def _write_duplicate_reports(self) -> None:
        rows = self._db.execute(
            """
            SELECT f.duplicate_group, g.confidence, f.full_path, f.size_bytes, f.sha256, f.partial_hash
            FROM files f
            JOIN duplicate_groups g ON f.duplicate_group = g.duplicate_group
            ORDER BY f.duplicate_group, f.full_path
            """
        ).fetchall()

        with self.duplicates_csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["duplicate_group", "confidence", "full_path", "size_bytes", "sha256", "partial_hash"])
            writer.writerows(rows)

        duplicate_json = [
            {
                "duplicate_group": row[0],
                "confidence": row[1],
                "full_path": row[2],
                "size_bytes": row[3],
                "sha256": row[4],
                "partial_hash": row[5],
            }
            for row in rows
        ]
        with self.duplicates_json_path.open("w", encoding="utf-8") as handle:
            json.dump(duplicate_json, handle, ensure_ascii=False, indent=2)

    def _rewrite_index_exports_from_db(self) -> None:
        query = f"SELECT {', '.join(CSV_FIELDS)} FROM files ORDER BY id"
        rows = self._db.execute(query)
        with self.csv_path.open("w", newline="", encoding="utf-8-sig") as csv_handle:
            csv_writer = csv.DictWriter(csv_handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
            csv_writer.writeheader()
            with self.json_path.open("w", encoding="utf-8") as json_handle:
                json_handle.write("[\n")
                first = True
                for row in rows:
                    record = {
                        field: _restore_value(field, value)
                        for field, value in zip(CSV_FIELDS, row, strict=True)
                    }
                    csv_writer.writerow({key: _csv_value(record.get(key)) for key in CSV_FIELDS})
                    if not first:
                        json_handle.write(",\n")
                    json.dump(record, json_handle, ensure_ascii=False, indent=2)
                    first = False
                json_handle.write("\n]\n")


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: record.get(field, "") for field in CSV_FIELDS}
    for field in LIST_FIELDS:
        value = normalized.get(field)
        if isinstance(value, list):
            normalized[field] = value
        elif value:
            normalized[field] = [str(value)]
        else:
            normalized[field] = []
    return normalized


def write_summary(
    path: Path,
    started_at: datetime,
    finished_at: datetime,
    config: dict[str, Any],
    errors: list[str],
    warnings: list[str],
    file_count: int,
    total_bytes: int,
    error_count: int,
    duplicate_count: int,
    duplicate_group_count: int,
    media_counter: Counter[str],
    extension_counter: Counter[str],
    project_counter: Counter[str],
    year_counter: Counter[str],
    source_role_counter: Counter[str],
    ai_category_counter: Counter[str],
    ocr_status_counter: Counter[str],
    sqlite_path: Path,
) -> None:
    lines = [
        "# Nakadachi Archive AI Summary",
        "",
        f"- Started: {started_at.isoformat(timespec='seconds')}",
        f"- Finished: {finished_at.isoformat(timespec='seconds')}",
        f"- File count: {file_count}",
        f"- Total size: {_format_bytes(total_bytes)}",
        f"- File errors: {error_count}",
        f"- Errors: {len(errors)}",
        f"- Warnings: {len(warnings)}",
        f"- Duplicate files: {duplicate_count}",
        f"- Duplicate groups: {duplicate_group_count}",
        f"- SQLite database: {sqlite_path}",
        "",
        "## Scan Paths",
        "",
    ]

    for scan_path in config.get("scan_paths", []):
        lines.append(f"- {scan_path}")

    sections = [
        ("Media Types", media_counter, 20),
        ("AI Categories", ai_category_counter, 30),
        ("OCR Status", ocr_status_counter, 20),
        ("Source Roles", source_role_counter, 20),
        ("Top Extensions", extension_counter, 30),
        ("Project Candidates", project_counter, 30),
        ("Year Candidates", year_counter, 30),
    ]
    for title, counter, limit in sections:
        lines.extend(["", f"## {title}", ""])
        lines.extend(_counter_lines(counter, limit=limit))

    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings[:100]:
            lines.append(f"- {warning}")
        if len(warnings) > 100:
            lines.append(f"- ...and {len(warnings) - 100} more warnings. See logs for details.")

    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors[:100]:
            lines.append(f"- {error}")
        if len(errors) > 100:
            lines.append(f"- ...and {len(errors) - 100} more errors. See logs for details.")

    lines.append("")
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _db_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _restore_value(field: str, value: Any) -> Any:
    if field in LIST_FIELDS:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return [str(value)]
        return parsed if isinstance(parsed, list) else [str(parsed)]
    return value if value is not None else ""


def _counter_lines(counter: Counter[str], limit: int = 20) -> list[str]:
    if not counter:
        return ["- None detected"]
    return [f"- {label}: {count}" for label, count in counter.most_common(limit)]


def _format_bytes(size: int) -> str:
    units = ["bytes", "KB", "MB", "GB", "TB", "PB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "bytes":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} bytes"


def _output_paths(output_dir: Path, started_at: datetime) -> tuple[Path, Path, Path, Path]:
    plain_paths = (
        output_dir / "archive_index.csv",
        output_dir / "archive_index.json",
        output_dir / "summary.md",
        output_dir / "archive_index.sqlite",
    )
    if not any(path.exists() for path in plain_paths):
        return plain_paths

    timestamp = started_at.strftime("%Y%m%d_%H%M%S")
    return (
        output_dir / f"archive_index_{timestamp}.csv",
        output_dir / f"archive_index_{timestamp}.json",
        output_dir / f"summary_{timestamp}.md",
        output_dir / f"archive_index_{timestamp}.sqlite",
    )
