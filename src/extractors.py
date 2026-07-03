from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".tif", ".tiff", ".bmp"}
AV_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".wmv", ".mp3", ".wav", ".aif", ".aiff", ".m4a"}


def enrich_content_metadata(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    extension = path.suffix.lower()
    result: dict[str, Any] = {
        "text_excerpt": "",
        "ocr_text": "",
        "ocr_status": "not_applicable",
        "duration_seconds": "",
        "width": "",
        "height": "",
        "codec": "",
        "technical_metadata_json": "{}",
    }

    text_limit = int(config.get("text_excerpt_chars") or 4000)
    if config.get("extract_text", True):
        result["text_excerpt"] = extract_text_excerpt(path, extension, text_limit)

    ocr_limit = int(config.get("ocr_max_file_mb") or 50) * 1024 * 1024
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0
    if config.get("enable_ocr", True) and extension in PDF_EXTENSIONS | IMAGE_EXTENSIONS and size_bytes <= ocr_limit:
        ocr_text, status = extract_ocr(path, extension, text_limit)
        result["ocr_text"] = ocr_text
        result["ocr_status"] = status
    elif config.get("enable_ocr", True) and extension in PDF_EXTENSIONS | IMAGE_EXTENSIONS:
        result["ocr_status"] = "skipped_size_limit"

    if extension in AV_EXTENSIONS or extension in IMAGE_EXTENSIONS:
        av_metadata = extract_media_metadata(path)
        result.update({key: av_metadata.get(key, "") for key in ["duration_seconds", "width", "height", "codec"]})
        result["technical_metadata_json"] = json.dumps(av_metadata, ensure_ascii=False, sort_keys=True)

    return result


def compute_hashes(path: Path, size_bytes: int, config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("duplicate_detection", True):
        return {"sha256": "", "partial_hash": "", "duplicate_key": ""}

    full_limit = int(config.get("full_hash_max_mb") or 256) * 1024 * 1024
    partial_bytes = int(config.get("partial_hash_bytes") or 1024 * 1024)
    partial_hash = compute_partial_hash(path, size_bytes, partial_bytes)
    sha256 = ""
    if size_bytes <= full_limit:
        sha256 = compute_sha256(path)

    duplicate_key = f"sha256:{size_bytes}:{sha256}" if sha256 else f"partial:{size_bytes}:{partial_hash}"
    return {
        "sha256": sha256,
        "partial_hash": partial_hash,
        "duplicate_key": duplicate_key if partial_hash or sha256 else "",
    }


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        logging.warning("Could not hash file: %s: %s", path, exc)
        return ""


def compute_partial_hash(path: Path, size_bytes: int, partial_bytes: int) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            if size_bytes <= partial_bytes * 2:
                digest.update(handle.read())
            else:
                digest.update(handle.read(partial_bytes))
                handle.seek(max(0, size_bytes - partial_bytes), os.SEEK_SET)
                digest.update(handle.read(partial_bytes))
        return digest.hexdigest()
    except OSError as exc:
        logging.warning("Could not partial-hash file: %s: %s", path, exc)
        return ""


def extract_text_excerpt(path: Path, extension: str, limit: int) -> str:
    if extension in TEXT_EXTENSIONS:
        return read_text_file(path, limit)
    if extension == ".pdf":
        return run_text_command(["pdftotext", "-layout", str(path), "-"], limit)
    if extension in {".doc", ".docx", ".rtf"}:
        return run_text_command(["textutil", "-convert", "txt", "-stdout", str(path)], limit)
    return ""


def extract_ocr(path: Path, extension: str, limit: int) -> tuple[str, str]:
    if extension == ".pdf":
        text = run_text_command(["pdftotext", "-layout", str(path), "-"], limit)
        if text:
            return text, "pdf_text_extracted"

    if shutil.which("tesseract"):
        text = run_text_command(["tesseract", str(path), "stdout", "-l", "jpn+eng"], limit)
        if text:
            return text, "tesseract"
        return "", "tesseract_no_text"

    if extension == ".pdf" and shutil.which("mdls"):
        text = run_text_command(["mdls", "-raw", "-name", "kMDItemTextContent", str(path)], limit)
        if text and text != "(null)":
            return text, "spotlight_text"

    return "", "ocr_tool_unavailable"


def extract_media_metadata(path: Path) -> dict[str, Any]:
    if shutil.which("ffprobe"):
        output = run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name,width,height",
                "-of",
                "json",
                str(path),
            ]
        )
        if output:
            return parse_ffprobe(output)

    if shutil.which("mdls"):
        metadata: dict[str, Any] = {}
        for key in ["kMDItemDurationSeconds", "kMDItemPixelWidth", "kMDItemPixelHeight", "kMDItemCodecs"]:
            value = run_text_command(["mdls", "-raw", "-name", key, str(path)], 1000)
            if value and value != "(null)":
                metadata[key] = value
        return {
            "duration_seconds": metadata.get("kMDItemDurationSeconds", ""),
            "width": metadata.get("kMDItemPixelWidth", ""),
            "height": metadata.get("kMDItemPixelHeight", ""),
            "codec": metadata.get("kMDItemCodecs", ""),
            "source": "mdls",
        }

    return {"source": "unavailable"}


def read_text_file(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit * 4)
        return data.decode("utf-8", errors="replace")[:limit]
    except OSError as exc:
        logging.warning("Could not read text excerpt: %s: %s", path, exc)
        return ""


def run_text_command(command: list[str], limit: int) -> str:
    if not shutil.which(command[0]):
        return ""
    output = run_command(command)
    return output[:limit] if output else ""


def run_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.debug("Metadata command failed: %s: %s", command[0], exc)
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def parse_ffprobe(output: str) -> dict[str, Any]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {"source": "ffprobe", "raw": output[:1000]}

    streams = data.get("streams") or []
    first_stream = streams[0] if streams else {}
    format_data = data.get("format") or {}
    return {
        "duration_seconds": format_data.get("duration", ""),
        "width": first_stream.get("width", ""),
        "height": first_stream.get("height", ""),
        "codec": first_stream.get("codec_name", ""),
        "source": "ffprobe",
    }
