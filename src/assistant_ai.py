from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import search_archive

DEFAULT_PROFILES = str(Path(__file__).resolve().parent.parent / "config" / "project_profiles.json")
DEFAULT_OUTPUT_DIR = "~/NakadachiArchiveAI/assistant_output"

TASKS = {
    "planning": {"label": "企画立案", "questions": ["何を実施するか", "誰に届けるか", "過去実績から何を継承するか", "今回の新規性は何か"], "outputs": ["企画趣旨", "対象者", "実施内容", "スケジュール", "必要資料"]},
    "marketing": {"label": "マーケティング", "questions": ["誰に届けるか", "何を価値として伝えるか", "過去の写真・記録をどう使うか", "告知導線は何か"], "outputs": ["訴求軸", "ターゲット", "告知文案", "SNS素材候補", "広報資料候補"]},
    "sales": {"label": "営業・提案", "questions": ["提案相手の関心は何か", "提示できる実績は何か", "相手にとってのメリットは何か"], "outputs": ["提案骨子", "営業先候補", "実績証拠", "提案メール下書き", "添付資料候補"]},
    "grant": {"label": "助成金申請", "questions": ["社会的意義は何か", "過去実績で証明できることは何か", "成果指標は何か", "予算根拠は何か"], "outputs": ["申請書骨子", "実績要約", "必要添付資料", "成果目標", "審査向け論点"]},
    "documents": {"label": "資料作成", "questions": ["誰に見せる資料か", "どの証拠資料を使うか", "1枚目で何を伝えるか"], "outputs": ["構成案", "引用資料候補", "図版候補", "不足資料", "確認事項"]},
    "presentation": {"label": "プレゼン資料作成", "questions": ["誰に何を決めてもらうか", "冒頭で何を伝えるか", "どの実績・写真・資料を根拠にするか"], "outputs": ["スライド構成", "各スライド要旨", "図版候補", "話す順番", "補足資料候補"]},
    "decision": {"label": "意思決定", "questions": ["選択肢は何か", "判断材料は何か", "リスクは何か", "今決めるべきことは何か"], "outputs": ["選択肢", "根拠資料", "リスク", "推奨判断", "次アクション"]},
}

REFERENCE_FILENAMES = {
    "AUTOMATION_PLAN.md", "BRAND_INDEX.md", "CHANGELOG.md", "KNOWLEDGE_INDEX.md",
    "PROJECT_INDEX.md", "REVIEW_LIST.md", "AI_CONSTITUTION.md", "AI_KNOWLEDGE_ENGINE.md",
}
REFERENCE_PATH_MARKERS = ("/00_CHURITSU_HUB/", "/assistant_output/", "/knowledge_engine/run_")
TEMP_PATH_MARKERS = ("/.codex_tmp/", "/.tmp/", "/tmp/", "/__pycache__/", "/previews/", "/previews_updated/", "/preview/")
CODE_EXTENSIONS = {".py", ".pyc", ".mjs", ".js", ".ts", ".sh", ".zsh"}
CODE_NAME_MARKERS = ("build_", "update_", "generate_", "sync_", "test_")
SIDECAR_SUFFIXES = (".inspect.ndjson", ".metadata.json", ".preview.json", ".ocr.json")
CROSS_PROJECT_MASTER_NAMES = {"KIO_実績マスター.xlsx", "KIO_実績マスター.xls", "KIO_実績マスター.csv"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Nakadachi Archive AI assistant layer.")
    parser.add_argument("--db", default=search_archive.DEFAULT_DB)
    parser.add_argument("--profiles", default=DEFAULT_PROFILES)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-projects")
    add_common_args(list_parser)
    list_parser.add_argument("--format", choices=["text", "json"], default="text")

    brief_parser = subparsers.add_parser("brief")
    add_common_args(brief_parser)
    brief_parser.add_argument("project")
    brief_parser.add_argument("--task", choices=["auto", *TASKS.keys(), "all"], default="auto")
    brief_parser.add_argument("--query", default="")
    brief_parser.add_argument("--limit", type=int, default=12)
    brief_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    brief_parser.add_argument("--save", action="store_true")

    ask_parser = subparsers.add_parser("ask")
    add_common_args(ask_parser)
    ask_parser.add_argument("request")
    ask_parser.add_argument("--project", default="")
    ask_parser.add_argument("--task", choices=["auto", *TASKS.keys(), "all"], default="auto")
    ask_parser.add_argument("--limit", type=int, default=12)
    ask_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ask_parser.add_argument("--save", action="store_true")

    build_parser = subparsers.add_parser("build-packs")
    add_common_args(build_parser)
    build_parser.add_argument("--task", choices=["auto", *TASKS.keys(), "all"], default="all")
    build_parser.add_argument("--limit", type=int, default=12)

    args = parser.parse_args()
    profiles = load_profiles(Path(args.profiles))
    if args.command == "list-projects":
        render_project_list(profiles, args.format)
        return 0

    db_path = search_archive.resolve_db_path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    with search_archive.connect_readonly(db_path) as db:
        db.row_factory = search_archive.sqlite3.Row
        if args.command == "brief":
            profile = find_profile(profiles, args.project)
            if not profile:
                print(f"Project profile not found: {args.project}")
                return 1
            pack = build_brief(db, profile, args.task, args.query, args.limit, db_path)
            render_or_save(pack, args.format, args.save, Path(args.output_dir).expanduser())
        elif args.command == "ask":
            profile = find_profile(profiles, args.project) if args.project else infer_profile(profiles, args.request)
            pack = build_request_context(db, profile, args.request, args.task, args.limit, db_path)
            render_or_save(pack, args.format, args.save, Path(args.output_dir).expanduser())
        else:
            output_dir = Path(args.output_dir).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)
            for profile in profiles:
                save_pack(build_brief(db, profile, args.task, "", args.limit, db_path), output_dir)
            print(f"Knowledge packs written to: {output_dir}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=argparse.SUPPRESS)
    parser.add_argument("--profiles", default=argparse.SUPPRESS)
    parser.add_argument("--output-dir", default=argparse.SUPPRESS)


def load_profiles(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["profiles"]


def find_profile(profiles: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    folded = query.casefold()
    for profile in profiles:
        values = [profile["id"], profile["name"], *(profile.get("aliases") or [])]
        if any(folded == str(value).casefold() for value in values):
            return profile
    for profile in profiles:
        values = [profile["id"], profile["name"], *(profile.get("aliases") or [])]
        if any(folded in str(value).casefold() or str(value).casefold() in folded for value in values):
            return profile
    return None


def infer_profile(profiles: list[dict[str, Any]], request: str) -> dict[str, Any] | None:
    folded = request.casefold()
    scores = []
    for profile in profiles:
        terms = [profile["name"], *(profile.get("aliases") or []), *(profile.get("keywords") or [])]
        score = sum(1 for term in terms if str(term).casefold() in folded)
        if score:
            scores.append((score, profile))
    return sorted(scores, key=lambda item: (-item[0], item[1]["id"]))[0][1] if scores else None


def profile_queries(profile: dict[str, Any]) -> list[str]:
    values: list[str] = [profile["name"]]
    values.extend(str(item) for item in profile.get("aliases") or [])
    values.extend(str(item) for item in profile.get("keywords") or [])
    values.extend(str(item) for item in profile.get("evidence_needs") or [])
    return list(dict.fromkeys(item.strip() for item in values if item and item.strip()))


def infer_task(text: str, default: str) -> str:
    folded = text.casefold()
    mapping = {
        "grant": ["助成", "申請", "補助金", "grant", "実績資料"],
        "marketing": ["マーケ", "広報", "告知", "sns", "観客", "集客", "publicity"],
        "sales": ["営業", "提案", "協賛", "スポンサー", "partner", "sales"],
        "planning": ["企画", "立案", "構想", "計画", "program", "planning"],
        "presentation": ["プレゼン", "スライド", "発表", "deck", "presentation"],
        "documents": ["資料", "文書", "企画書", "報告書", "document"],
        "decision": ["判断", "意思決定", "比較", "選択", "決める", "decision"],
    }
    scores = {task: sum(1 for keyword in words if keyword.casefold() in folded) for task, words in mapping.items()}
    task, score = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0]
    return task if score else default


def evidence_eligibility(result: dict[str, Any]) -> tuple[bool, str]:
    file_name = str(result.get("file_name") or "")
    full_path = str(result.get("full_path") or "")
    extension = str(result.get("extension") or Path(file_name).suffix).casefold()
    source_role = str(result.get("source_role") or "").casefold()
    path_folded = full_path.casefold()
    name_folded = file_name.casefold()

    if any(name_folded.endswith(suffix) for suffix in SIDECAR_SUFFIXES):
        return False, "derived_sidecar_metadata"
    if file_name.upper() in {name.upper() for name in REFERENCE_FILENAMES}:
        return False, "generated_index_or_hub_document"
    if any(marker.casefold() in path_folded for marker in REFERENCE_PATH_MARKERS):
        return False, "generated_reference_context"
    if any(marker.casefold() in path_folded for marker in TEMP_PATH_MARKERS):
        return False, "temporary_preview_or_working_file"
    if extension in CODE_EXTENSIONS and any(marker in name_folded for marker in CODE_NAME_MARKERS):
        return False, "implementation_script_not_business_evidence"
    if any(term in source_role for term in ("generated", "derived", "index", "ai")):
        return False, "derived_or_generated_source"
    return True, "eligible_primary_or_business_evidence"


def annotate_eligibility(result: dict[str, Any]) -> dict[str, Any]:
    eligible, reason = evidence_eligibility(result)
    result["evidence_eligible"] = eligible
    result["evidence_eligibility_reason"] = reason
    return result


def material_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("file_name", "full_path", "parent_folder", "text_excerpt", "ocr_text", "snippet", "ai_category", "ai_subcategory"):
        value = result.get(key)
        if value:
            parts.append(str(value))
    for key in ("project_candidates", "event_candidates", "generated_tags", "organization_candidates", "year_candidates"):
        value = result.get(key) or []
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts).casefold()


def project_relevance(result: dict[str, Any], profile: dict[str, Any] | None) -> tuple[bool, str, int]:
    if not profile:
        return True, "no_project_gate", 0

    file_name = str(result.get("file_name") or "")
    if file_name in CROSS_PROJECT_MASTER_NAMES:
        return True, "approved_cross_project_master", 100

    text = material_text(result)
    identity_terms = profile.get("identity_terms") or [profile.get("name", ""), *(profile.get("aliases") or [])]
    strict_identity = bool(profile.get("require_identity_match"))
    weak_terms = profile.get("keywords") or []
    evidence_terms = profile.get("evidence_needs") or []

    identity_hits = [str(term) for term in identity_terms if term and str(term).casefold() in text]
    weak_hits = [str(term) for term in weak_terms if term and len(str(term)) >= 3 and str(term).casefold() in text]
    evidence_hits = [str(term) for term in evidence_terms if term and str(term).casefold() in text]

    score = len(identity_hits) * 5 + min(len(weak_hits), 4) + min(len(evidence_hits), 2)
    result["project_relevance_score"] = score
    result["project_relevance_hits"] = {"identity": identity_hits[:8], "weak": weak_hits[:8], "evidence": evidence_hits[:8]}

    if identity_hits:
        return True, "project_identity_match", score
    if strict_identity:
        return False, "missing_required_project_identity", score
    if score >= 4:
        return True, "project_context_match", score
    return False, "insufficient_project_relevance", score


def annotate_project_relevance(result: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    relevant, reason, score = project_relevance(result, profile)
    result["project_relevant"] = relevant
    result["project_relevance_reason"] = reason
    result["project_relevance_score"] = score
    return result


def gather_candidates(db: Any, queries: list[str], limit: int, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    per_query_limit = max(30, limit * 5)
    for query in [item for item in queries if item.strip()]:
        args = argparse.Namespace(query=query, mode="all", limit=per_query_limit, format="json", category=None, media_type=None, source_role=None, extension=None, ocr_only=False)
        for raw in search_archive.search(db, args):
            result = annotate_project_relevance(annotate_eligibility(raw), profile)
            key = result["full_path"]
            existing = found.get(key)
            if not existing or float(result.get("score") or 0) > float(existing.get("score") or 0):
                result["retrieval_query"] = query
                found[key] = result
    return sorted(found.values(), key=lambda item: (-int(item.get("project_relevance_score") or 0), -float(item.get("score") or 0), item.get("file_name", "")))


def gather_evidence(db: Any, queries: list[str], limit: int, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    candidates = gather_candidates(db, queries, limit, profile)
    return [item for item in candidates if item.get("evidence_eligible") and item.get("project_relevant")][:limit]


def gather_reference_context(db: Any, queries: list[str], evidence: list[dict[str, Any]], limit: int, profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    references: dict[str, dict[str, Any]] = {}
    evidence_paths = {item.get("full_path") for item in evidence}
    for item in gather_candidates(db, queries, limit, profile):
        if item.get("full_path") not in evidence_paths and (not item.get("evidence_eligible") or not item.get("project_relevant")):
            references[item["full_path"]] = item
    for seed in evidence[:3]:
        args = argparse.Namespace(query="", path=seed["full_path"], id=None, limit=max(10, limit * 2), format="json")
        for raw in search_archive.related(db, args):
            result = annotate_project_relevance(annotate_eligibility(raw), profile)
            if result.get("full_path") not in evidence_paths:
                references.setdefault(result["full_path"], result)
    return sorted(references.values(), key=lambda item: (-int(item.get("project_relevance_score") or 0), -float(item.get("score") or 0), item.get("file_name", "")))[:limit]


def build_brief(db: Any, profile: dict[str, Any], task: str, extra_query: str, limit: int, db_path: Path) -> dict[str, Any]:
    queries = profile_queries(profile)
    if extra_query:
        queries.insert(0, extra_query)
    evidence = gather_evidence(db, queries, limit, profile)
    related = gather_reference_context(db, queries, evidence, max(5, limit // 2), profile)
    resolved_task = infer_task(extra_query, default="planning") if task == "auto" else task
    task_names = list(TASKS) if resolved_task == "all" else [resolved_task]
    return make_pack(f"{profile['name']} AI事業ブリーフ", profile, task_names, extra_query, evidence, related, db_path)


def build_request_context(db: Any, profile: dict[str, Any] | None, request: str, task: str, limit: int, db_path: Path) -> dict[str, Any]:
    queries = [request]
    if profile:
        queries.extend(profile_queries(profile))
    evidence = gather_evidence(db, queries, limit, profile)
    related = gather_reference_context(db, queries, evidence, max(5, limit // 2), profile)
    resolved_task = infer_task(request, default="decision") if task == "auto" else task
    task_names = list(TASKS) if resolved_task == "all" else [resolved_task]
    return make_pack("Nakadachi Archive AI 要求対応パック", profile, task_names, request, evidence, related, db_path)


def make_pack(title: str, profile: dict[str, Any] | None, task_names: list[str], request: str, evidence: list[dict[str, Any]], related: list[dict[str, Any]], db_path: Path) -> dict[str, Any]:
    return {
        "title": title,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "database": str(db_path),
        "profile": profile,
        "request": request,
        "tasks": [TASKS[name] | {"id": name} for name in task_names],
        "summary": {
            "evidence_count": len(evidence),
            "related_count": len(related),
            "categories": dict(Counter(item.get("ai_category") or "unknown" for item in evidence).most_common()),
            "media_types": dict(Counter(item.get("media_type_candidate") or "unknown" for item in evidence).most_common()),
        },
        "evidence": evidence,
        "related_materials": related,
        "assistant_instructions": assistant_instructions(profile, task_names),
        "draft_outputs": generate_draft_outputs(profile, task_names, request, evidence, related),
        "next_actions": next_actions(task_names),
    }


def assistant_instructions(profile: dict[str, Any] | None, task_names: list[str]) -> list[str]:
    name = profile["name"] if profile else "該当事業"
    labels = "、".join(TASKS[item]["label"] for item in task_names)
    return [
        f"{name}について、Evidence を事実根拠として扱い、Related Materials は補助文脈として扱う。",
        "Evidence は資格判定とプロジェクト関連性ゲートの両方を通過した資料だけを使う。",
        "Related Materials のみで事実を確定しない。資料パスを明示し、根拠がないことは推測として分ける。",
        f"今回の目的は {labels} の支援である。",
        "不足資料・確認事項・次に探すべき資料を最後に整理する。",
        "元ファイルの移動、削除、リネーム、編集は提案しない。",
    ]


def next_actions(task_names: list[str]) -> list[str]:
    actions = [f"{TASKS[name]['label']}: {', '.join(TASKS[name]['outputs'][:3])} を作成する" for name in task_names]
    actions.extend(["根拠資料のパスを脚注または参考資料として残す", "不足している証拠資料を追加検索する"])
    return actions


def render_project_list(profiles: list[dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(profiles, ensure_ascii=False, indent=2))
    else:
        for profile in profiles:
            print(f"{profile['id']} | {profile['name']} | aliases: {', '.join(profile.get('aliases') or [])}")


def render_or_save(pack: dict[str, Any], output_format: str, save: bool, output_dir: Path) -> None:
    if save:
        print(f"Wrote assistant pack: {save_pack(pack, output_dir)}")
    elif output_format == "json":
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        print(pack_to_markdown(pack))


def save_pack(pack: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_id = (pack.get("profile") or {}).get("id", "request")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{profile_id}_{timestamp}.md"
    path.write_text(pack_to_markdown(pack), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pack_to_markdown(pack: dict[str, Any]) -> str:
    lines = [f"# {pack['title']}", "", f"- Created: {pack['created_at']}", f"- Database: `{pack['database']}`"]
    profile = pack.get("profile")
    if profile:
        lines.extend([
            f"- Project: {profile['name']}",
            f"- Goals: {', '.join(profile.get('business_goals') or [])}",
            f"- Audiences: {', '.join(profile.get('audiences') or [])}",
            f"- Evidence needs: {', '.join(profile.get('evidence_needs') or [])}",
        ])
    if pack.get("request"):
        lines.append(f"- Request: {pack['request']}")
    lines.extend(["", "## Assistant Instructions", ""])
    lines.extend(f"- {item}" for item in pack["assistant_instructions"])
    lines.extend(["", "## Task Frames", ""])
    for task in pack["tasks"]:
        lines.extend([f"### {task['label']}", "", "Questions:"])
        lines.extend(f"- {item}" for item in task["questions"])
        lines.extend(["", "Expected outputs:"])
        lines.extend(f"- {item}" for item in task["outputs"])
        lines.append("")
    lines.extend(["", "## Evidence", ""])
    if not pack["evidence"]:
        lines.append("- No eligible project-relevant evidence found.")
    for index, item in enumerate(pack["evidence"], 1):
        lines.append(render_material(index, item))
    lines.extend(["", "## Related Materials / Reference Context", ""])
    if not pack["related_materials"]:
        lines.append("- No related reference context found.")
    for index, item in enumerate(pack["related_materials"], 1):
        lines.append(render_material(index, item))
    lines.extend(["", "## Draft Outputs", ""])
    for draft in pack.get("draft_outputs") or []:
        lines.append(render_draft(draft))
    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"- {item}" for item in pack["next_actions"])
    lines.extend([
        "", "## Prompt For ChatGPT Or Codex", "", "```text",
        "このパックの Evidence を事実根拠として、目的に沿った提案・文案・判断材料を作成してください。",
        "Evidence は資格判定とプロジェクト関連性ゲートを通過した資料だけです。",
        "Related Materials / Reference Context は補助文脈に限定し、それだけで事実を確定しないでください。",
        "根拠資料のパスを明示し、推測と事実を分け、不足資料を最後に列挙してください。",
        "元ファイルの移動・削除・リネーム・編集は提案しないでください。", "```", "",
    ])
    return "\n".join(lines)


def render_material(index: int, item: dict[str, Any]) -> str:
    lines = [
        f"### {index}. {item.get('file_name', '')}", "",
        f"- Path: `{item.get('full_path', '')}`",
        f"- Category: `{item.get('ai_category', '')}`",
        f"- Media type: `{item.get('media_type_candidate', '')}`",
        f"- Modified: `{item.get('modified_at', '')}`",
        f"- Score: `{item.get('score', '')}`",
    ]
    if "evidence_eligible" in item:
        lines.append(f"- Evidence eligible: `{item.get('evidence_eligible')}` ({item.get('evidence_eligibility_reason', '')})")
    if "project_relevant" in item:
        lines.append(f"- Project relevant: `{item.get('project_relevant')}` ({item.get('project_relevance_reason', '')}, score={item.get('project_relevance_score', 0)})")
    tags = item.get("generated_tags") or []
    if tags:
        lines.append(f"- Tags: {', '.join(str(tag) for tag in tags[:16])}")
    if item.get("snippet"):
        lines.extend(["", "Snippet:", "", str(item["snippet"])])
    lines.append("")
    return "\n".join(lines)


def generate_draft_outputs(profile: dict[str, Any] | None, task_names: list[str], request: str, evidence: list[dict[str, Any]], related: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [generate_task_draft(name, profile, request, evidence, related) for name in task_names]


def generate_task_draft(task_name: str, profile: dict[str, Any] | None, request: str, evidence: list[dict[str, Any]], related: list[dict[str, Any]]) -> dict[str, Any]:
    project_name = profile["name"] if profile else "対象プロジェクト"
    evidence_lines = evidence_bullets(evidence[:6])
    missing = missing_evidence(profile, evidence)
    audiences = ", ".join(profile.get("audiences") or ["関係者", "観客", "協働先"]) if profile else "関係者、観客、協働先"
    if task_name == "planning":
        body = [f"目的: {project_name}の過去資料を根拠に、次の企画の方向性を定める。", "対象者:", f"- {audiences}", "実施内容案:", "- Project-relevant Eligible Evidence から確認できる強みを中心に企画骨子を作る。", "根拠資料:", *evidence_lines]
    elif task_name == "marketing":
        body = [f"訴求軸: {project_name}の実績、文化的意義、記録性を前面に出す。", "ターゲット:", f"- {audiences}", "素材候補:", *media_bullets(evidence + related), "根拠資料:", *evidence_lines]
    elif task_name == "sales":
        body = ["提案骨子:", f"- {project_name}のProject-relevant Eligible Evidenceを実績根拠として提示する。", "添付資料候補:", *evidence_lines]
    elif task_name == "grant":
        body = ["申請書骨子:", f"- 事業名: {project_name}", "- Project-relevant Eligible Evidence の活動実績、報告書、予算資料、記録を根拠に説明する。", "必要添付資料候補:", *evidence_lines]
    elif task_name == "documents":
        body = ["資料構成案:", "1. 表紙", "2. 背景", "3. 実績", "4. 提案内容", "5. 体制・スケジュール", "6. 参考資料・証拠資料一覧", "引用・図版候補:", *evidence_lines]
    elif task_name == "presentation":
        body = ["スライド構成案:", "1. タイトル", "2. 背景", "3. 実績", "4. 企画", "5. 対象者・届け方", "6. 期待成果", "7. 協力・支援のお願い", "8. 参考資料", "図版候補:", *media_bullets(evidence + related), "根拠資料:", *evidence_lines]
    else:
        body = ["意思決定メモ:", f"- 判断対象: {request or project_name}", "- 推奨: Project-relevant Eligible Evidence がある範囲で進め、不足部分は推測として扱う。", "判断材料:", *evidence_lines]
    body.extend(["不足資料・追加検索候補:", *missing])
    return {"task": task_name, "label": TASKS[task_name]["label"], "body": body}


def evidence_bullets(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- 現在のインデックスでは直接使える根拠資料が不足している。"]
    return [f"- {item.get('file_name', '')} [{item.get('ai_category') or 'unknown'}/{item.get('media_type_candidate') or 'unknown'}] `{item.get('full_path', '')}`" for item in items]


def media_bullets(items: list[dict[str, Any]]) -> list[str]:
    media: list[dict[str, Any]] = []
    for item in items:
        if item.get("media_type_candidate") not in {"image", "video", "presentation", "document", "spreadsheet"}:
            continue
        if item.get("evidence_eligible") is False:
            continue
        if item.get("project_relevant") is False:
            continue
        path = str(item.get("full_path") or "").casefold()
        if "/preview" in path:
            continue
        media.append(item)
        if len(media) >= 6:
            break
    return evidence_bullets(media) if media else ["- 画像・動画・提案資料などの素材候補は追加検索が必要。"]


def missing_evidence(profile: dict[str, Any] | None, evidence: list[dict[str, Any]]) -> list[str]:
    needs = profile.get("evidence_needs") if profile else []
    if not needs:
        return ["- 目的別に不足資料を追加検索する。"]
    evidence_text = " ".join(str(item.get("file_name", "")) + " " + " ".join(map(str, item.get("generated_tags") or [])) for item in evidence)
    missing = [f"- {need}" for need in needs if str(need).casefold() not in evidence_text.casefold()]
    return missing or ["- 現時点の主要な証拠資料は一通り候補化されている。"]


def render_draft(draft: dict[str, Any]) -> str:
    return "\n".join([f"### {draft['label']}", "", *draft["body"], ""])


if __name__ == "__main__":
    raise SystemExit(main())
