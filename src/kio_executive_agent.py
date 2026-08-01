from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import assistant_ai
import search_archive


DEFAULT_KPS_ROOT = "~/Documents/kio-project-system"
CASE_STATUSES = {"open", "waiting-decision", "in-progress", "completed", "cancelled"}
DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}
CASE_ID_RE = re.compile(r"\AKIO-[A-Z0-9-]{3,64}\Z")
SAFE_ACTIONS = {"validate-kps", "refresh-archive-index", "build-knowledge-engine"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="KIO Executive Agent: verified evidence, KPS case control, safe execution, and final decisions."
    )
    add_runtime_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Create a KPS-managed case and retrieve evidence candidates.")
    add_runtime_args(run_parser, suppress_defaults=True)
    run_parser.add_argument("request")
    run_parser.add_argument("--project", default="")
    run_parser.add_argument("--project-id", default="PRJ-001")
    run_parser.add_argument("--due", default="")
    run_parser.add_argument("--limit", type=int, default=12)
    run_parser.add_argument("--execute-safe", action="store_true")
    run_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    status_parser = subparsers.add_parser("status", help="Show one case or the active case queue.")
    add_runtime_args(status_parser, suppress_defaults=True)
    status_parser.add_argument("case_id", nargs="?")
    status_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    verify_parser = subparsers.add_parser("verify-evidence", help="Promote one retrieved candidate to a verified fact.")
    add_runtime_args(verify_parser, suppress_defaults=True)
    verify_parser.add_argument("case_id")
    verify_parser.add_argument("evidence_id")
    verify_parser.add_argument("--fact", required=True, help="The exact claim supported by the checked source.")
    verify_parser.add_argument("--reason", required=True)
    verify_parser.add_argument("--verifier", required=True)
    verify_parser.add_argument("--sha256", default="")

    decision_parser = subparsers.add_parser("decision", help="Record a decision against a case.")
    add_runtime_args(decision_parser, suppress_defaults=True)
    decision_parser.add_argument("case_id")
    decision_parser.add_argument("decision")
    decision_parser.add_argument("--status", choices=sorted(DECISION_STATUSES), default="accepted")
    decision_parser.add_argument("--reason", required=True)
    decision_parser.add_argument("--review-date", default="")

    action_parser = subparsers.add_parser("action", help="Run one explicitly allowlisted local action.")
    add_runtime_args(action_parser, suppress_defaults=True)
    action_parser.add_argument("case_id")
    action_parser.add_argument("action", choices=sorted(SAFE_ACTIONS))

    record_action_parser = subparsers.add_parser("record-action", help="Record work performed by an approved chat connector or operator.")
    add_runtime_args(record_action_parser, suppress_defaults=True)
    record_action_parser.add_argument("case_id")
    record_action_parser.add_argument("action_type")
    record_action_parser.add_argument("--status", choices=["completed", "failed", "blocked"], required=True)
    record_action_parser.add_argument("--result", required=True)
    record_action_parser.add_argument("--external-effect", action="store_true")
    record_action_parser.add_argument("--decision-id", default="")

    complete_parser = subparsers.add_parser("complete", help="Complete a case with a verified outcome.")
    add_runtime_args(complete_parser, suppress_defaults=True)
    complete_parser.add_argument("case_id")
    complete_parser.add_argument("outcome")

    validate_parser = subparsers.add_parser("validate", help="Validate configuration and KPS connectivity.")
    add_runtime_args(validate_parser, suppress_defaults=True)
    validate_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")

    args = parser.parse_args(argv)
    kps_root = Path(args.kps_root).expanduser().resolve()
    state_dir = resolve_state_dir(getattr(args, "state_dir", ""), kps_root)

    if args.command == "run":
        result = create_case(args, state_dir, kps_root)
        output_format = args.format
    elif args.command == "status":
        result = case_status(state_dir, args.case_id)
        output_format = args.format
    elif args.command == "verify-evidence":
        result = verify_evidence(state_dir, args.case_id, args.evidence_id, args.fact, args.reason, args.verifier, args.sha256)
        output_format = "markdown"
    elif args.command == "decision":
        result = record_decision(state_dir, args)
        output_format = "markdown"
    elif args.command == "action":
        result = run_case_action(state_dir, args.case_id, args.action, kps_root)
        output_format = "markdown"
    elif args.command == "record-action":
        result = record_action_result(
            state_dir, args.case_id, args.action_type, args.status, args.result, args.external_effect, args.decision_id
        )
        output_format = "markdown"
    elif args.command == "complete":
        result = complete_case(state_dir, args.case_id, args.outcome)
        output_format = "markdown"
    else:
        result = validate_installation(args, state_dir, kps_root)
        output_format = args.format
    render(result, output_format)
    return 0 if result.get("ok") else 1


def add_runtime_args(parser: argparse.ArgumentParser, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else search_archive.DEFAULT_DB
    parser.add_argument("--db", default=default)
    default = argparse.SUPPRESS if suppress_defaults else assistant_ai.DEFAULT_PROFILES
    parser.add_argument("--profiles", default=default)
    default = argparse.SUPPRESS if suppress_defaults else DEFAULT_KPS_ROOT
    parser.add_argument("--kps-root", default=default)
    default = argparse.SUPPRESS if suppress_defaults else ""
    parser.add_argument(
        "--state-dir",
        default=default,
        help="Private ledger location. Defaults to <kps-root>/.kps-runtime/executive-agent.",
    )


def resolve_state_dir(value: str, kps_root: Path) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return (kps_root / ".kps-runtime" / "executive-agent").resolve()


def create_case(args: argparse.Namespace, state_dir: Path, kps_root: Path) -> dict[str, Any]:
    due = normalize_date(args.due) if args.due else ""
    if args.due and not due:
        return {"ok": False, "error": "Due date must be YYYY-MM-DD."}
    if not valid_project_id(args.project_id):
        return {"ok": False, "error": "Project ID must use the PRJ-000 format."}

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
    candidates = [evidence_ref(item, index) for index, item in enumerate(pack.get("evidence", []), start=1)]
    case = {
        "schema_version": "2.0",
        "case_id": case_id,
        "kps_project_id": args.project_id,
        "project_profile": (profile or {}).get("id", "unassigned"),
        "request": args.request,
        "status": "in-progress" if args.execute_safe else "waiting-decision",
        "created_at": now,
        "updated_at": now,
        "due_date": due,
        "deadline_status": deadline_status(due),
        "evidence_candidates": candidates,
        "facts": [f"Knowledge Archive indexを{now}に検索し、候補資料を{len(candidates)}件取得した。"],
        "inferences": inference_notes(candidates),
        "actions": build_actions(pack, args.execute_safe),
        "decisions": [],
        "outcome": "",
        "decision_required": decision_required(pack, args.execute_safe),
    }
    if args.execute_safe:
        case["actions"] = execute_planned_actions(case["actions"], kps_root)
        if any(item["status"] in {"failed", "blocked"} for item in case["actions"]):
            case["status"] = "waiting-decision"
    save_case(state_dir, case)
    return {"ok": True, "case": case, "brief_path": str(brief_path(state_dir, case_id)), "ledger": str(state_dir)}


def evidence_ref(item: dict[str, Any], index: int) -> dict[str, Any]:
    excerpt = (item.get("text_excerpt") or item.get("ocr_text") or "").strip().replace("\n", " ")
    return {
        "evidence_id": f"EVD-{index:02d}",
        "file_name": item.get("file_name", ""),
        "path": item.get("full_path", ""),
        "category": item.get("ai_category", "unknown"),
        "classification_confidence": item.get("ai_confidence"),
        "retrieval_score": item.get("score"),
        "modified_at": item.get("modified_at", ""),
        "excerpt": excerpt[:360],
        "verification_status": "candidate",
        "verified_at": "",
        "verified_by": "",
        "verification_reason": "",
        "verified_fact": "",
        "sha256": "",
    }


def inference_notes(evidence: list[dict[str, Any]]) -> list[str]:
    notes = ["検索順位・AI分類・抜粋は候補抽出であり、正式版・承認状態・本文の真偽を保証しない。"]
    if not evidence:
        notes.append("関連資料を取得できていないため、現段階の判断確度は低い。")
    elif len(evidence) < 3:
        notes.append("候補資料が3件未満のため、単一資料への依存に注意が必要。")
    return notes


def build_actions(pack: dict[str, Any], execute_safe: bool) -> list[dict[str, Any]]:
    return [
        {"id": "ACT-01", "type": "retrieve-evidence", "status": "completed", "result": f"{len(pack.get('evidence', []))}件を候補として取得"},
        {"id": "ACT-02", "type": "sync-kps-ledger", "status": "completed", "result": "KPS private runtime ledgerへ同期"},
        {"id": "ACT-03", "type": "prepare-executive-brief", "status": "completed", "result": "事実・推測・判断事項を分離"},
        {"id": "ACT-04", "type": "validate-kps", "status": "pending" if execute_safe else "planned", "result": ""},
    ]


def execute_planned_actions(actions: list[dict[str, Any]], kps_root: Path) -> list[dict[str, Any]]:
    for action in actions:
        if action["status"] != "pending":
            continue
        result = execute_allowlisted_action(action["type"], kps_root)
        action.update(result)
    return actions


def execute_allowlisted_action(action: str, kps_root: Path, archive_root: Path | None = None) -> dict[str, Any]:
    if action not in SAFE_ACTIONS:
        return {"status": "blocked", "result": f"Action is not allowlisted: {action}"}
    archive_root = archive_root or Path(__file__).resolve().parents[1]
    if action == "validate-kps":
        command = [sys.executable, str(kps_root / "scripts" / "validate_repository.py")]
        cwd = kps_root
    elif action == "refresh-archive-index":
        command = [str(archive_root / "scripts" / "run_auto_update.sh"), "--once"]
        cwd = archive_root
    else:
        command = [str(archive_root / "scripts" / "run_knowledge_engine.sh")]
        cwd = archive_root
    if not Path(command[1] if action == "validate-kps" else command[0]).exists():
        return {"status": "blocked", "result": f"Action executable not found: {command[-1]}"}
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"status": "failed", "result": "Action timed out after 1800 seconds.", "executed_at": timestamp()}
    except OSError as exc:
        return {"status": "failed", "result": f"Action could not start: {exc}", "executed_at": timestamp()}
    output = (completed.stdout + completed.stderr).strip()
    return {
        "status": "completed" if completed.returncode == 0 else "failed",
        "result": output[-2000:],
        "exit_code": completed.returncode,
        "executed_at": timestamp(),
    }


def run_case_action(state_dir: Path, case_id: str, action: str, kps_root: Path) -> dict[str, Any]:
    case = load_case(state_dir, case_id)
    if not case:
        return {"ok": False, "error": f"Case not found: {case_id}"}
    result = execute_allowlisted_action(action, kps_root)
    action_record = {
        "id": f"ACT-{len(case.get('actions', [])) + 1:02d}",
        "type": action,
        **result,
    }
    case.setdefault("actions", []).append(action_record)
    case["updated_at"] = timestamp()
    if result["status"] in {"failed", "blocked"}:
        case["status"] = "waiting-decision"
    save_case(state_dir, case)
    return {"ok": result["status"] == "completed", "case_id": case_id, "action": action_record}


def record_action_result(
    state_dir: Path,
    case_id: str,
    action_type: str,
    status: str,
    result: str,
    external_effect: bool = False,
    decision_id: str = "",
) -> dict[str, Any]:
    case = load_case(state_dir, case_id)
    if not case:
        return {"ok": False, "error": f"Case not found: {case_id}"}
    if status not in {"completed", "failed", "blocked"}:
        return {"ok": False, "error": f"Invalid action status: {status}"}
    if not action_type.strip() or not result.strip():
        return {"ok": False, "error": "Action type and result are required."}
    accepted = {
        item.get("decision_id")
        for item in case.get("decisions", [])
        if item.get("status") == "accepted"
    }
    if external_effect and decision_id not in accepted:
        return {"ok": False, "error": "External-effect actions require a matching accepted Decision ID."}
    action_record = {
        "id": f"ACT-{len(case.get('actions', [])) + 1:02d}",
        "type": action_type.strip(),
        "status": status,
        "result": result.strip(),
        "external_effect": external_effect,
        "authorization_decision_id": decision_id if external_effect else "",
        "recorded_at": timestamp(),
    }
    case.setdefault("actions", []).append(action_record)
    case["updated_at"] = timestamp()
    if status in {"failed", "blocked"}:
        case["status"] = "waiting-decision"
    save_case(state_dir, case)
    return {"ok": True, "case_id": case_id, "action": action_record}


def verify_evidence(
    state_dir: Path, case_id: str, evidence_id: str, fact: str, reason: str, verifier: str, expected_sha256: str = ""
) -> dict[str, Any]:
    case = load_case(state_dir, case_id)
    if not case:
        return {"ok": False, "error": f"Case not found: {case_id}"}
    candidate = next((item for item in case.get("evidence_candidates", []) if item.get("evidence_id") == evidence_id), None)
    if not candidate:
        return {"ok": False, "error": f"Evidence candidate not found: {evidence_id}"}
    if not fact.strip() or not reason.strip() or not verifier.strip():
        return {"ok": False, "error": "Verified fact, verification reason, and verifier are required."}
    source = Path(candidate.get("path", "")).expanduser()
    if not source.is_file():
        return {"ok": False, "error": f"Cannot verify evidence; source is unavailable: {source}"}
    actual = file_sha256(source)
    if expected_sha256 and actual.casefold() != expected_sha256.casefold():
        return {"ok": False, "error": "SHA-256 does not match the source file.", "actual_sha256": actual}
    candidate.update(
        verification_status="verified",
        verified_at=timestamp(),
        verified_by=verifier.strip(),
        verification_reason=reason.strip(),
        verified_fact=fact.strip(),
        sha256=actual,
    )
    fact_record = f"{fact.strip()}（根拠: {evidence_id}「{candidate['file_name']}」、確認者: {verifier.strip()}）"
    if fact_record not in case["facts"]:
        case["facts"].append(fact_record)
    case["updated_at"] = timestamp()
    save_case(state_dir, case)
    return {"ok": True, "case_id": case_id, "evidence": candidate, "fact": fact_record}


def decision_required(pack: dict[str, Any], execute_safe: bool) -> str:
    if not pack.get("evidence"):
        return "根拠候補なしで進めるか、資料収集を優先するか。"
    if not execute_safe:
        return "候補資料を検証し、安全なローカル作業を実行するか。"
    return "外部への送信・公開・契約・支払い・削除・権限変更を承認するか。"


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
    return {"ok": True, "case_id": args.case_id, "decision": decision}


def complete_case(state_dir: Path, case_id: str, outcome: str) -> dict[str, Any]:
    case = load_case(state_dir, case_id)
    if not case:
        return {"ok": False, "error": f"Case not found: {case_id}"}
    failed = [item for item in case.get("actions", []) if item.get("status") in {"failed", "blocked"}]
    if failed:
        return {"ok": False, "error": "Case has failed or blocked actions.", "actions": failed}
    if not any(item.get("verification_status") == "verified" for item in case.get("evidence_candidates", [])):
        return {"ok": False, "error": "At least one evidence candidate must be verified before completion."}
    case.update(status="completed", outcome=outcome, updated_at=timestamp(), decision_required="")
    case["deadline_status"] = deadline_status(case.get("due_date", ""))
    save_case(state_dir, case)
    return {"ok": True, "case_id": case_id, "status": "completed", "outcome": outcome}


def case_status(state_dir: Path, case_id: str | None) -> dict[str, Any]:
    if case_id:
        case = load_case(state_dir, case_id)
        if case:
            case["deadline_status"] = deadline_status(case.get("due_date", ""), case.get("status"))
        return {"ok": bool(case), "case": case} if case else {"ok": False, "error": f"Case not found: {case_id}"}
    ensure_state_dir(state_dir)
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(case_dir(state_dir).glob("*.json"))]
    active = [case for case in cases if case.get("status") not in {"completed", "cancelled"}]
    for case in active:
        case["deadline_status"] = deadline_status(case.get("due_date", ""), case.get("status"))
    active.sort(key=lambda item: (item.get("due_date") or "9999-12-31", item.get("created_at", "")))
    return {"ok": True, "active_cases": active, "count": len(active), "ledger": str(state_dir)}


def validate_installation(args: argparse.Namespace, state_dir: Path, kps_root: Path) -> dict[str, Any]:
    db_path = search_archive.resolve_db_path(args.db)
    checks = {
        "knowledge_archive_database": db_path.exists(),
        "project_profiles": Path(args.profiles).expanduser().exists(),
        "kps_root": (kps_root / "README.md").exists(),
        "kps_project_registry": (kps_root / "projects" / "registry.md").exists(),
        "kps_validator": (kps_root / "scripts" / "validate_repository.py").exists(),
        "ledger_is_inside_kps": is_relative_to(state_dir, kps_root / ".kps-runtime"),
        "state_directory_writable": ensure_state_dir(state_dir),
    }
    return {"ok": all(checks.values()), "checks": checks, "paths": {"db": str(db_path), "kps": str(kps_root), "state": str(state_dir)}}


def save_case(state_dir: Path, case: dict[str, Any]) -> None:
    case_id = str(case.get("case_id", ""))
    if not valid_case_id(case_id):
        raise ValueError(f"Invalid case ID: {case_id}")
    directory = case_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{case_id}.json"
    atomic_write(target, json.dumps(case, ensure_ascii=False, indent=2) + "\n")
    write_brief(state_dir, case)
    write_decisions(state_dir, case)
    rebuild_runtime_registry(state_dir)


def load_case(state_dir: Path, case_id: str) -> dict[str, Any] | None:
    if not valid_case_id(case_id):
        return None
    target = case_dir(state_dir) / f"{case_id}.json"
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else None


def write_brief(state_dir: Path, case: dict[str, Any]) -> None:
    path = brief_path(state_dir, case["case_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, case_to_markdown(case))


def write_decisions(state_dir: Path, case: dict[str, Any]) -> None:
    directory = state_dir / "decisions"
    directory.mkdir(parents=True, exist_ok=True)
    for decision in case.get("decisions", []):
        target = directory / f"{decision['decision_id']}.json"
        atomic_write(target, json.dumps({"case_id": case["case_id"], **decision}, ensure_ascii=False, indent=2) + "\n")


def rebuild_runtime_registry(state_dir: Path) -> None:
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(case_dir(state_dir).glob("*.json"))]
    registry = {
        "schema_version": "1.0",
        "updated_at": timestamp(),
        "cases": [
            {
                "case_id": case["case_id"],
                "kps_project_id": case.get("kps_project_id", ""),
                "status": case.get("status", ""),
                "due_date": case.get("due_date", ""),
                "deadline_status": deadline_status(case.get("due_date", ""), case.get("status")),
                "updated_at": case.get("updated_at", ""),
            }
            for case in cases
        ],
    }
    atomic_write(state_dir / "registry.json", json.dumps(registry, ensure_ascii=False, indent=2) + "\n")
    active = [case for case in cases if case.get("status") not in {"completed", "cancelled"}]
    active.sort(key=lambda item: (item.get("due_date") or "9999-12-31", item.get("created_at", "")))
    lines = ["# KIO Executive Agent Active Cases", "", "Private runtime ledger. Do not commit this file.", ""]
    lines.extend(
        f"- {case['case_id']} | {case['status']} | due={case.get('due_date') or '-'} | {case.get('request', '')}"
        for case in active
    )
    atomic_write(state_dir / "active-cases.md", "\n".join(lines) + "\n")


def case_to_markdown(case: dict[str, Any]) -> str:
    verified = [item for item in case.get("evidence_candidates", []) if item.get("verification_status") == "verified"]
    pending = [item for item in case.get("evidence_candidates", []) if item.get("verification_status") != "verified"]
    lines = [
        f"# KIO Executive Brief: {case['case_id']}", "",
        f"- Status: {case['status']}",
        f"- KPS Project: {case['kps_project_id']}",
        f"- Due: {case.get('due_date') or 'Not set'} ({deadline_status(case.get('due_date', ''), case.get('status'))})", "",
        "## 結論", "", case.get("outcome") or "実行中。最終判断待ち。", "",
        "## 確認できた事実", "",
    ]
    lines.extend(f"- {fact}" for fact in case.get("facts", []))
    lines.extend(["", "## 検証済み根拠", ""])
    lines.extend(f"- {item['evidence_id']} {item['file_name']} — {item.get('verified_fact', '')}" for item in verified)
    if not verified:
        lines.append("- なし")
    lines.extend(["", "## 未検証の根拠候補", ""])
    lines.extend(f"- {item['evidence_id']} {item['file_name']} — `{item['path']}`" for item in pending)
    if not pending:
        lines.append("- なし")
    lines.extend(["", "## 推測・制約", ""])
    lines.extend(f"- {item}" for item in case.get("inferences", []))
    lines.extend(["", "## 実行状況", ""])
    lines.extend(f"- {item['id']} {item['type']}: {item['status']} — {item.get('result', '')}" for item in case.get("actions", []))
    lines.extend(["", "## 中立公平の最終判断", "", case.get("decision_required") or "現時点で追加判断なし。", ""])
    return "\n".join(lines)


def render(result: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("case"):
        print(case_to_markdown(result["case"]))
    elif result.get("active_cases") is not None:
        print("# KIO Executive Agent Active Cases")
        for case in result["active_cases"]:
            print(f"- {case['case_id']} | {case['status']} | due={case.get('due_date') or '-'} | {case['deadline_status']} | {case['request']}")
    else:
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


def valid_case_id(value: str) -> bool:
    return bool(CASE_ID_RE.fullmatch(value))


def valid_project_id(value: str) -> bool:
    return bool(re.fullmatch(r"PRJ-\d{3,4}", value))


def deadline_status(value: str, status: str = "") -> str:
    if not value:
        return "not-set"
    if status in {"completed", "cancelled"}:
        return "closed"
    due = date.fromisoformat(value)
    today = date.today()
    if due < today:
        return "overdue"
    if due == today:
        return "due-today"
    return "scheduled"


def normalize_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
