from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import kio_node_automation as automation
import scan_archive

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / 'config' / 'historical_works.json'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


def normalise(value: str) -> str:
    return re.sub(r'\s+', '', value.casefold())


def match_aliases(value: str, aliases: list[str]) -> list[str]:
    target = normalise(value)
    return [alias for alias in aliases if normalise(alias) and normalise(alias) in target]


def file_matches(path: Path, aliases: list[str]) -> list[str]:
    return match_aliases(str(path), aliases)


def evidence_categories_text(value: str, config: dict[str, Any]) -> list[str]:
    target = normalise(value)
    found: list[str] = []
    for category, words in (config.get('evidence_keywords') or {}).items():
        if any(normalise(word) in target for word in words):
            found.append(category)
    return found


def evidence_categories(path: Path, config: dict[str, Any]) -> list[str]:
    return evidence_categories_text(path.name, config)


def classify_media(path: Path, config: dict[str, Any]) -> str:
    ext = path.suffix.casefold()
    if ext in set(config.get('media_extensions', [])):
        return 'media'
    if ext in set(config.get('content_extensions', [])):
        return 'document'
    return 'other'


def _archive_output_dir() -> Path:
    cfg = scan_archive.load_config(Path(scan_archive.default_config_path()))
    raw = cfg.get('output_dir') or '~/NakadachiArchiveAI/output'
    return Path(os.path.expandvars(os.path.expanduser(str(raw)))).resolve()


def latest_archive_db() -> Path | None:
    output_dir = _archive_output_dir()
    candidates = [p for p in output_dir.glob('archive_index*.sqlite') if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime_ns)


def _json_list(value: Any) -> list[str]:
    if value in (None, ''):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    text = str(value)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [text]


def _snippet(text: str, aliases: list[str], radius: int = 180) -> str:
    if not text:
        return ''
    folded = text.casefold()
    positions = [folded.find(alias.casefold()) for alias in aliases if alias and folded.find(alias.casefold()) >= 0]
    if not positions:
        return text[: radius * 2].replace('\n', ' ').strip()
    pos = min(positions)
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    return text[start:end].replace('\n', ' ').strip()


def _known_normalised_aliases(works: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for work in works:
        values.add(normalise(str(work.get('title') or '')))
        for alias in work.get('aliases', []):
            values.add(normalise(str(alias)))
    return {item for item in values if item}


def discover_from_archive_index(db_path: Path, config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], int, list[dict[str, Any]], list[str]]:
    max_results = int(config.get('max_results_per_work', 500))
    works = config.get('works', [])
    results: dict[str, list[dict[str, Any]]] = {work['id']: [] for work in works}
    seen: dict[str, set[str]] = {work['id']: set() for work in works}
    project_counts: Counter[str] = Counter()
    project_samples: dict[str, list[str]] = defaultdict(list)
    scanned = 0
    errors: list[str] = []

    query = '''
        SELECT file_name, extension, size_bytes, modified_at, full_path,
               source_root, source_role, project_candidates, generated_tags,
               ai_category, text_excerpt, ocr_text, error
        FROM files
        ORDER BY id
    '''
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    except sqlite3.Error as exc:
        return results, 0, [], [f'{db_path}: {exc}']

    try:
        cursor = conn.execute(query)
        for row in cursor:
            scanned += 1
            (
                file_name, extension, size_bytes, modified_at, full_path,
                source_root, source_role, project_candidates, generated_tags,
                ai_category, text_excerpt, ocr_text, row_error,
            ) = row
            path = Path(str(full_path or file_name or ''))
            project_values = _json_list(project_candidates)
            for project in project_values:
                project_counts[project] += 1
                if len(project_samples[project]) < 3 and full_path:
                    project_samples[project].append(str(full_path))

            path_text = str(full_path or '')
            content_text = ' '.join(
                str(value or '') for value in [file_name, generated_tags, ai_category, text_excerpt, ocr_text]
            )
            combined_text = f'{path_text} {content_text}'

            for work in works:
                bucket = results[work['id']]
                if len(bucket) >= max_results:
                    continue
                aliases = [str(alias) for alias in work.get('aliases', []) if str(alias).strip()]
                path_matches = match_aliases(path_text, aliases)
                content_matches = match_aliases(content_text, aliases)
                matched = list(dict.fromkeys([*path_matches, *content_matches]))
                if not matched:
                    continue
                key = str(full_path or file_name or '')
                if key in seen[work['id']]:
                    continue
                seen[work['id']].add(key)
                match_sources = []
                if path_matches:
                    match_sources.append('path')
                if content_matches:
                    match_sources.append('extracted_text')
                confidence = 'high' if path_matches else 'medium'
                bucket.append({
                    'path': str(full_path or ''),
                    'filename': str(file_name or path.name),
                    'extension': str(extension or path.suffix).casefold(),
                    'kind': classify_media(path, config),
                    'matched_aliases': matched,
                    'match_sources': match_sources,
                    'match_confidence': confidence,
                    'evidence_categories': evidence_categories_text(combined_text, config),
                    'size_bytes': int(size_bytes or 0),
                    'modified_at': str(modified_at or ''),
                    'source_root': str(source_root or ''),
                    'source_role': str(source_role or ''),
                    'text_snippet': _snippet(f'{text_excerpt or ""} {ocr_text or ""}', matched),
                    'archive_error': str(row_error or ''),
                })
    except sqlite3.Error as exc:
        errors.append(f'{db_path}: {exc}')
    finally:
        conn.close()

    known = _known_normalised_aliases(works)
    generic = {normalise(item) for item in config.get('generic_project_names', [])}
    unlisted: list[dict[str, Any]] = []
    for project, count in project_counts.most_common(int(config.get('max_unlisted_projects', 100)) * 3):
        norm = normalise(project)
        if not norm or norm in known or norm in generic:
            continue
        if count < int(config.get('min_unlisted_project_hits', 2)):
            continue
        unlisted.append({'project_candidate': project, 'file_count': count, 'sample_paths': project_samples[project]})
        if len(unlisted) >= int(config.get('max_unlisted_projects', 100)):
            break

    return results, scanned, unlisted, errors


def discover_from_paths(config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], int, list[str], list[str]]:
    roots, excludes, warnings = automation.configured_scan_roots()
    max_results = int(config.get('max_results_per_work', 500))
    works = config.get('works', [])
    results: dict[str, list[dict[str, Any]]] = {work['id']: [] for work in works}
    scanned = 0
    errors: list[str] = []

    for root in roots:
        if not root.exists():
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        path = Path(entry.path)
                        if automation.excluded(path, excludes):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            stat = entry.stat(follow_symlinks=False)
                        except OSError as exc:
                            if len(errors) < 50:
                                errors.append(f'{path}: {exc}')
                            continue
                        scanned += 1
                        for work in works:
                            bucket = results[work['id']]
                            if len(bucket) >= max_results:
                                continue
                            matches = file_matches(path, work.get('aliases', []))
                            if not matches:
                                continue
                            bucket.append({
                                'path': str(path),
                                'filename': path.name,
                                'extension': path.suffix.casefold(),
                                'kind': classify_media(path, config),
                                'matched_aliases': matches,
                                'match_sources': ['path'],
                                'match_confidence': 'high',
                                'evidence_categories': evidence_categories(path, config),
                                'size_bytes': stat.st_size,
                                'modified_at': datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec='seconds'),
                                'source_root': str(root),
                                'source_role': '',
                                'text_snippet': '',
                                'archive_error': '',
                            })
            except OSError as exc:
                if len(errors) < 50:
                    errors.append(f'{current}: {exc}')
    return results, scanned, warnings, errors


def _sort_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            0 if item.get('match_confidence') == 'high' else 1,
            0 if item.get('evidence_categories') else 1,
            str(item.get('modified_at') or ''),
        ),
    )


def discover() -> dict[str, Any]:
    config = load_config()
    roots, _, root_warnings = automation.configured_scan_roots()
    db_path = latest_archive_db()
    unlisted_projects: list[dict[str, Any]] = []

    if db_path is not None:
        results, scanned, unlisted_projects, errors = discover_from_archive_index(db_path, config)
        warnings = list(root_warnings)
        discovery_source = 'archive_index_sqlite'
    else:
        results, scanned, warnings, errors = discover_from_paths(config)
        warnings = [*root_warnings, *warnings, 'Archive SQLite index not found; used path-only fallback.']
        discovery_source = 'filesystem_path_fallback'

    output_dir = Path(os.path.expanduser(config.get('output_dir', '~/NakadachiArchiveAI/historical_works')))
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        'status': 'ok' if not errors else 'partial',
        'generated_at': now_iso(),
        'discovery_source': discovery_source,
        'archive_index': str(db_path or ''),
        'roots': [str(root) for root in roots],
        'scanned_files': scanned,
        'warnings': warnings,
        'errors': errors,
        'works': [],
        'unlisted_project_candidates': unlisted_projects,
    }
    ledger_rows: list[dict[str, Any]] = []
    for work in config.get('works', []):
        items = _sort_items(results[work['id']])
        summary = {
            'id': work['id'],
            'title': work['title'],
            'aliases': work.get('aliases', []),
            'match_count': len(items),
            'document_count': sum(1 for item in items if item['kind'] == 'document'),
            'media_count': sum(1 for item in items if item['kind'] == 'media'),
            'path_match_count': sum(1 for item in items if 'path' in item.get('match_sources', [])),
            'text_match_count': sum(1 for item in items if 'extracted_text' in item.get('match_sources', [])),
            'items': items,
        }
        report['works'].append(summary)
        for item in items:
            ledger_rows.append({'work_id': work['id'], 'work_title': work['title'], **item})

    (output_dir / 'historical_works_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    jsonl = '\n'.join(json.dumps(row, ensure_ascii=False) for row in ledger_rows)
    (output_dir / 'representative_work_evidence_candidates.jsonl').write_text(
        jsonl + ('\n' if jsonl else ''), encoding='utf-8'
    )
    (output_dir / 'unlisted_project_candidates.json').write_text(
        json.dumps(unlisted_projects, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return report


def main() -> int:
    report = discover()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] in {'ok', 'partial'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
