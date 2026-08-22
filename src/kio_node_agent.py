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
    all_changes = run(['git', 'status', '--porcelain']).stdout.strip().splitlines()
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

    # Cloud-issued commands have priority over local maintenance. This keeps
    # lightweight requests such as repo_status responsive even when a local
    # file change would otherwise trigger a long full archive refresh.
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
        # Persist command evidence before entering potentially long maintenance.
        write_heartbeat(heartbeat)

    try:
        heartbeat['automation'] = automation.automation_cycle(execute_action_once, gh_ready)
    except Exception as exc:
        heartbeat['automation'] = {'status': 'failed', 'error': str(exc)}
        heartbeat['warnings'].append(f'Automation: {exc}')

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
