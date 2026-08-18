from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import kio_codex_repair as codex_repair

RUNTIME_DIR = Path(os.path.expanduser('~/NakadachiArchiveAI'))
STATE_DIR = RUNTIME_DIR / 'agent_state'
STATE_PATH = STATE_DIR / 'engineering_loop.json'
RETRY_STATE_PATH = STATE_DIR / 'github_retry_state.json'

RED_TERMS = (
    'public api safety', 'public-api', 'row level security', 'rls', 'service_role',
    'service role', 'anonymous reads', 'anon key', 'unpublished programme',
    'private/back-office', 'secret exposure', 'credential exposure',
)
YELLOW_TERMS = (
    'missing_live_', 'missing environment', 'secret is not set', 'permission denied',
    'not authenticated', 'unauthorized', 'forbidden', 'requires approval',
)
GREEN_TRANSIENT_TERMS = (
    'timed out', 'timeout', 'econnreset', 'connection reset', '502 bad gateway',
    '503 service unavailable', '504 gateway timeout', 'rate limit', 'runner lost',
    'network error', 'temporary failure', 'cancelled', 'canceled',
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ''
        stderr = exc.stderr if isinstance(exc.stderr, str) else ''
        return subprocess.CompletedProcess(cmd, 124, stdout, f'{stderr}\ncommand timed out')


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


def env_int(name: str, default: int, minimum: int = 0, maximum: int = 100) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def recent_failed_runs(repo: str) -> list[dict[str, Any]]:
    completed = run([
        'gh', 'run', 'list', '--repo', repo, '--status', 'failure', '--limit', '12', '--json',
        'databaseId,workflowName,headSha,headBranch,url,conclusion,createdAt',
    ], timeout=60)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f'gh run list failed for {repo}')
    return json.loads(completed.stdout or '[]')


def failed_log(repo: str, run_id: str) -> str:
    completed = run(['gh', 'run', 'view', run_id, '--repo', repo, '--log-failed'], timeout=120)
    text = (completed.stdout or '') + '\n' + (completed.stderr or '')
    return text[-24000:]


def classify(workflow: str, log: str) -> dict[str, str]:
    text = f'{workflow}\n{log}'.lower()
    if any(term in text for term in RED_TERMS):
        return {
            'level': 'RED',
            'category': 'security_or_publication_boundary',
            'action': 'human_approval_required',
            'reason': 'Security/Public API publication boundary is involved; automatic code/config relaxation is prohibited.',
        }
    if any(term in text for term in YELLOW_TERMS):
        return {
            'level': 'YELLOW',
            'category': 'environment_or_permission',
            'action': 'escalate',
            'reason': 'Environment, secret, authentication, permission, or approval is required.',
        }
    if any(term in text for term in GREEN_TRANSIENT_TERMS):
        return {
            'level': 'GREEN',
            'category': 'transient_infrastructure',
            'action': 'bounded_retry',
            'reason': 'Failure looks transient and is eligible for one bounded failed-job rerun.',
        }
    return {
        'level': 'YELLOW',
        'category': 'deterministic_code_or_test',
        'action': 'codex_repair_pr',
        'reason': 'A deterministic code/test failure is eligible for a constrained Codex repair proposal.',
    }


def existing_retry_ids() -> set[str]:
    state = read_json(RETRY_STATE_PATH, {'run_ids': []})
    return {str(item) for item in state.get('run_ids', [])}


def rerun_failed(repo: str, run_id: str) -> dict[str, Any]:
    completed = run(['gh', 'run', 'rerun', run_id, '--repo', repo, '--failed'], timeout=60)
    return {
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'stderr': completed.stderr.strip()[-1200:],
    }


def create_escalation_issue(
    repo: str,
    item: dict[str, Any],
    diagnosis: dict[str, str],
    log: str,
    repair: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_id = str(item.get('databaseId') or '')
    title = f"[KIO-LOOP][{diagnosis['level']}] {item.get('workflowName') or 'Workflow'} failure #{run_id}"
    excerpt = '\n'.join(log.splitlines()[-35:])[-7000:]
    repair_note = ''
    if repair:
        repair_note = (
            '\n### Automatic repair result\n'
            f"- Status: `{repair.get('status', 'unknown')}`\n"
            f"- Reason: {repair.get('reason', repair.get('error', ''))}\n"
        )
    body = (
        'KIO Engineering Loop escalation\n\n'
        f"- Level: **{diagnosis['level']}**\n"
        f"- Category: `{diagnosis['category']}`\n"
        f"- Required action: `{diagnosis['action']}`\n"
        f"- Reason: {diagnosis['reason']}\n"
        f"- Workflow run: {item.get('url') or ''}\n"
        f"- Head: `{item.get('headBranch') or ''}` / `{item.get('headSha') or ''}`\n"
        f"{repair_note}\n"
        '### Guardrail\n'
        'Do not bypass, skip, weaken, or redefine an acceptance/security gate to make CI pass. '
        'Public API safety, RLS, anonymous-access and publication-boundary failures require the original safety invariant to remain intact.\n\n'
        '### Failure excerpt\n```text\n' + excerpt + '\n```\n'
    )
    completed = run(['gh', 'issue', 'create', '--repo', repo, '--title', title, '--body', body], timeout=60)
    return {
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'url': completed.stdout.strip(),
        'stderr': completed.stderr.strip()[-1200:],
    }


def engineering_loop_cycle(repos: list[str], slack_notify: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    if not env_bool('KIO_ENGINEERING_LOOP_ENABLED', True):
        return {'status': 'disabled', 'checked_at': now_iso()}

    state = read_json(STATE_PATH, {})
    handled: dict[str, Any] = state.get('handled_runs', {}) if isinstance(state, dict) else {}
    baseline = not bool(state)
    discovered: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    errors: list[str] = []
    retry_ids = existing_retry_ids()
    repairs_remaining = env_int('KIO_ENGINEERING_MAX_CODE_REPAIRS_PER_CYCLE', 1, minimum=0, maximum=3)

    for repo in repos:
        try:
            runs = recent_failed_runs(repo)
        except Exception as exc:
            errors.append(f'{repo}: {exc}')
            continue
        for item in runs:
            run_id = str(item.get('databaseId') or '')
            if not run_id:
                continue
            key = f'{repo}#{run_id}'
            if key in handled:
                continue
            if baseline:
                handled[key] = {'status': 'baseline', 'seen_at': now_iso(), 'head_sha': item.get('headSha')}
                continue

            log = failed_log(repo, run_id)
            diagnosis = classify(str(item.get('workflowName') or ''), log)
            record: dict[str, Any] = {
                'key': key,
                'repo': repo,
                'run_id': run_id,
                'workflow': item.get('workflowName'),
                'url': item.get('url'),
                'head_branch': item.get('headBranch'),
                'head_sha': item.get('headSha'),
                'diagnosis': diagnosis,
                'diagnosed_at': now_iso(),
            }
            discovered.append(record)

            if diagnosis['level'] == 'GREEN' and env_bool('KIO_ENGINEERING_AUTO_REPAIR', True):
                if run_id in retry_ids:
                    action = {'type': 'bounded_retry', 'status': 'already_retried_by_pr_monitor'}
                else:
                    retry = rerun_failed(repo, run_id)
                    action = {'type': 'bounded_retry', **retry}
                record['action'] = action
                actions.append({'key': key, **action})
                slack_notify(f"🔁 KIO Engineering Loop GREEN: {repo} / {item.get('workflowName')} を限定再試行しました。\n{item.get('url')}")
            elif diagnosis['category'] == 'deterministic_code_or_test' and repairs_remaining > 0:
                repair = codex_repair.attempt_code_repair(repo, item, log)
                repairs_remaining -= 1
                record['action'] = {'type': 'codex_repair', **repair}
                actions.append({'key': key, 'type': 'codex_repair', **repair})
                if repair.get('status') == 'pr_created':
                    slack_notify(
                        f"🛠️ KIO Engineering Loop v2: {repo} のCI不具合をCodexで修正し、検証済みPRを作成しました。\n"
                        f"{repair.get('pr_url')}"
                    )
                else:
                    ticket = create_escalation_issue(repo, item, diagnosis, log, repair)
                    record['escalation'] = ticket
                    actions.append({'key': key, 'type': 'escalation_issue', **ticket})
                    slack_notify(
                        f"⚠️ KIO Engineering Loop v2: {repo} / {item.get('workflowName')} の自動修正を完了できませんでした。\n"
                        f"状態: {repair.get('status')}\n{item.get('url')}"
                    )
            else:
                ticket = create_escalation_issue(repo, item, diagnosis, log)
                record['action'] = {'type': 'escalation_issue', **ticket}
                actions.append({'key': key, 'type': 'escalation_issue', **ticket})
                icon = '🛑' if diagnosis['level'] == 'RED' else '⚠️'
                slack_notify(
                    f"{icon} KIO Engineering Loop {diagnosis['level']}: {repo} / {item.get('workflowName')}\n"
                    f"原因分類: {diagnosis['category']}\n{item.get('url')}"
                )
            handled[key] = record

    output = {
        'status': 'baseline_created' if baseline else ('ok' if not errors else 'partial'),
        'version': 'v2',
        'checked_at': now_iso(),
        'baseline': baseline,
        'discovered_failures': discovered,
        'actions': actions,
        'errors': errors,
    }
    write_json(STATE_PATH, {
        'version': 'v2',
        'checked_at': output['checked_at'],
        'handled_runs': dict(list(handled.items())[-300:]),
    })
    return output
