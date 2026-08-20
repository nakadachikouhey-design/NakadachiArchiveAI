from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import kio_node_automation as automation

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / 'config' / 'historical_works.json'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))


def normalise(value: str) -> str:
    return re.sub(r'\s+', '', value.casefold())


def file_matches(path: Path, aliases: list[str]) -> list[str]:
    target = normalise(str(path))
    return [alias for alias in aliases if normalise(alias) in target]


def evidence_categories(path: Path, config: dict[str, Any]) -> list[str]:
    target = normalise(path.name)
    found: list[str] = []
    for category, words in (config.get('evidence_keywords') or {}).items():
        if any(normalise(word) in target for word in words):
            found.append(category)
    return found


def classify_media(path: Path, config: dict[str, Any]) -> str:
    ext = path.suffix.casefold()
    if ext in set(config.get('media_extensions', [])):
        return 'media'
    if ext in set(config.get('content_extensions', [])):
        return 'document'
    return 'other'


def discover() -> dict[str, Any]:
    config = load_config()
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
                                'evidence_categories': evidence_categories(path, config),
                                'size_bytes': stat.st_size,
                                'modified_at': datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec='seconds'),
                            })
            except OSError as exc:
                if len(errors) < 50:
                    errors.append(f'{current}: {exc}')

    output_dir = Path(os.path.expanduser(config.get('output_dir', '~/NakadachiArchiveAI/historical_works')))
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        'status': 'ok' if not errors else 'partial',
        'generated_at': now_iso(),
        'roots': [str(root) for root in roots],
        'scanned_files': scanned,
        'warnings': warnings,
        'errors': errors,
        'works': [],
    }
    ledger_rows: list[dict[str, Any]] = []
    for work in works:
        items = sorted(results[work['id']], key=lambda item: item['modified_at'])
        summary = {
            'id': work['id'],
            'title': work['title'],
            'aliases': work.get('aliases', []),
            'match_count': len(items),
            'document_count': sum(1 for item in items if item['kind'] == 'document'),
            'media_count': sum(1 for item in items if item['kind'] == 'media'),
            'items': items,
        }
        report['works'].append(summary)
        for item in items:
            ledger_rows.append({'work_id': work['id'], 'work_title': work['title'], **item})

    (output_dir / 'historical_works_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    jsonl = '\n'.join(json.dumps(row, ensure_ascii=False) for row in ledger_rows)
    (output_dir / 'representative_work_evidence_candidates.jsonl').write_text(jsonl + ('\n' if jsonl else ''), encoding='utf-8')
    return report


def main() -> int:
    report = discover()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] in {'ok', 'partial'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
