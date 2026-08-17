from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ai_classifier import classify_with_ai
from classify_rules import classify_file
from exporter import ArchiveExporter
from extractors import compute_hashes, enrich_content_metadata
from private_storage import ensure_private_directory, set_private_umask


DEFAULT_CONFIG = {
    "scan_paths": ["/Volumes/Transcend"],
    "exclude_paths": [".git", ".DS_Store", "node_modules", "__pycache__"],
    "output_dir": "~/NakadachiArchiveAI/output",
    "log_dir": "~/NakadachiArchiveAI/logs",
    "max_depth": None,
    "allowed_extensions": [],
    "duplicate_detection": True,
    "full_hash_max_mb": 256,
    "partial_hash_bytes": 1048576,
    "extract_text": True,
    "enable_ocr": True,
    "ocr_max_file_mb": 50,
    "text_excerpt_chars": 4000,
    "commit_every": 1000,
}


def main() -> int:
    set_private_umask()
    parser = argparse.ArgumentParser(description="Read-only archive catalog generator.")
    parser.add_argument("--config", default=default_config_path(), help="Path to config.yaml")
    parser.add_argument(
        "--init-user-dirs",
        action="store_true",
        help="Create default output and log directories, then exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check paths and read metadata without writing index files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after this many indexed files. Useful for a safe test run.",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    output_dir = expand_path(config.get("output_dir") or DEFAULT_CONFIG["output_dir"])
    log_dir = expand_path(config.get("log_dir") or DEFAULT_CONFIG["log_dir"])

    if args.init_user_dirs:
        ensure_private_directory(output_dir, harden_existing=True)
        ensure_private_directory(log_dir, harden_existing=True)
        print(f"Created output folder: {output_dir}")
        print(f"Created log folder: {log_dir}")
        return 0

    started_at = datetime.now().astimezone()
    warnings: list[str] = []
    errors: list[str] = []
    scan_roots = resolve_scan_paths(config.get("scan_paths", []), warnings)

    if args.dry_run:
        dry_run(scan_roots, output_dir, log_dir, config, warnings, errors, args.limit)
        return 0

    ensure_private_directory(log_dir, harden_existing=True)
    setup_logging(log_dir)
    logging.info("Nakadachi Archive AI scan started")
    logging.info("Read-only mode: no archive files will be changed")
    for warning in warnings:
        logging.warning(warning)

    exporter = ArchiveExporter(output_dir, started_at, config)

    limit_reached = False
    for root_path in scan_roots:
        for record in scan_path(root_path, config, errors):
            exporter.write_record(record)
            if args.limit is not None and exporter.file_count >= args.limit:
                limit_reached = True
                break
        if limit_reached:
            break

    finished_at = datetime.now().astimezone()
    exported = exporter.finish(finished_at, errors, warnings)

    logging.info(
        "Scan finished: %s files, %s errors, %s warnings",
        exporter.file_count,
        len(errors),
        len(warnings),
    )
    for label, path in exported.items():
        logging.info("Wrote %s: %s", label, path)

    print(f"Scan finished. Files indexed: {exporter.file_count}. Errors: {len(errors)}.")
    if warnings:
        print(f"Warnings: {len(warnings)}.")
    print(f"Output folder: {output_dir}")
    if args.limit is not None and limit_reached:
        print(f"Stopped after limit: {args.limit}")
    return 0


def dry_run(
    scan_roots: list[Path],
    output_dir: Path,
    log_dir: Path,
    config: dict[str, Any],
    warnings: list[str],
    errors: list[str],
    limit: int | None,
) -> None:
    checked_count = 0
    print("Dry run: no index files will be written.")
    print(f"Output folder configured: {output_dir}")
    print(f"Output folder exists: {output_dir.exists()}")
    print(f"Log folder configured: {log_dir}")
    print(f"Log folder exists: {log_dir.exists()}")

    plain_outputs = [
        output_dir / "archive_index.csv",
        output_dir / "archive_index.json",
        output_dir / "summary.md",
        output_dir / "archive_index.sqlite",
    ]
    existing_outputs = [path for path in plain_outputs if path.exists()]
    if existing_outputs:
        print("Existing output files detected. A real run will use timestamped filenames.")
        for path in existing_outputs:
            print(f"- {path}")
    else:
        print("Existing output files detected: none")

    if not scan_roots:
        print("No existing scan paths were found.")

    for root_path in scan_roots:
        print(f"Readable scan path: {root_path}")
        for record in scan_path(root_path, config, errors):
            checked_count += 1
            if limit is not None and checked_count >= limit:
                break
        if limit is not None and checked_count >= limit:
            break

    print(f"Dry run metadata records checked: {checked_count}")
    print(f"Warnings: {len(warnings)}")
    for warning in warnings[:20]:
        print(f"- {warning}")
    print(f"Errors: {len(errors)}")
    for error in errors[:20]:
        print(f"- {error}")


def scan_path(
    root_path: Path,
    config: dict[str, Any],
    errors: list[str],
) -> Iterator[dict[str, Any]]:
    exclude_paths = [str(item) for item in config.get("exclude_paths", [])]
    allowed_extensions = normalize_extensions(config.get("allowed_extensions", []))
    max_depth = config.get("max_depth")

    def on_walk_error(exc: OSError) -> None:
        message = f"{getattr(exc, 'filename', root_path)}: {exc}"
        logging.warning("Could not access folder while scanning: %s", message)
        errors.append(message)

    for current_root, dirnames, filenames in os.walk(
        root_path,
        topdown=True,
        onerror=on_walk_error,
        followlinks=False,
    ):
        current_path = Path(current_root)
        depth = get_depth(root_path, current_path)

        if max_depth is not None and depth >= max_depth:
            dirnames[:] = []

        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not should_exclude(current_path / dirname, exclude_paths)
        ]

        for filename in filenames:
            full_path = current_path / filename
            if should_exclude(full_path, exclude_paths):
                continue

            extension = full_path.suffix.lower()
            if allowed_extensions and extension not in allowed_extensions:
                continue

            try:
                record = build_record(full_path, root_path, depth)
                record.update(classify_file(record))
                record.update(compute_hashes(full_path, int(record.get("size_bytes") or 0), config))
                record.update(enrich_content_metadata(full_path, config))
                combined_text = " ".join(
                    [
                        str(record.get("text_excerpt") or ""),
                        str(record.get("ocr_text") or ""),
                    ]
                ).strip()
                record.update(classify_with_ai(record, combined_text))
                yield record
            except OSError as exc:
                if isinstance(exc, FileNotFoundError):
                    logging.info("Skipped disappeared file while scanning: %s", full_path)
                    continue
                message = f"{full_path}: {exc}"
                logging.exception("Could not read file metadata: %s", full_path)
                errors.append(message)
                yield build_error_record(full_path, root_path, depth, str(exc))
            except Exception as exc:
                message = f"{full_path}: {exc}"
                logging.exception("Unexpected error while scanning: %s", full_path)
                errors.append(message)
                yield build_error_record(full_path, root_path, depth, str(exc))


def build_record(full_path: Path, source_root: Path, depth: int) -> dict[str, Any]:
    stat = full_path.stat()
    created_timestamp = getattr(stat, "st_birthtime", stat.st_ctime)
    created_at = datetime.fromtimestamp(created_timestamp).astimezone()
    modified_at = datetime.fromtimestamp(stat.st_mtime).astimezone()

    return {
        "file_name": full_path.name,
        "extension": full_path.suffix.lower(),
        "size_bytes": stat.st_size,
        "created_at": created_at.isoformat(timespec="seconds"),
        "modified_at": modified_at.isoformat(timespec="seconds"),
        "full_path": str(full_path),
        "parent_folder": full_path.parent.name,
        "source_root": str(source_root),
        "source_role": guess_source_role(source_root),
        "depth": depth,
        "sha256": "",
        "partial_hash": "",
        "duplicate_key": "",
        "duplicate_group": "",
        "text_excerpt": "",
        "ocr_text": "",
        "ocr_status": "not_processed",
        "duration_seconds": "",
        "width": "",
        "height": "",
        "codec": "",
        "technical_metadata_json": "{}",
        "ai_category": "",
        "ai_subcategory": "",
        "ai_confidence": "",
        "ai_reason": "",
        "classifier": "",
        "generated_tags": [],
        "error": "",
    }


def build_error_record(full_path: Path, source_root: Path, depth: int, error: str) -> dict[str, Any]:
    record = {
        "file_name": full_path.name,
        "extension": full_path.suffix.lower(),
        "size_bytes": "",
        "created_at": "",
        "modified_at": "",
        "full_path": str(full_path),
        "parent_folder": full_path.parent.name,
        "source_root": str(source_root),
        "source_role": guess_source_role(source_root),
        "depth": depth,
        "sha256": "",
        "partial_hash": "",
        "duplicate_key": "",
        "duplicate_group": "",
        "project_candidates": [],
        "person_candidates": [],
        "organization_candidates": [],
        "event_candidates": [],
        "year_candidates": [],
        "media_type_candidate": "unknown",
        "importance_candidate": "unknown",
        "tag_candidates": [],
        "generated_tags": [],
        "ai_category": "error",
        "ai_subcategory": "error",
        "ai_confidence": 0,
        "ai_reason": "metadata read failed",
        "classifier": "none",
        "text_excerpt": "",
        "ocr_text": "",
        "ocr_status": "not_processed",
        "duration_seconds": "",
        "width": "",
        "height": "",
        "codec": "",
        "technical_metadata_json": "{}",
        "error": error,
    }
    return record


def load_config(path: Path) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if not path.exists():
        return config

    parsed = parse_simple_yaml(path.read_text(encoding="utf-8"))
    for key, value in parsed.items():
        config[key] = value

    if config.get("max_depth") in ("", None, []):
        config["max_depth"] = None
    else:
        config["max_depth"] = int(config["max_depth"])

    if config.get("allowed_extensions") in ("", None):
        config["allowed_extensions"] = []

    return config


def resolve_scan_paths(raw_paths: list[str], errors: list[str]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()

    for raw_path in raw_paths:
        expanded = os.path.expandvars(os.path.expanduser(str(raw_path)))
        candidates = sorted(glob.glob(expanded)) if has_glob(expanded) else [expanded]
        if not candidates:
            errors.append(f"Scan path pattern matched nothing: {raw_path}")
            continue

        for candidate in candidates:
            path = Path(candidate).resolve()
            path_text = str(path)
            if path_text in seen:
                continue
            seen.add(path_text)

            if not path.exists():
                errors.append(f"Scan path does not exist: {path}")
                continue
            if not path.is_dir():
                errors.append(f"Scan path is not a directory: {path}")
                continue
            if not os.access(path, os.R_OK):
                errors.append(f"Scan path may not be readable: {path}")
                continue

            resolved.append(path)

    return resolved


def has_glob(path_text: str) -> bool:
    return any(char in path_text for char in "*?[")


def parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        stripped = line.strip()
        if stripped.startswith("- ") and current_key:
            result.setdefault(current_key, [])
            result[current_key].append(_parse_scalar(stripped[2:].strip()))
            continue

        if ":" in stripped:
            key, value = stripped.split(":", 1)
            current_key = key.strip()
            value = value.strip()
            if value == "":
                result[current_key] = []
            else:
                result[current_key] = _parse_scalar(value)

    return result


def _parse_scalar(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    if value.lower() in {"null", "none", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        return value


def expand_path(path_value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path_value)))).resolve()


def normalize_extensions(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, str):
        values = [values]
    normalized = set()
    for value in values:
        extension = str(value).strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = f".{extension}"
        normalized.add(extension)
    return normalized


def should_exclude(path: Path, exclude_paths: list[str]) -> bool:
    path_text = str(path)
    name = path.name
    for raw_exclude in exclude_paths:
        exclude = os.path.expandvars(os.path.expanduser(raw_exclude))
        if not exclude:
            continue
        if name == exclude or path_text == exclude or exclude in path.parts:
            return True
        if os.path.isabs(exclude) and path_text.startswith(exclude.rstrip(os.sep) + os.sep):
            return True
    return False


def get_depth(root_path: Path, current_path: Path) -> int:
    try:
        return len(current_path.relative_to(root_path).parts)
    except ValueError:
        return 0


def guess_source_role(source_root: Path) -> str:
    text = str(source_root).casefold()
    name = source_root.name.casefold()
    if name in {"transcend", "trancend"} or "/volumes/transcend" in text or "/volumes/trancend" in text:
        return "main_archive_external_hdd"
    if "googledrive" in text or "google drive" in text or "cloudstorage" in text:
        return "shared_in_progress_google_drive"
    if str(Path.home()).casefold() in text:
        return "working_materials_mac_local"
    return "unknown"


def setup_logging(log_dir: Path) -> None:
    log_file = log_dir / "nakadachi_archive_ai.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def default_config_path() -> str:
    project_dir = Path(__file__).resolve().parent.parent
    return str(project_dir / "config" / "config.yaml")


if __name__ == "__main__":
    raise SystemExit(main())
