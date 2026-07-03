from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import scan_archive


DEFAULT_STATE_DIR = "~/NakadachiArchiveAI/state"
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuously refresh the Nakadachi Archive AI knowledge base.")
    parser.add_argument("--config", default=scan_archive.default_config_path())
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one refresh or keep refreshing on an interval.")
    add_common_args(run_parser)
    run_parser.add_argument("--once", action="store_true", help="Run one refresh and exit.")
    run_parser.add_argument("--interval-seconds", type=int, default=None)
    run_parser.add_argument("--limit", type=int, default=None, help="Pass a file limit to the scanner.")
    run_parser.add_argument("--dry-run", action="store_true", help="Check paths without writing index files.")

    status_parser = subparsers.add_parser("status", help="Show auto-update state and configured scan roots.")
    add_common_args(status_parser)
    status_parser.add_argument("--format", choices=["text", "json", "markdown"], default="markdown")

    args = parser.parse_args()
    config = scan_archive.load_config(Path(args.config))
    state_dir = Path(args.state_dir).expanduser()

    if args.command == "status":
        render_status(build_status(config, state_dir), args.format)
        return 0

    interval = args.interval_seconds
    if interval is None:
        interval = int(config.get("auto_update_interval_seconds") or DEFAULT_INTERVAL_SECONDS)
    interval = max(60, interval)

    while True:
        run_result = run_refresh(
            project_dir=Path(__file__).resolve().parent.parent,
            state_dir=state_dir,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print(json.dumps(run_result, ensure_ascii=False))
        if args.once:
            return 0 if run_result["returncode"] == 0 else run_result["returncode"]
        time.sleep(interval)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS)
    parser.add_argument("--state-dir", default=argparse.SUPPRESS)


def run_refresh(
    project_dir: Path,
    state_dir: Path,
    limit: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "auto_update.lock"
    if lock_path.exists():
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {"started_at": "unknown"}
        return {
            "status": "skipped_locked",
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "message": f"Another refresh appears to be running: {existing}",
            "returncode": 0,
        }

    command = [str(project_dir / "scripts" / "run_full_update.sh")]
    if dry_run:
        command.append("--dry-run")
    if limit is not None:
        command.extend(["--limit", str(limit)])

    started_at = datetime.now().astimezone()
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": started_at.isoformat(timespec="seconds")}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        finished_at = datetime.now().astimezone()
        result = {
            "status": "ok" if completed.returncode == 0 else "failed",
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "returncode": completed.returncode,
            "command": command,
            "stdout_tail": tail_lines(completed.stdout),
            "stderr_tail": tail_lines(completed.stderr),
        }
        (state_dir / "last_run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        append_history(state_dir / "history.ndjson", result)
        return result
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def build_status(config: dict[str, Any], state_dir: Path) -> dict[str, Any]:
    warnings: list[str] = []
    roots = scan_archive.resolve_scan_paths(config.get("scan_paths", []), warnings)
    last_run_path = state_dir / "last_run.json"
    last_run = None
    if last_run_path.exists():
        try:
            last_run = json.loads(last_run_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            last_run = {"status": "unreadable_state"}

    return {
        "state_dir": str(state_dir),
        "configured_scan_paths": config.get("scan_paths", []),
        "active_scan_roots": [str(path) for path in roots],
        "warnings": warnings,
        "auto_update_interval_seconds": int(config.get("auto_update_interval_seconds") or DEFAULT_INTERVAL_SECONDS),
        "last_run": last_run,
    }


def render_status(status: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    if output_format == "markdown":
        print("# Nakadachi Archive AI Auto Update Status\n")
        print(f"- State dir: `{status['state_dir']}`")
        print(f"- Interval seconds: {status['auto_update_interval_seconds']}")
        print("\n## Active Scan Roots\n")
        if status["active_scan_roots"]:
            for root in status["active_scan_roots"]:
                print(f"- `{root}`")
        else:
            print("- None")
        if status["warnings"]:
            print("\n## Warnings\n")
            for warning in status["warnings"]:
                print(f"- {warning}")
        if status["last_run"]:
            print("\n## Last Run\n")
            print(f"- Status: {status['last_run'].get('status')}")
            print(f"- Started: {status['last_run'].get('started_at')}")
            print(f"- Finished: {status['last_run'].get('finished_at')}")
        return
    print(f"State dir: {status['state_dir']}")
    print(f"Active roots: {len(status['active_scan_roots'])}")
    print(f"Warnings: {len(status['warnings'])}")


def append_history(path: Path, result: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")


def tail_lines(text: str, limit: int = 40) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
