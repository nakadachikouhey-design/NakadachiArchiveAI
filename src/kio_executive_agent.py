from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import assistant_ai
import search_archive


DEFAULT_KPS_ROOT = "~/Documents/kio-project-system"
DEFAULT_STATE_DIR = "~/NakadachiArchiveAI/executive_state"
CASE_STATUSES = {"open", "waiting-decision", "in-progress", "completed", "cancelled"}
DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KIO Executive Agent: evidence retrieval, KPS case control, safe execution, and decision briefs."
    )
    parser.add_argument("--db", default=search_archive.DEFAULT_DB)
    parser.add_argument("--profiles", default=assistant_ai.DEFAULT_PROFILES)
    parser.add_argument("--kps-root", default=DEFAULT_KPS_ROOT)
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Create a managed case and retrieve evidence.")
    run_parser.add_argument("request")
    run_parser.add_argument("--project", default="")
    run_parser.add_argument("--project-id", default="PRJ-001")
    run_parser.add_argument("--due", default="")
    run_parser.add_argument("--limit", type=int, default=12)
    run_parser.add_argument("--execute-safe", action="store_true")
    run_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    status_parser = subparsers.add_parser("status", help="Show one case or the active case queue.")
    status_parser.add_argument("case_id", nargs="?")
    status_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    decision_parser = subparsers.add_parser("decision", help="Record a decision against a case.")
    decision_parser.add_argument("case_id")
    decision_parser.add_argument("decision")
    decision_parser.add_argument("--status", choices=sorted(DECISION_STATUSES), default="accepted")
    decision_parser.add_argument("--reason", required=True)
    decision_parser.add_argument("--review-date", default="")

    complete_parser = subparsers.add_parser("complete", help="Complete a case with a verified outcome.")
    complete_parser.add_argument("case_id")
    complete_parser.add_argument("outcome")

    validate_parser = subparsers.add_parser("validate", help="Validate configuration and KPS connectivity.")
    validate_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    args = parser.parse_args()
    state_dir = Path(args.state_dir).expanduser().resolve()
    kps_root = Path(args.kps_root).expanduser().resolve()

    if args.command == "run":
        result = create_case(args, state_dir, kps_root)
        render(result, args.format)
        return 0 if result.get("ok") else 1
    if args.command == "status":
        result = case_status(state_dir, args.case_id)
        render(result, args.format)
        return 0 if result.get("ok") else 1
    if args.command == "decision":
        result = record_decision(state_dir, args)
        render(result, "markdown")
        return 0 if result.get("ok") else 1
    if args.command == "complete":
        result = complete_case(state_dir, args.case_id, args.outcome)
        render(result, "markdown")
        return 0 if result.get("ok") else 1
    result = validate_installation(args, state_dir, kps_root)
    render(result, args.format)
    return 0 if result.get("ok") else 1


def create_case(args: argparse.Namespace, state_dir: Path, kps_root: Path) -> dict[str, Any]:
    due = normalize_date(args.due) if args.due else ""
    if args.due and not due:
        return {"ok": False, "error": "Due date must be YYYY-MM-DD."}

    db_path = search_archive.resolve_db_path(args.db)
    if not db_path.exists():
        return {"ok": False, "error": f"Knowledge Archive database not found: {db_path}"}

    profiles = assistant_ai.load_profiles(Path(args.profiles).expanduser().resolve())
    profile = assistant_ai.find_profile(profiles, args.project) if args.project else assistant_ai.infer_profile(profiles, args.request)
    with search_archive.connect_readonly(db_path) as db:
        db.row_factory = search_archive.sqlite3.Row
        pack = assistant_ai.build_request_context(db, profile, args.request, "auto", args.limit, db_path)

    now = timestamp()
    case_id = f"KIO-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    evidence = [evidence_ref(item) for item in pack.get("evidence", [])]
    case = {
        "schema_version": "1.0",
        "case_id": case_id,
        "kps_project_id": args.project_id,
        "project_profile": (profile or {}).get("id", "unassigned"),
        "request": args.request,
        "status": "in-progress" if args.execute_safe else "waiting-decision",
        "created_at": now,
        "updated_at": now,
        "due_date": due,
        "evidence": evidence,
        "facts": evidence_facts(evidence),
        "inferences": inference_notes(evidence),
        "actions": build_actions(pack, args.execute_safe),
        "decisions": [],
        "outcome": "",
        "decision_required": decision_required(pack, args.execute_safe),
    }
    if args.execute_safe:
        case["actions"] = execute_safe_actions(case["actions"], kps_root)
        case["status"] = "waiting-decision" if case["decision_required"] else "in-progress"
    save_case(state_dir, case)
    write_brief(state_dir, case)
    return {"ok": True, "case": case, "brief_path": str(brief_path(state_dir, case_id))}


def evidence_ref(item: dict[str, Any]) -> dict[str, Any]:
    excerpt = (item.get("text_excerpt") or item.get("ocr_text") or "").strip().replace("\n", " ")
    return {
        "file_name": item.get("file_name", ""),
        "path": item.get("full_path", ""),
        "category": item.get("ai_category", "unknown"),
        "confidence": item.get("ai_confidence"),
        "modified_at": item.get("modified_at", ""),
        "excerpt": excerpt[:360],
    }


def evidence_facts(evidence: list[dict[str, Any]]) -> list[str]:
    return [
        f"Knowledge Archiveで「{item['file_name']}」を確認（{item['path']}）。"
        for item in evidence[:8]
    ]


def inference_notes(evidence: list[dict[str, Any]]) -> list[str]:
    if not evidence:
        return ["関連資料を取得できていないため、現段階の判断確度は低い。"]
    if len(evidence) < 3:
        return ["関連資料が3件未満のため、単一資料への依存に注意が必要。"]
    return ["取得資料の関連度は検索結果に基づく。内容の正式版・承認状態は各正本で確認する必要がある。"]


def build_actions(pack: dict[str, Any], execute_safe: bool) -> list[dict[str, Any]]:
    actions = [
        {"id": "ACT-01", "type": "retrieve-evidence", "status": "completed", "result": f"{len(pack.get('evidence', []))}件取得"},
        {"id": "ACT-02", "type": "prepare-executive-brief", "status": "completed", "result": "事実・推測・判断事項を分離"},
        {"id": "ACT-03", "type": "validate-kps", "status": "pending" if execute_safe else "planned", "result": ""},
    ]
    return actions


def execute_safe_actions(actions: list[dict[str, Any]], kps_root: Path) -> list[dict[str, Any]]:
    for action in actions:
        if action["type"] != "validate-kps" or action["status"] != "pending":
            continue
        validator = kps_root / "scripts" / "validate_repository.py"
        if not validator.exists():
            action.update(status="blocked", result=f"KPS validator not found: {validator}")
            continue
        completed = subprocess.run(
            [sys.executable, str(validator)], cwd=kps_root, text=True, capture_output=True, check=False
        )
        output = (completed.stdout + completed.stderr).strip()
        action.update(
            status="completed" if completed.returncode == 0 else "failed",
            result=output[-1000:],
            exit_code=completed.returncode,
        )
    return actions


def decision_required(pack: dict[str, Any], execute_safe: bool) -> str:
    if not pack.get("evidence"):
        return "根拠資料なしで作業を進めるか、資料収集を優先するか。"
    if not execute_safe:
        return "取得した根拠に基づき、安全な検証作業を実行するか。"
    return "外部への送信・公開・契約・削除など、不可逆または対外的な作業を承認するか。"


def record_decision(state_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    case = load_case(state_dir, args.case_id)
    if not case:
        return {"ok": False, "error": f"Case not found: {args.case_id}"}
    review_date = normalize_date(args.review_date) if args.review_date else ""
    if args.review_date and not review_date:
        return {"ok": False, "error": "Review date must be YYYY-MM-DD."}
    decision = {
        "decision_id": f"{args.case_id}-DEC-{len(case['decisions']) + 1:02d}",
        "status": args.status,
        "decision": args.decision,
        "reason": args.reason,
        "review_date": review_date,
        "recorded_at": timestamp(),
    }
    case["decisions"].append(decision)
    case["decision_required"] = ""
    case["status"] = "in-progress" if args.status == "accepted" else "waiting-decision"
    case["updated_at"] = timestamp()
    save_case(state_dir, case)
    write_brief(state_dir, case)
    return {"ok": True, "case_id": args.case_id, "decision": decision}


def complete_case(state_dir: Path, case_id: str, outcome: str) -> dict[str, Any]:
    case = load_case(state_dir, case_id)
    if not case:
        return {"ok": False, "error": f"Case not found: {case_id}"}
    failed = [item for item in case.get("actions", []) if item.get("status") in {"failed", "blocked"}]
    if failed:
        return {"ok": False, "error": "Case has failed or blocked actions.", "actions": failed}
    case.update(status="completed", outcome=outcome, updated_at=timestamp(), decision_required="")
    save_case(state_dir, case)
    write_brief(state_dir, case)
    return {"ok": True, "case_id": case_id, "status": "completed", "outcome": outcome}


def case_status(state_dir: Path, case_id: str | None) -> dict[str, Any]:
    if case_id:
        case = load_case(state_dir, case_id)
        return {"ok": bool(case), "case": case} if case else {"ok": False, "error": f"Case not found: {case_id}"}
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(case_dir(state_dir).glob("*.json"))]
    active = [case for case in cases if case.get("status") not in {"completed", "cancelled"}]
    active.sort(key=lambda item: (item.get("due_date") or "9999-12-31", item.get("created_at", "")))
    return {"ok": True, "active_cases": active, "count": len(active)}


def validate_installation(args: argparse.Namespace, state_dir: Path, kps_root: Path) -> dict[str, Any]:
    db_path = search_archive.resolve_db_path(args.db)
    checks = {
        "knowledge_archive_database": db_path.exists(),
        "project_profiles": Path(args.profiles).expanduser().exists(),
        "kps_root": (kps_root / "README.md").exists(),
        "kps_validator": (kps_root / "scripts" / "validate_repository.py").exists(),
        "state_directory_writable": ensure_state_dir(state_dir),
    }
    return {"ok": all(checks.values()), "checks": checks, "paths": {"db": str(db_path), "kps": str(kps_root), "state": str(state_dir)}}


def save_case(state_dir: Path, case: dict[str, Any]) -> None:
    directory = case_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{case['case_id']}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def load_case(state_dir: Path, case_id: str) -> dict[str, Any] | None:
    target = case_dir(state_dir) / f"{case_id}.json"
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def write_brief(state_dir: Path, case: dict[str, Any]) -> None:
    path = brief_path(state_dir, case["case_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(case_to_markdown(case), encoding="utf-8")


def case_to_markdown(case: dict[str, Any]) -> str:
    lines = [
        f"# KIO Executive Brief: {case['case_id']}", "",
        f"- Status: {case['status']}",
        f"- KPS Project: {case['kps_project_id']}",
        f"- Due: {case.get('due_date') or 'Not set'}", "",
        "## 結論", "", case.get("outcome") or "実行中。最終判断待ち。", "",
        "## 確認できた事実", "",
    ]
    lines.extend(f"- {fact}" for fact in case.get("facts", []))
    lines.extend(["", "## 推測・制約", ""])
    lines.extend(f"- {item}" for item in case.get("inferences", []))
    lines.extend(["", "## 実行状況", ""])
    lines.extend(f"- {item['id']} {item['type']}: {item['status']} — {item.get('result', '')}" for item in case.get("actions", []))
    lines.extend(["", "## 中立公平の最終判断", "", case.get("decision_required") or "現時点で追加判断なし。", ""])
    return "\n".join(lines)


def render(result: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("case"):
        print(case_to_markdown(result["case"]))
        return
    if result.get("active_cases") is not None:
        print("# KIO Executive Agent Active Cases")
        for case in result["active_cases"]:
            print(f"- {case['case_id']} | {case['status']} | due={case.get('due_date') or '-'} | {case['request']}")
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


def case_dir(state_dir: Path) -> Path:
    return state_dir / "cases"


def brief_path(state_dir: Path, case_id: str) -> Path:
    return state_dir / "briefs" / f"{case_id}.md"


def ensure_state_dir(state_dir: Path) -> bool:
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        probe = state_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def normalize_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
