from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

RUNTIME_DIR = Path(os.path.expanduser('~/NakadachiArchiveAI'))
REPAIR_ROOT = RUNTIME_DIR / 'repair_workspaces'

PROTECTED_PREFIXES = ('.github/', 'supabase/', 'migrations/', '.codex/')
PROTECTED_NAMES = {
    'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lockb',
    'vercel.json', '.env', '.env.local', '.env.production', '.env.development',
}
PROTECTED_PATH_TERMS = (
    'public-api', 'public_api', 'rls', 'security', 'service-role', 'service_role',
    'auth/', 'authentication', 'permissions',
)
SELF_PROTECTED = {
    'src/kio_engineering_loop.py', 'src/kio_codex_repair.py', 'src/kio_node_agent.py',
    'src/kio_node_automation.py', 'scripts/install_kio_node_agent.sh',
}
SUSPICIOUS_DIFF_TERMS = (
    'describe.skip', 'it.skip', 'test.skip', 'xit(', 'xdescribe(', '|| true',
    'continue-on-error: true', 'if: false', 'public api safety', 'public-api',
    'row level security', 'service_role', 'service role', 'anonymous reads',
    'unpublished programme',
)
SECRET_ENV_TERMS = ('TOKEN', 'SECRET', 'KEY', 'PASSWORD', 'WEBHOOK', 'CREDENTIAL')


def run(
    cmd: list[str], cwd: Path | None = None, timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, cwd=cwd, text=True, capture_output=True, check=False,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ''
        stderr = exc.stderr if isinstance(exc.stderr, str) else ''
        return subprocess.CompletedProcess(cmd, 124, stdout, f'{stderr}\ncommand timed out')


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def sanitized_codex_env() -> dict[str, str]:
    safe = dict(os.environ)
    for name in list(safe):
        upper = name.upper()
        if any(term in upper for term in SECRET_ENV_TERMS):
            safe.pop(name, None)
    # Codex should use its saved CLI authentication. Secrets from the local node
    # are deliberately not inherited by the repair agent or its child commands.
    return safe


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
            'workspace': str(workspace), 'changes': dirty[:30],
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
        return {'status': 'failed', 'reason': 'checkout_failed', 'stderr': checkout.stderr.strip()[-2000:]}
    return {
        'status': 'ok', 'workspace': str(workspace), 'branch': branch,
        'base_branch': head_branch, 'head_sha': head_sha,
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


def added_diff_text(workspace: Path) -> str:
    completed = run(['git', 'diff', '--no-ext-diff', '--unified=0'], cwd=workspace, timeout=60)
    added = [
        line[1:] for line in completed.stdout.splitlines()
        if line.startswith('+') and not line.startswith('+++')
    ]
    for path in git_lines(workspace, 'ls-files', '--others', '--exclude-standard'):
        try:
            file_path = workspace / path
            if file_path.is_file() and file_path.stat().st_size <= 1_000_000:
                added.append(file_path.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            continue
    return '\n'.join(added).lower()


def inspect_repair_diff(workspace: Path, files: list[str]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    for path in files:
        reason = protected_path(path)
        if reason:
            violations.append({'path': path, 'reason': reason})
    added = added_diff_text(workspace)
    suspicious = [term for term in SUSPICIOUS_DIFF_TERMS if term in added]
    if violations or suspicious:
        return {
            'status': 'rejected', 'reason': 'repair diff violated Engineering Loop guardrails',
            'protected_files': violations, 'suspicious_terms': suspicious,
        }
    return {'status': 'ok'}


def codex_prompt(repo: str, item: dict[str, Any], log: str) -> str:
    excerpt = '\n'.join(log.splitlines()[-80:])[-12000:]
    return (
        'You are the KIO Engineering Loop repair agent. Fix only the deterministic code/test failure described below.\n\n'
        f'Repository: {repo}\nFailed workflow: {item.get("workflowName") or ""}\n'
        f'Failed run: {item.get("url") or ""}\nBase branch: {item.get("headBranch") or ""}\n'
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
    completed = run(
        ['codex', 'exec', '--ignore-user-config', '--sandbox', 'workspace-write', '--ephemeral', codex_prompt(repo, item, log)],
        cwd=workspace,
        timeout=env_int('KIO_CODEX_REPAIR_TIMEOUT_SECONDS', 1200, minimum=120, maximum=3600),
        env=sanitized_codex_env(),
    )
    return {
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'returncode': completed.returncode,
        'stdout_tail': completed.stdout.splitlines()[-30:],
        'stderr_tail': completed.stderr.splitlines()[-30:],
        'changed_files': changed_files(workspace),
    }


def run_validation_step(name: str, cmd: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    completed = run(cmd, cwd=cwd, timeout=timeout)
    return {
        'name': name, 'command': cmd,
        'status': 'ok' if completed.returncode == 0 else 'failed',
        'returncode': completed.returncode,
        'stdout_tail': completed.stdout.splitlines()[-25:],
        'stderr_tail': completed.stderr.splitlines()[-25:],
    }


def validate_node_app(app_dir: Path, label: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if not (app_dir / 'node_modules').is_dir():
        install = run_validation_step(f'{label}: npm ci --ignore-scripts', ['npm', 'ci', '--ignore-scripts'], app_dir, 600)
        steps.append(install)
        if install['status'] != 'ok':
            return steps
    for name, cmd in [
        (f'{label}: test', ['npm', 'test']),
        (f'{label}: lint', ['npm', 'run', 'lint']),
        (f'{label}: typecheck', ['npm', 'run', 'typecheck']),
    ]:
        step = run_validation_step(name, cmd, app_dir, 600)
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
        if (workspace / 'scripts' / 'validate_repository.py').exists():
            step = run_validation_step('KPS repository validator', ['python3', '-B', 'scripts/validate_repository.py'], workspace, 600)
            steps.append(step)
            if step['status'] != 'ok':
                return {'status': 'failed', 'steps': steps}
    elif repo == 'nakadachikouhey-design/NakadachiArchiveAI':
        step = run_validation_step('Python compileall', ['python3', '-m', 'compileall', '-q', 'src', 'scripts'], workspace, 300)
        steps.append(step)
        if step['status'] != 'ok':
            return {'status': 'failed', 'steps': steps}
    return {'status': 'ok', 'steps': steps}


def publish_repair_pr(repo: str, workspace: Path, prep: dict[str, Any], item: dict[str, Any], files: list[str]) -> dict[str, Any]:
    run_id = str(item.get('databaseId') or '')
    add = run(['git', 'add', '--', *files], cwd=workspace, timeout=60)
    if add.returncode != 0:
        return {'status': 'failed', 'reason': 'git_add_failed', 'stderr': add.stderr.strip()[-1600:]}
    commit = run(['git', 'commit', '-m', f'fix(kio-loop): repair CI run {run_id}'], cwd=workspace, timeout=60)
    if commit.returncode != 0:
        return {'status': 'failed', 'reason': 'git_commit_failed', 'stderr': commit.stderr.strip()[-1600:]}
    push = run(['git', 'push', '--force-with-lease', '-u', 'origin', str(prep['branch'])], cwd=workspace, timeout=180)
    if push.returncode != 0:
        return {'status': 'failed', 'reason': 'git_push_failed', 'stderr': push.stderr.strip()[-1600:]}

    body = (
        'Automated constrained repair proposed by **KIO Engineering Loop v2**.\n\n'
        f"- Source failed run: {item.get('url') or ''}\n- Base commit: `{item.get('headSha') or ''}`\n"
        f"- Changed files: {', '.join(files)}\n\n"
        '### Safety\n- No auto-merge.\n'
        '- Protected security/Public API/RLS/workflow/dependency surfaces are blocked by the local guardrail.\n'
        '- This PR was created only after local validation passed.\n- CI remains the final verifier.\n'
    )
    pr = run([
        'gh', 'pr', 'create', '--repo', repo, '--base', str(prep['base_branch']), '--head', str(prep['branch']),
        '--title', f'fix: KIO Engineering Loop repair for CI run {run_id}', '--body', body,
    ], cwd=workspace, timeout=60)
    return {
        'status': 'ok' if pr.returncode == 0 else 'failed', 'url': pr.stdout.strip(),
        'reason': '' if pr.returncode == 0 else 'pr_create_failed', 'stderr': pr.stderr.strip()[-1600:],
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
        return {'status': 'failed', 'reason': 'codex_exec_failed', 'codex': codex, 'workspace': str(workspace)}
    if not files:
        return {'status': 'no_change', 'reason': 'Codex produced no workspace changes within the guardrails.', 'codex': codex, 'workspace': str(workspace)}

    guardrail = inspect_repair_diff(workspace, files)
    if guardrail.get('status') != 'ok':
        return {'status': 'rejected', 'reason': guardrail.get('reason'), 'guardrail': guardrail, 'changed_files': files, 'workspace': str(workspace)}

    validation = validate_repair(repo, workspace, files)
    if validation.get('status') != 'ok':
        return {'status': 'validation_failed', 'reason': 'local validation did not pass; PR was not created', 'validation': validation, 'changed_files': files, 'workspace': str(workspace)}

    publication = publish_repair_pr(repo, workspace, prep, item, files)
    if publication.get('status') != 'ok':
        return {'status': 'failed', 'reason': publication.get('reason'), 'publication': publication, 'validation': validation, 'changed_files': files, 'workspace': str(workspace)}
    return {
        'status': 'pr_created', 'pr_url': publication.get('url'), 'branch': prep.get('branch'),
        'base_branch': prep.get('base_branch'), 'changed_files': files,
        'validation': validation, 'workspace': str(workspace),
    }
