from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

RUNTIME_DIR = Path(os.path.expanduser('~/NakadachiArchiveAI'))
STATE_DIR = RUNTIME_DIR / 'agent_state'
STATE_PATH = STATE_DIR / 'engineering_loop.json'
RETRY_STATE_PATH = STATE_DIR / 'github_retry_state.json'
REPAIR_ROOT = RUNTIME_DIR / 'repair_workspaces'

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

PROTECTED_PREFIXES = (
    '.github/', 'supabase/', 'migrations/', '.codex/',
)
PROTECTED_NAMES = {
    'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lockb',
    'vercel.json', '.env', '.env.local', '.env.production', '.env.development',
}
PROTECTED_PATH_TERMS = (
    'public-api', 'public_api', 'rls', 'security', 'service-role', 'service_role',
    'auth/', 'authentication', 'permissions',
)
SELF_PROTECTED = {
    'src/kio_engineering_loop.py',
    'src/kio_node_agent.py',
    'src/kio_node_automation.py',
    'scripts/install_kio_node_agent.sh',
}
SUSPICIOUS_DIFF_TERMS = (
    'describe.skip', 'it.skip', 'test.skip', 'xit(', 'xdescribe(', '|| true',
    'continue-on-error: true', 'if: false', 'public api safety', 'public-api',
    'row level security', 'service_role', 'service role', 'anonymous reads',
    'unpublished programme',
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=env,
        )
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
    ])
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


def repo_slug(repo: str) -> str:
    return repo.replace('/', '__').replace('..', '_')


def managed_workspace(repo: str) -> Path:
    return REPAIR_ROOT / repo_slug(repo)


def git_lines(workspace: Path, *args: str) -> list[str]:
    completed = run(['git', *args], cwd=workspace, timeout=60)
    if completed.returncode != 0:
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def prepare_workspace(repo: str, item: dict[str, Any]) -> dict[str, Any]:
    workspace = managed_workspace(repo)
    REPAIR_ROOT.mkdir(parents=True, exist_ok=True)
    if not (workspace / '.git').is_dir():
        if workspace.exists():
            return {'status': 'blocked', 'reason': f'non-git managed workspace exists: {workspace}'}
        clone = run(['gh', 'repo', 'clone', repo, str(workspace)], timeout=300)
        if clone.returncode != 0:
            return {'status': 'failed', 'reason': 'clone_failed', 'stderr': clone.stderr.strip()[-2000:]}

    dirty = git_lines(workspace, 'status', '--porcelain')
    if dirty:
        return {
            'status': 'blocked',
            'reason': 'managed repair workspace is dirty; refusing destructive reset',
            'workspace': str(workspace),
            'changes': dirty[:30],
        }

    fetch = run(['git', 'fetch', 'origin', '--prune'], cwd=workspace, timeout=180)
    if fetch.returncode != 0:
        return {'status': 'failed', 'reason': 'fetch_failed', 'stderr': fetch.stderr.strip()[-2000:]}

    head_branch = str(item.get('headBranch') or '').strip()
    head_sha = str(item.get('headSha') or '').strip()
    run_id = str(item.get('databaseId') or '').strip()
    if not head_branch or not head_sha or not run_id:
        return {'status': 'blocked', 'reason': 'missing failed-run head branch/sha/run id'}

    branch = f'kio-loop/fix-{run_id}'
    checkout = run(['git', 'checkout', '-B', branch, head_sha], cwd=workspace, timeout=60)
    if checkout.returncode != 0:
        fallback = run(['git', 'fetch', 'origin', head_branch], cwd=workspace, timeout=180)
        if fallback.returncode == 0:
            checkout = run(['git', 'checkout', '-B', branch, head_sha], cwd=workspace, timeout=60)
    if checkout.returncode != 0:
        return {
            'status': 'failed',
            'reason': 'checkout_failed',
            'stderr': checkout.stderr.strip()[-2000:],
        }
    return {
        'status': 'ok',
        'workspace': str(workspace),
        'branch': branch,
        'base_branch': head_branch,
        'head_sha': head_sha,
    }


def changed_files(workspace: Path) -> list[str]:
    names = git_lines(workspace, 'diff', '--name-only')
    untracked = git_lines(workspace, 'ls-files', '--others', '--exclude-standard')
    result: list[str] = []
    for name in [*names, *untracked]:
        if name not in result:
            result.append(name)
    return result


def protected_path(path: str) -> str | None:
    lowered = path.lower()
    if path in SELF_PROTECTED:
        return 'local_agent_control_plane'
    if path in PROTECTED_NAMES or Path(path).name in PROTECTED_NAMES:
        return 'dependency_or_environment_configuration'
    if any(lowered.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return 'security_or_delivery_configuration'
    if any(term in lowered for term in PROTECTED_PATH_TERMS):
        return 'security_sensitive_path'
    return None


def inspect_repair_diff(workspace: Path, files: list[str]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for path in files:
        reason = protected_path(path)
        if reason:
            violations.append({'path': path, 'reason': reason})
    diff = run(['git', 'diff', '--no-ext-diff', '--unified=0'], cwd=workspace, timeout=60)
    diff_text = (diff.stdout or '').lower()
    suspicious = [term for term in SUSPICIOUS_DIFF_TERMS if term in diff_text]
    if violations or suspicious:
        return {
            'status': 'rejected',
            'reason': 'repair diff violated Engineering Loop guardrails',
            'protected_files': violations,
            'suspicious_terms': suspicious,
        }
    return {'status': 'ok'}


def codex_prompt(repo: str, item: dict[str, Any], log: str) -> str:
    excerpt = '\n'.join(log.splitlines()[-80:])[-12000:]
    return (
        'You are the KIO Engineering Loop repair agent. Fix only the deterministic code/test failure described below.\n\n'
        f'Repository: {repo}\n'
        f'Failed workflow: {item.get("workflowName") or ""}\n'
        f'Failed run: {item.get("url") or ""}\n'
        f'Base branch: {item.get("headBranch") or ""}\n'
        f'Base commit: {item.get("headSha") or ""}\n\n'
        'Mandatory constraints:\n'
        '- Make the smallest production-quality change that fixes the actual defect.\n'
        '- Do not modify GitHub workflows, Supabase/migrations, environment files, dependency manifests/lockfiles, authentication/security/Public API/RLS code, or KIO local-agent control files.\n'
        '- Never skip, disable, loosen, mock away, or redefine a failing acceptance/security test just to make CI pass.\n'
        '- Public API safety, RLS, anonymous-access, service-role and publication-boundary invariants are immutable.\n'
        '- Do not commit, push, create a PR, merge, or change repository settings. The outer controller owns Git operations.\n'
        '- You may edit files and run focused local tests inside this workspace.\n'
        '- If the failure cannot be fixed within these constraints, leave the workspace unchanged and explain why.\n\n'
        'Failure log excerpt:\n```text\n' + excerpt + '\n```\n'
    )


def run_codex_repair(workspace: Path, repo: str, item: dict[str, Any], log: str) -> dict[str, Any]:
    if not shutil.which('codex'):
        return {'status': 'unavailable', 'reason': 'Codex CLI is not installed or not on PATH'}
    prompt = codex_prompt(repo, item, log)
    completed = run(
        ['codex', 'exec', '--sandbox', 'workspace-write', '--ephemeral', prompt],
        cwd=workspace,
        timeout=env_int('KIO_CODEX_REPAIR_TIMEOUT_SECONDS', 1200, minimum=120, maximum=3600),
    )
    files = changed_files(workspace)
    return {
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'returncode': completed.returncode,
        'stdout_tail': completed.stdout.splitlines()[-30:],
        'stderr_tail': completed.stderr.splitlines()[-30:],
        'changed_files': files,
    }


def run_validation_step(name: str, cmd: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    completed = run(cmd, cwd=cwd, timeout=timeout)
    return {
        'name': name,
        'command': cmd,
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'returncode': completed.returncode,
        'stdout_tail': completed.stdout.splitlines()[-25:],
        'stderr_tail': completed.stderr.splitlines()[-25:],
    }


def validate_node_app(app_dir: Path, label: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if not (app_dir / 'node_modules').is_dir():
        install = run_validation_step(
            f'{label}: npm ci --ignore-scripts', ['npm', 'ci', '--ignore-scripts'], app_dir, 600
        )
        steps.append(install)
        if install['status'] != 'ok':
            return steps
    for name, cmd, timeout in [
        (f'{label}: test', ['npm', 'test'], 600),
        (f'{label}: lint', ['npm', 'run', 'lint'], 600),
        (f'{label}: typecheck', ['npm', 'run', 'typecheck'], 600),
    ]:
        step = run_validation_step(name, cmd, app_dir, timeout)
        steps.append(step)
        if step['status'] != 'ok':
            break
    return steps


def validate_repair(repo: str, workspace: Path, files: list[str]) -> dict[str, Any]:
    steps = [run_validation_step('git diff --check', ['git', 'diff', '--check'], workspace, 60)]
    if steps[-1]['status'] != 'ok':
        return {'status': 'failed', 'steps': steps}

    if repo == 'nakadachikouhey-design/osaka-fringe-platform':
        roots = {path.split('/', 1)[0] for path in files if '/' in path}
        for root in ('audience-app', 'avr-app', 'monitoring'):
            if root in roots and (workspace / root / 'package.json').exists():
                app_steps = validate_node_app(workspace / root, root)
                steps.extend(app_steps)
                if app_steps and app_steps[-1]['status'] != 'ok':
                    return {'status': 'failed', 'steps': steps}
    elif repo == 'nakadachikouhey-design/kio-project-system':
        validator = workspace / 'scripts' / 'validate_repository.py'
        if validator.exists():
            step = run_validation_step(
                'KPS repository validator', ['python3', '-B', 'scripts/validate_repository.py'], workspace, 600
            )
            steps.append(step)
            if step['status'] != 'ok':
                return {'status': 'failed', 'steps': steps}
    elif repo == 'nakadachikouhey-design/NakadachiArchiveAI':
        step = run_validation_step(
            'Python compileall', ['python3', '-m', 'compileall', '-q', 'src', 'scripts'], workspace, 300
        )
        steps.append(step)
        if step['status'] != 'ok':
            return {'status': 'failed', 'steps': steps}

    return {'status': 'ok', 'steps': steps}


def publish_repair_pr(repo: str, workspace: Path, prep: dict[str, Any], item: dict[str, Any], files: list[str]) -> dict[str, Any]:
    run_id = str(item.get('databaseId') or '')
    add = run(['git', 'add', '--', *files], cwd=workspace, timeout=60)
    if add.returncode != 0:
        return {'status': 'failed', 'reason': 'git_add_failed', 'stderr': add.stderr.strip()[-1600:]}
    commit = run(
        ['git', 'commit', '-m', f'fix(kio-loop): repair CI run {run_id}'],
        cwd=workspace,
        timeout=60,
    )
    if commit.returncode != 0:
        return {'status': 'failed', 'reason': 'git_commit_failed', 'stderr': commit.stderr.strip()[-1600:]}
    push = run(
        ['git', 'push', '--force-with-lease', '-u', 'origin', str(prep['branch'])],
        cwd=workspace,
        timeout=180,
    )
    if push.returncode != 0:
        return {'status': 'failed', 'reason': 'git_push_failed', 'stderr': push.stderr.strip()[-1600:]}

    body = (
        'Automated constrained repair proposed by **KIO Engineering Loop v2**.\n\n'
        f"- Source failed run: {item.get('url') or ''}\n"
        f"- Base commit: `{item.get('headSha') or ''}`\n"
        f"- Changed files: {', '.join(files)}\n\n"
        '### Safety\n'
        '- No auto-merge.\n'
        '- Protected security/Public API/RLS/workflow/dependency surfaces are blocked by the local guardrail.\n'
        '- This PR was created only after local validation passed.\n'
        '- CI remains the final verifier.\n'
    )
    pr = run([
        'gh', 'pr', 'create', '--repo', repo,
        '--base', str(prep['base_branch']), '--head', str(prep['branch']),
        '--title', f'fix: KIO Engineering Loop repair for CI run {run_id}',
        '--body', body,
    ], cwd=workspace, timeout=60)
    return {
        'status': 'ok' if pr.returncode == 0 else 'failed',
        'url': pr.stdout.strip(),
        'reason': '' if pr.returncode == 0 else 'pr_create_failed',
        'stderr': pr.stderr.strip()[-1600:],
    }


def attempt_code_repair(repo: str, item: dict[str, Any], log: str) -> dict[str, Any]:
    if not env_bool('KIO_ENGINEERING_CODE_REPAIR_ENABLED', True):
        return {'status': 'disabled', 'reason': 'KIO_ENGINEERING_CODE_REPAIR_ENABLED is false'}

    prep = prepare_workspace(repo, item)
    if prep.get('status') != 'ok':
        return prep
    workspace = Path(str(prep['workspace']))

    codex = run_codex_repair(workspace, repo, item, log)
    files = changed_files(workspace)
    if codex.get('status') != 'ok':
        return {
            'status': 'failed',
            'reason': 'codex_exec_failed',
            'codex': codex,
            'workspace': str(workspace),
        }
    if not files:
        return {
            'status': 'no_change',
            'reason': 'Codex produced no workspace changes within the guardrails.',
            'codex': codex,
            'workspace': str(workspace),
        }

    guardrail = inspect_repair_diff(workspace, files)
    if guardrail.get('status') != 'ok':
        return {
            'status': 'rejected',
            'reason': guardrail.get('reason'),
            'guardrail': guardrail,
            'changed_files': files,
            'workspace': str(workspace),
        }

    validation = validate_repair(repo, workspace, files)
    if validation.get('status') != 'ok':
        return {
            'status': 'validation_failed',
            'reason': 'local validation did not pass; PR was not created',
            'validation': validation,
            'changed_files': files,
            'workspace': str(workspace),
        }

    publication = publish_repair_pr(repo, workspace, prep, item, files)
    if publication.get('status') != 'ok':
        return {
            'status': 'failed',
            'reason': publication.get('reason'),
            'publication': publication,
            'validation': validation,
            'changed_files': files,
            'workspace': str(workspace),
        }
    return {
        'status': 'pr_created',
        'pr_url': publication.get('url'),
        'branch': prep.get('branch'),
        'base_branch': prep.get('base_branch'),
        'changed_files': files,
        'validation': validation,
        'workspace': str(workspace),
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
                repair = attempt_code_repair(repo, item, log)
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
