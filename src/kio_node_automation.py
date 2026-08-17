from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import scan_archive

RUNTIME_DIR = Path(os.path.expanduser('~/NakadachiArchiveAI'))
STATE_DIR = RUNTIME_DIR / 'agent_state'
DEFAULT_REPO = 'nakadachikouhey-design/NakadachiArchiveAI'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def slack_notify(text: str) -> dict[str, Any]:
    webhook = os.environ.get('KIO_SLACK_WEBHOOK_URL', '').strip()
    if not webhook:
        return {'status': 'skipped', 'reason': 'KIO_SLACK_WEBHOOK_URL is not configured'}
    payload = json.dumps({'text': text}, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(webhook, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return {'status': 'ok', 'http_status': response.status}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {'status': 'failed', 'error': str(exc)}


def retry_action(action: str, executor: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    max_retries = max(1, int(os.environ.get('KIO_ACTION_MAX_RETRIES', '3')))
    delay = max(1, int(os.environ.get('KIO_RETRY_DELAY_SECONDS', '10')))
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_retries + 1):
        result = executor(action)
        result['attempt'] = attempt
        attempts.append(result)
        if result.get('status') in {'ok', 'rejected', 'skipped_dirty_worktree'}:
            result['attempts'] = attempts
            result['recovered_after_retry'] = attempt > 1 and result.get('status') == 'ok'
            return result
        if attempt < max_retries:
            time.sleep(delay * attempt)
    result = attempts[-1]
    result['attempts'] = attempts
    result['retries_exhausted'] = True
    return result


def configured_scan_roots() -> tuple[list[Path], list[str], list[str]]:
    config = scan_archive.load_config(Path(scan_archive.default_config_path()))
    warnings: list[str] = []
    roots = scan_archive.resolve_scan_paths(config.get('scan_paths', []), warnings)
    excludes = [str(item) for item in config.get('exclude_paths', [])]
    return roots, excludes, warnings


def excluded(path: Path, excludes: list[str]) -> bool:
    return any(item and (item in path.parts or path.name == item) for item in excludes)


def snapshot_files(roots: list[Path], excludes: list[str]) -> dict[str, Any]:
    digest = hashlib.blake2b(digest_size=20)
    file_count = 0
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
                        if excluded(path, excludes):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            stat = entry.stat(follow_symlinks=False)
                        except OSError as exc:
                            if len(errors) < 20:
                                errors.append(f'{path}: {exc}')
                            continue
                        file_count += 1
                        digest.update(str(path).encode('utf-8', errors='surrogateescape'))
                        digest.update(b'\0')
                        digest.update(str(stat.st_size).encode())
                        digest.update(b'\0')
                        digest.update(str(stat.st_mtime_ns).encode())
                        digest.update(b'\n')
            except OSError as exc:
                if len(errors) < 20:
                    errors.append(f'{current}: {exc}')
    return {
        'digest': digest.hexdigest(),
        'file_count': file_count,
        'roots': [str(root) for root in roots],
        'checked_at': now_iso(),
        'errors': errors,
    }


def file_watch_cycle(executor: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    state_path = STATE_DIR / 'file_watch.json'
    roots, excludes, warnings = configured_scan_roots()
    current = snapshot_files(roots, excludes)
    previous = read_json(state_path, {})
    changed = bool(previous) and (
        previous.get('digest') != current.get('digest') or previous.get('file_count') != current.get('file_count')
    )
    result: dict[str, Any] = {
        'status': 'changed' if changed else ('baseline_created' if not previous else 'unchanged'),
        'file_count': current['file_count'],
        'roots': current['roots'],
        'warnings': warnings,
        'errors': current['errors'],
        'checked_at': current['checked_at'],
    }
    if changed:
        refresh = retry_action('full_update', executor)
        result['refresh'] = refresh
        if refresh.get('status') == 'ok':
            slack_notify('✅ KIO Local Agent: ファイル変更を検知し、Archive / Knowledge Engine / Assistant Pack を更新しました。')
        else:
            slack_notify(f"🚨 KIO Local Agent: ファイル変更後の更新に失敗しました。attempts={len(refresh.get('attempts', []))}")
    write_json(state_path, current)
    return result


def monitored_repos() -> list[str]:
    raw = os.environ.get('KIO_MONITORED_REPOS', DEFAULT_REPO)
    repos = [item.strip() for item in raw.split(',') if item.strip()]
    return repos or [DEFAULT_REPO]


def list_prs(repo: str) -> list[dict[str, Any]]:
    completed = run([
        'gh', 'pr', 'list', '--repo', repo, '--state', 'open', '--limit', '50', '--json',
        'number,title,url,headRefName,headRefOid,mergeStateStatus,reviewDecision,statusCheckRollup,updatedAt',
    ])
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f'gh pr list failed for {repo}')
    return json.loads(completed.stdout or '[]')


def check_failed(check: dict[str, Any]) -> bool:
    value = str(check.get('conclusion') or check.get('state') or '').upper()
    return value in {'FAILURE', 'FAILED', 'ERROR', 'TIMED_OUT', 'CANCELLED'}


def check_pending(check: dict[str, Any]) -> bool:
    value = str(check.get('status') or '').upper()
    return value in {'QUEUED', 'IN_PROGRESS', 'PENDING'}


def summarize_pr(repo: str, pr: dict[str, Any]) -> dict[str, Any]:
    checks = pr.get('statusCheckRollup') or []
    return {
        'repo': repo,
        'number': pr.get('number'),
        'title': pr.get('title'),
        'url': pr.get('url'),
        'headRefName': pr.get('headRefName'),
        'headRefOid': pr.get('headRefOid'),
        'mergeStateStatus': pr.get('mergeStateStatus'),
        'reviewDecision': pr.get('reviewDecision'),
        'failed_checks': [str(item.get('name') or item.get('context') or 'check') for item in checks if check_failed(item)],
        'pending_checks': [str(item.get('name') or item.get('context') or 'check') for item in checks if check_pending(item)],
        'updatedAt': pr.get('updatedAt'),
    }


def failed_runs(repo: str, pr: dict[str, Any]) -> list[dict[str, Any]]:
    branch = str(pr.get('headRefName') or '')
    sha = str(pr.get('headRefOid') or '')
    if not branch or not sha:
        return []
    completed = run([
        'gh', 'run', 'list', '--repo', repo, '--branch', branch, '--status', 'failure', '--limit', '10', '--json',
        'databaseId,headSha,workflowName,url',
    ])
    if completed.returncode != 0:
        return []
    return [item for item in json.loads(completed.stdout or '[]') if str(item.get('headSha') or '') == sha]


def retry_failed_runs(repo: str, pr: dict[str, Any], retry_state: dict[str, Any]) -> list[dict[str, Any]]:
    if not env_bool('KIO_AUTO_RETRY_GITHUB_ACTIONS', True):
        return []
    done = set(str(item) for item in retry_state.get('run_ids', []))
    results: list[dict[str, Any]] = []
    for item in failed_runs(repo, pr):
        run_id = str(item.get('databaseId') or '')
        if not run_id or run_id in done:
            continue
        completed = run(['gh', 'run', 'rerun', run_id, '--repo', repo, '--failed'])
        result = {
            'repo': repo,
            'pr': pr.get('number'),
            'run_id': run_id,
            'workflow': item.get('workflowName'),
            'status': 'ok' if completed.returncode == 0 else 'failed',
            'retried_at': now_iso(),
            'stderr': completed.stderr.strip()[-1000:],
        }
        results.append(result)
        if completed.returncode == 0:
            done.add(run_id)
    retry_state['run_ids'] = sorted(done)[-200:]
    return results


def pr_monitor_cycle() -> dict[str, Any]:
    state_path = STATE_DIR / 'pr_monitor.json'
    retry_path = STATE_DIR / 'github_retry_state.json'
    previous = read_json(state_path, {'prs': {}})
    retry_state = read_json(retry_path, {'run_ids': []})
    current: dict[str, Any] = {'checked_at': now_iso(), 'prs': {}}
    changes: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []
    errors: list[str] = []

    for repo in monitored_repos():
        try:
            prs = list_prs(repo)
        except Exception as exc:
            errors.append(f'{repo}: {exc}')
            continue
        for pr in prs:
            summary = summarize_pr(repo, pr)
            key = f"{repo}#{summary['number']}"
            current['prs'][key] = summary
            old = previous.get('prs', {}).get(key)
            if old != summary:
                changes.append({'key': key, 'previous': old, 'current': summary})
            if summary['failed_checks']:
                retries.extend(retry_failed_runs(repo, pr, retry_state))

    for key in sorted(set(previous.get('prs', {})) - set(current['prs'])):
        changes.append({'key': key, 'previous': previous['prs'][key], 'current': None})

    write_json(state_path, current)
    write_json(retry_path, retry_state)

    for change in changes:
        cur = change['current']
        if cur is None:
            slack_notify(f"✅ KIO GitHub Monitor: {change['key']} がOpen PR一覧から外れました（merge/closeの可能性）。")
        elif cur['failed_checks']:
            slack_notify(f"🚨 KIO GitHub Monitor: {change['key']} CI失敗: {', '.join(cur['failed_checks'])}\n{cur['url']}")
        elif cur.get('reviewDecision') == 'APPROVED' and not cur['pending_checks']:
            slack_notify(f"✅ KIO GitHub Monitor: {change['key']} 承認済み・CI待ちなし。\n{cur['url']}")
        elif change.get('previous') is None:
            slack_notify(f"ℹ️ KIO GitHub Monitor: 新しいPR {change['key']} {cur['title']}\n{cur['url']}")

    for item in retries:
        if item['status'] == 'ok':
            slack_notify(f"🔁 KIO GitHub Monitor: {item['repo']} PR #{item['pr']} の {item.get('workflow')} を1回だけ自動再実行しました。")
        else:
            slack_notify(f"🚨 KIO GitHub Monitor: {item['repo']} PR #{item['pr']} のWorkflow再実行に失敗しました。")

    return {
        'status': 'ok' if not errors else 'partial',
        'checked_at': current['checked_at'],
        'open_pr_count': len(current['prs']),
        'changes': changes,
        'retries': retries,
        'errors': errors,
    }


def automation_cycle(executor: Callable[[str], dict[str, Any]], gh_ready: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        result['file_watch'] = file_watch_cycle(executor)
    except Exception as exc:
        result['file_watch'] = {'status': 'failed', 'error': str(exc)}
        slack_notify(f'🚨 KIO Local Agent: ファイル監視処理で例外が発生しました: {exc}')
    if gh_ready:
        try:
            result['pr_monitor'] = pr_monitor_cycle()
        except Exception as exc:
            result['pr_monitor'] = {'status': 'failed', 'error': str(exc)}
            slack_notify(f'🚨 KIO GitHub Monitor: PR監視処理で例外が発生しました: {exc}')
    else:
        result['pr_monitor'] = {'status': 'skipped', 'reason': 'GitHub CLI is unavailable or unauthenticated'}
    return result
