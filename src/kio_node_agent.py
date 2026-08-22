from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import kio_engineering_loop as engineering
import kio_json_safe
import kio_node_automation as automation

PROJECT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.path.expanduser('~/NakadachiArchiveAI/agent_state'))
OWNER_LOGIN = 'nakadachikouhey-design'
REPO = 'nakadachikouhey-design/NakadachiArchiveAI'
TITLE_PREFIX = '[KIO-AGENT]'
DISPOSABLE_READ_MODELS = {'dashboard/data/dashboard.json'}
HEAVY_MAINTENANCE_STATE = STATE_DIR / 'heavy_maintenance.json'
DEFAULT_HEAVY_MAINTENANCE_INTERVAL_SECONDS = 21600

ACTIONS = {
    'full_update': ['scripts/run_full_update.sh'],
    'knowledge_update': ['scripts/run_knowledge_engine.sh'],
    'archive_update': ['scripts/run_archive.sh'],
    'assistant_build': ['scripts/run_assistant.sh', 'build-packs', '--task', 'all'],
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def run(cmd: list[str], cwd: Path = PROJECT_DIR) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def gh_available() -> bool:
    return run(['which', 'gh'], cwd=Path.home()).returncode == 0


def gh_authenticated() -> bool:
    return gh_available() and run(['gh', 'auth', 'status'], cwd=Path.home()).returncode == 0


def _porcelain_path(line: str) -> str:
    path = line[3:] if len(line) >= 4 else line
    if ' -> ' in path:
        path = path.split(' -> ', 1)[1]
    return path.strip()


def git_status() -> dict[str, Any]:
    branch = run(['git', 'branch', '--show-current']).stdout.strip()
    all_changes = [line for line in run(['git', 'status', '--porcelain']).stdout.splitlines() if line]
    blocking_changes = [line for line in all_changes if _porcelain_path(line) not in DISPOSABLE_READ_MODELS]
    generated_changes = [line for line in all_changes if _porcelain_path(line) in DISPOSABLE_READ_MODELS]
    remote = run(['git', 'remote', 'get-url', 'origin']).stdout.strip()
    return {
        'branch': branch,
        'clean': not blocking_changes,
        'changes': blocking_changes[:50],
        'generated_changes': generated_changes[:50],
        'remote': remote,
    }


def github_sync() -> dict[str, Any]:
    status = git_status()
    fetch = run(['git', 'fetch', 'origin', '--prune'])
    result: dict[str, Any] = {
        'action': 'github_sync',
        'started_at': now_iso(),
        'pre_status': status,
        'fetch_returncode': fetch.returncode,
        'fetch_stderr': fetch.stderr.strip()[-4000:],
    }
    if fetch.returncode != 0:
        result['status'] = 'failed'
        return result
    if not status['clean']:
        result['status'] = 'skipped_dirty_worktree'
        return result
    pull = run(['git', 'pull', '--ff-only'])
    result.update({
        'status': 'ok' if pull.returncode == 0 else 'failed',
        'pull_returncode': pull.returncode,
        'pull_stdout': pull.stdout.strip()[-4000:],
        'pull_stderr': pull.stderr.strip()[-4000:],
        'finished_at': now_iso(),
    })
    return result


def repo_status() -> dict[str, Any]:
    status = git_status()
    status.update({'action': 'repo_status', 'status': 'ok', 'checked_at': now_iso()})
    return status


def execute_action_once(action: str) -> dict[str, Any]:
    if action == 'repo_status':
        return repo_status()
    if action == 'github_sync':
        return github_sync()
    if action == 'engineering_loop':
        return engineering.engineering_loop_cycle(automation.monitored_repos(), automation.slack_notify)
    cmd = ACTIONS.get(action)
    if not cmd:
        return {'action': action, 'status': 'rejected', 'message': 'Action is not allowlisted.', 'finished_at': now_iso()}
    started = now_iso()
    completed = run([str(PROJECT_DIR / cmd[0]), *cmd[1:]])
    return {
        'action': action,
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'returncode': completed.returncode,
        'started_at': started,
        'finished_at': now_iso(),
        'stdout_tail': completed.stdout.splitlines()[-40:],
        'stderr_tail': completed.stderr.splitlines()[-40:],
    }


def execute_action(action: str) -> dict[str, Any]:
    return automation.retry_action(action, execute_action_once)


def list_agent_issues() -> list[dict[str, Any]]:
    query = [
        'gh', 'issue', 'list', '--repo', REPO, '--state', 'open',
        '--search', f'"{TITLE_PREFIX}" in:title', '--limit', '20',
        '--json', 'number,title,body,author,url',
    ]
    completed = run(query)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or 'gh issue list failed')
    return json.loads(completed.stdout or '[]')


def parse_task(issue: dict[str, Any]) -> dict[str, Any] | None:
    author = ((issue.get('author') or {}).get('login') or '').strip()
    if author != OWNER_LOGIN:
        return None
    try:
        task = json.loads((issue.get('body') or '').strip())
    except json.JSONDecodeError:
        return {'action': '__invalid_json__'}
    return task if isinstance(task, dict) else {'action': '__invalid_json__'}


def comment_and_close(number: int, result: dict[str, Any]) -> None:
    safe_result = kio_json_safe.make_json_safe(result)
    body = 'KIO local node result\n\n```json\n' + json.dumps(safe_result, ensure_ascii=False, indent=2) + '\n```'
    comment = run(['gh', 'issue', 'comment', str(number), '--repo', REPO, '--body', body])
    if comment.returncode != 0:
        raise RuntimeError(comment.stderr.strip() or 'gh issue comment failed')
    if safe_result.get('status') in {'ok', 'rejected', 'skipped_dirty_worktree', 'baseline_created', 'partial'}:
        close = run(['gh', 'issue', 'close', str(number), '--repo', REPO])
        if close.returncode != 0:
            raise RuntimeError(close.stderr.strip() or 'gh issue close failed')


def _heavy_maintenance_interval() -> int:
    raw = os.environ.get('KIO_HEAVY_MAINTENANCE_INTERVAL_SECONDS', str(DEFAULT_HEAVY_MAINTENANCE_INTERVAL_SECONDS))
    try:
        return max(3600, int(raw))
    except ValueError:
        return DEFAULT_HEAVY_MAINTENANCE_INTERVAL_SECONDS


def heavy_maintenance_cycle() -> dict[str, Any]:
    interval = _heavy_maintenance_interval()
    now_epoch = int(datetime.now().timestamp())
    state = automation.read_json(HEAVY_MAINTENANCE_STATE, {})
    try:
        last_attempt = int(state.get('last_attempt_epoch', 0))
    except (TypeError, ValueError):
        last_attempt = 0
    remaining = interval - (now_epoch - last_attempt)
    if last_attempt and remaining > 0:
        return {
            'status': 'deferred',
            'reason': 'heavy local maintenance is rate-limited',
            'interval_seconds': interval,
            'next_due_in_seconds': remaining,
            'last_attempt_at': state.get('last_attempt_at'),
        }

    attempt_state = {
        'last_attempt_epoch': now_epoch,
        'last_attempt_at': now_iso(),
        'interval_seconds': interval,
    }
    automation.write_json(HEAVY_MAINTENANCE_STATE, attempt_state)
    result = automation.file_watch_cycle(execute_action_once)
    attempt_state['last_result'] = kio_json_safe.make_json_safe(result)
    attempt_state['finished_at'] = now_iso()
    if result.get('status') in {'unchanged', 'baseline_created', 'changed'} and (result.get('refresh') or {}).get('status', 'ok') == 'ok':
        attempt_state['last_success_epoch'] = now_epoch
        attempt_state['last_success_at'] = attempt_state['finished_at']
    automation.write_json(HEAVY_MAINTENANCE_STATE, attempt_state)
    return result


def lightweight_automation_cycle(gh_ready: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if gh_ready:
        try:
            result['pr_monitor'] = automation.pr_monitor_cycle()
        except Exception as exc:
            result['pr_monitor'] = {'status': 'failed', 'error': str(exc)}
            automation.slack_notify(f'🚨 KIO GitHub Monitor: PR監視処理で例外が発生しました: {exc}')
    else:
        result['pr_monitor'] = {'status': 'skipped', 'reason': 'GitHub CLI is unavailable or unauthenticated'}

    try:
        result['heavy_maintenance'] = heavy_maintenance_cycle()
    except Exception as exc:
        result['heavy_maintenance'] = {'status': 'failed', 'error': str(exc)}
        automation.slack_notify(f'🚨 KIO Local Agent: 重いローカル保守処理で例外が発生しました: {exc}')
    return result


def cycle() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    gh_ready = gh_authenticated()
    heartbeat: dict[str, Any] = {
        'node': 'KIO-Mac-mini',
        'checked_at': now_iso(),
        'project_dir': str(PROJECT_DIR),
        'repo': REPO,
        'gh_available': gh_available(),
        'gh_authenticated': gh_ready,
        'processed': [],
        'automation': {'status': 'pending'},
        'engineering_loop': {'status': 'pending'},
        'warnings': [],
    }

    if not heartbeat['gh_available']:
        heartbeat['automation'] = {'status': 'skipped', 'reason': 'GitHub CLI unavailable'}
        heartbeat['engineering_loop'] = {'status': 'skipped', 'reason': 'GitHub CLI unavailable'}
        heartbeat['warnings'].append('GitHub CLI (gh) is unavailable; GitHub/PR monitoring is disabled.')
        write_heartbeat(heartbeat)
        return heartbeat
    if not gh_ready:
        heartbeat['automation'] = {'status': 'skipped', 'reason': 'GitHub CLI unauthenticated'}
        heartbeat['engineering_loop'] = {'status': 'skipped', 'reason': 'GitHub CLI unauthenticated'}
        heartbeat['warnings'].append('gh is installed but not authenticated.')
        write_heartbeat(heartbeat)
        return heartbeat

    # Cloud-issued commands always run before any local maintenance.
    try:
        issues = list_agent_issues()
    except Exception as exc:
        issues = []
        heartbeat['warnings'].append(str(exc))

    for issue in issues:
        task = parse_task(issue)
        if task is None:
            continue
        action = str(task.get('action', '')).strip()
        if action == '__invalid_json__' or not action:
            result = {'status': 'rejected', 'message': 'Issue body must be a JSON object with an action field.'}
        else:
            result = execute_action(action)
        result['issue_number'] = issue['number']
        safe_result = kio_json_safe.make_json_safe(result)
        heartbeat['processed'].append(safe_result)
        if safe_result.get('status') in {'ok', 'baseline_created'}:
            automation.slack_notify(f"✅ KIO Local Agent: {action} 完了 (Issue #{issue['number']})")
        elif safe_result.get('status') not in {'rejected', 'skipped_dirty_worktree'}:
            automation.slack_notify(f"🚨 KIO Local Agent: {action} 失敗/要確認 (Issue #{issue['number']})")
        try:
            comment_and_close(int(issue['number']), safe_result)
        except Exception as exc:
            heartbeat['warnings'].append(f"Issue #{issue['number']}: {exc}")
        write_heartbeat(heartbeat)

    # Ten-minute cycles stay lightweight. Heavy file scanning/full_update is
    # independently rate-limited (6h by default) and only refreshes on changes.
    heartbeat['automation'] = lightweight_automation_cycle(gh_ready)

    try:
        heartbeat['engineering_loop'] = engineering.engineering_loop_cycle(
            automation.monitored_repos(), automation.slack_notify
        )
    except Exception as exc:
        heartbeat['engineering_loop'] = {'status': 'failed', 'error': str(exc)}
        heartbeat['warnings'].append(f'Engineering Loop: {exc}')
        automation.slack_notify(f'🚨 KIO Engineering Loop: 例外が発生しました: {exc}')

    write_heartbeat(heartbeat)
    return kio_json_safe.make_json_safe(heartbeat)


def write_heartbeat(data: dict[str, Any]) -> None:
    automation.write_json(STATE_DIR / 'heartbeat.json', kio_json_safe.make_json_safe(data))


def main() -> int:
    parser = argparse.ArgumentParser(description='KIO always-on local node controller.')
    parser.add_argument('command', choices=['cycle', 'status', 'run', 'watch-files', 'monitor-prs', 'engineering-loop', 'notify-test'])
    parser.add_argument('action', nargs='?')
    args = parser.parse_args()

    if args.command == 'cycle':
        print(json.dumps(cycle(), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'status':
        heartbeat = STATE_DIR / 'heartbeat.json'
        if heartbeat.exists():
            print(heartbeat.read_text(encoding='utf-8'))
        else:
            print(json.dumps({'status': 'not_started', 'state_dir': str(STATE_DIR)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'watch-files':
        print(json.dumps(automation.file_watch_cycle(execute_action_once), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'monitor-prs':
        print(json.dumps(automation.pr_monitor_cycle(), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'engineering-loop':
        result = engineering.engineering_loop_cycle(automation.monitored_repos(), automation.slack_notify)
        print(json.dumps(kio_json_safe.make_json_safe(result), ensure_ascii=False, indent=2))
        return 0 if result.get('status') in {'ok', 'partial', 'baseline_created', 'disabled'} else 1
    if args.command == 'notify-test':
        result = automation.slack_notify('✅ KIO Local Agent: Slack通知テストに成功しました。')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('status') in {'ok', 'skipped'} else 1
    if not args.action:
        parser.error('run requires an action')
    result = execute_action(args.action)
    safe_result = kio_json_safe.make_json_safe(result)
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    return 0 if safe_result.get('status') in {'ok', 'rejected', 'skipped_dirty_worktree', 'baseline_created', 'partial'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
