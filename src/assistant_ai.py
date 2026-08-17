from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from private_storage import ensure_private_directory, set_private_umask, write_private_text
import search_archive


DEFAULT_PROFILES = str(Path(__file__).resolve().parent.parent / "config" / "project_profiles.json")
DEFAULT_OUTPUT_DIR = "~/NakadachiArchiveAI/assistant_output"
TASKS = {
    "planning": {
        "label": "企画立案",
        "questions": ["何を実施するか", "誰に届けるか", "過去実績から何を継承するか", "今回の新規性は何か"],
        "outputs": ["企画趣旨", "対象者", "実施内容", "スケジュール", "必要資料"],
    },
    "marketing": {
        "label": "マーケティング",
        "questions": ["誰に届けるか", "何を価値として伝えるか", "過去の写真・記録をどう使うか", "告知導線は何か"],
        "outputs": ["訴求軸", "ターゲット", "告知文案", "SNS素材候補", "広報資料候補"],
    },
    "sales": {
        "label": "営業・提案",
        "questions": ["提案相手の関心は何か", "提示できる実績は何か", "相手にとってのメリットは何か"],
        "outputs": ["提案骨子", "営業先候補", "実績証拠", "提案メール下書き", "添付資料候補"],
    },
    "grant": {
        "label": "助成金申請",
        "questions": ["社会的意義は何か", "過去実績で証明できることは何か", "成果指標は何か", "予算根拠は何か"],
        "outputs": ["申請書骨子", "実績要約", "必要添付資料", "成果目標", "審査向け論点"],
    },
    "documents": {
        "label": "資料作成",
        "questions": ["誰に見せる資料か", "どの証拠資料を使うか", "1枚目で何を伝えるか"],
        "outputs": ["構成案", "引用資料候補", "図版候補", "不足資料", "確認事項"],
    },
    "presentation": {
        "label": "プレゼン資料作成",
        "questions": ["誰に何を決めてもらうか", "冒頭で何を伝えるか", "どの実績・写真・資料を根拠にするか"],
        "outputs": ["スライド構成", "各スライド要旨", "図版候補", "話す順番", "補足資料候補"],
    },
    "decision": {
        "label": "意思決定",
        "questions": ["選択肢は何か", "判断材料は何か", "リスクは何か", "今決めるべきことは何か"],
        "outputs": ["選択肢", "根拠資料", "リスク", "推奨判断", "次アクション"],
    },
}


def main() -> int:
    set_private_umask()
    parser = argparse.ArgumentParser(description="Nakadachi Archive AI assistant layer.")
    parser.add_argument("--db", default=search_archive.DEFAULT_DB)
    parser.add_argument("--profiles", default=DEFAULT_PROFILES)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-projects", help="List known project profiles.")
    add_common_args(list_parser)
    list_parser.add_argument("--format", choices=["text", "json"], default="text")

    brief_parser = subparsers.add_parser("brief", help="Build an AI-ready project/task brief.")
    add_common_args(brief_parser)
    brief_parser.add_argument("project", help="Project id, name, or alias.")
    brief_parser.add_argument("--task", choices=["auto", *TASKS.keys(), "all"], default="auto")
    brief_parser.add_argument("--query", default="", help="Additional retrieval query.")
    brief_parser.add_argument("--limit", type=int, default=12)
    brief_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    brief_parser.add_argument("--save", action="store_true")

    ask_parser = subparsers.add_parser("ask", help="Create an AI context packet and practical draft for a natural-language request.")
    add_common_args(ask_parser)
    ask_parser.add_argument("request")
    ask_parser.add_argument("--project", default="")
    ask_parser.add_argument("--task", choices=["auto", *TASKS.keys(), "all"], default="auto")
    ask_parser.add_argument("--limit", type=int, default=12)
    ask_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ask_parser.add_argument("--save", action="store_true")

    build_parser = subparsers.add_parser("build-packs", help="Build knowledge packs for every project profile.")
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
        elif args.command == "build-packs":
            output_dir = Path(args.output_dir).expanduser()
            ensure_private_directory(output_dir, harden_existing=True)
            for profile in profiles:
                pack = build_brief(db, profile, args.task, "", args.limit, db_path)
                save_pack(pack, output_dir)
            print(f"Knowledge packs written to: {output_dir}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=argparse.SUPPRESS)
    parser.add_argument("--profiles", default=argparse.SUPPRESS)
    parser.add_argument("--output-dir", default=argparse.SUPPRESS)


def load_profiles(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)["profiles"]


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
    scores: list[tuple[int, dict[str, Any]]] = []
    for profile in profiles:
        terms = [profile["name"], *(profile.get("aliases") or []), *(profile.get("keywords") or [])]
        score = sum(1 for term in terms if str(term).casefold() in folded)
        if score:
            scores.append((score, profile))
    if scores:
        return sorted(scores, key=lambda item: (-item[0], item[1]["id"]))[0][1]
    return None


def build_brief(
    db: Any,
    profile: dict[str, Any],
    task: str,
    extra_query: str,
    limit: int,
    db_path: Path,
) -> dict[str, Any]:
    queries = profile_queries(profile)
    if extra_query:
        queries.insert(0, extra_query)
    evidence = gather_evidence(db, queries, limit)
    related = gather_related(db, evidence[:3], limit=max(5, limit // 2))
    resolved_task = infer_task(extra_query, default="planning") if task == "auto" else task
    tasks = list(TASKS) if resolved_task == "all" else [resolved_task]
    return make_pack(
        title=f"{profile['name']} AI事業ブリーフ",
        profile=profile,
        task_names=tasks,
        request=extra_query,
        evidence=evidence,
        related=related,
        db_path=db_path,
    )


def build_request_context(
    db: Any,
    profile: dict[str, Any] | None,
    request: str,
    task: str,
    limit: int,
    db_path: Path,
) -> dict[str, Any]:
    queries = [request]
    if profile:
        queries.extend(profile_queries(profile))
    evidence = gather_evidence(db, queries, limit)
    related = gather_related(db, evidence[:3], limit=max(5, limit // 2))
    resolved_task = infer_task(request, default="decision") if task == "auto" else task
    tasks = list(TASKS) if resolved_task == "all" else [resolved_task]
    return make_pack(
        title="Nakadachi Archive AI 要求対応パック",
        profile=profile,
        task_names=tasks,
        request=request,
        evidence=evidence,
        related=related,
        db_path=db_path,
    )


def profile_queries(profile: dict[str, Any]) -> list[str]:
    return [
        profile["name"],
        " ".join(profile.get("aliases") or []),
        " ".join(profile.get("keywords") or []),
        " ".join(profile.get("evidence_needs") or []),
    ]


def infer_task(text: str, default: str) -> str:
    folded = text.casefold()
    task_keywords = {
        "grant": ["助成", "申請", "補助金", "grant", "実績資料"],
        "marketing": ["マーケ", "広報", "告知", "sns", "観客", "集客", "publicity"],
        "sales": ["営業", "提案", "協賛", "スポンサー", "partner", "sales"],
        "planning": ["企画", "立案", "構想", "計画", "program", "planning"],
        "presentation": ["プレゼン", "スライド", "発表", "deck", "presentation"],
        "documents": ["資料", "スライド", "文書", "企画書", "報告書", "document"],
        "decision": ["判断", "意思決定", "比較", "選択", "決める", "decision"],
    }
    scores = {
        task: sum(1 for keyword in keywords if keyword.casefold() in folded)
        for task, keywords in task_keywords.items()
    }
    best_task, best_score = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0]
    return best_task if best_score else default


def gather_evidence(db: Any, queries: list[str], limit: int) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    per_query_limit = max(5, limit)
    for query in [item for item in queries if item.strip()]:
        args = argparse.Namespace(
            query=query,
            mode="all",
            limit=per_query_limit,
            format="json",
            category=None,
            media_type=None,
            source_role=None,
            extension=None,
            ocr_only=False,
        )
        for result in search_archive.search(db, args):
            key = result["full_path"]
            existing = found.get(key)
            if not existing or result["score"] > existing["score"]:
                result["retrieval_query"] = query
                found[key] = result
    return sorted(found.values(), key=lambda item: (-float(item.get("score") or 0), item["file_name"]))[:limit]


def gather_related(db: Any, seeds: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    related: dict[str, dict[str, Any]] = {}
    for seed in seeds:
        args = argparse.Namespace(query="", path=seed["full_path"], id=None, limit=limit, format="json")
        for result in search_archive.related(db, args):
            if result["full_path"] not in related:
                related[result["full_path"]] = result
    return sorted(related.values(), key=lambda item: (-float(item.get("score") or 0), item["file_name"]))[:limit]


def make_pack(
    title: str,
    profile: dict[str, Any] | None,
    task_names: list[str],
    request: str,
    evidence: list[dict[str, Any]],
    related: list[dict[str, Any]],
    db_path: Path,
) -> dict[str, Any]:
    categories = Counter(item.get("ai_category") or "unknown" for item in evidence)
    media_types = Counter(item.get("media_type_candidate") or "unknown" for item in evidence)
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
            "categories": dict(categories.most_common()),
            "media_types": dict(media_types.most_common()),
        },
        "evidence": evidence,
        "related_materials": related,
        "assistant_instructions": assistant_instructions(profile, task_names),
        "draft_outputs": generate_draft_outputs(profile, task_names, request, evidence, related),
        "next_actions": next_actions(task_names),
    }


def assistant_instructions(profile: dict[str, Any] | None, task_names: list[str]) -> list[str]:
    name = profile["name"] if profile else "該当事業"
    task_labels = "、".join(TASKS[name]["label"] for name in task_names)
    return [
        f"{name}について、添付された evidence と related_materials を根拠として扱う。",
        "資料パスを明示し、根拠がないことは推測として分ける。",
        f"今回の目的は {task_labels} の支援である。",
        "不足資料・確認事項・次に探すべき資料を最後に整理する。",
        "元ファイルの移動、削除、リネーム、編集は提案しない。",
    ]


def next_actions(task_names: list[str]) -> list[str]:
    actions = []
    for task_name in task_names:
        task = TASKS[task_name]
        actions.append(f"{task['label']}: {', '.join(task['outputs'][:3])} を作成する")
    actions.append("根拠資料のパスを脚注または参考資料として残す")
    actions.append("不足している証拠資料を追加検索する")
    return actions


def render_project_list(profiles: list[dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(profiles, ensure_ascii=False, indent=2))
        return
    for profile in profiles:
        print(f"{profile['id']} | {profile['name']} | aliases: {', '.join(profile.get('aliases') or [])}")


def render_or_save(pack: dict[str, Any], output_format: str, save: bool, output_dir: Path) -> None:
    if save:
        path = save_pack(pack, output_dir)
        print(f"Wrote assistant pack: {path}")
        return
    if output_format == "json":
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        print(pack_to_markdown(pack))


def save_pack(pack: dict[str, Any], output_dir: Path) -> Path:
    ensure_private_directory(output_dir, harden_existing=True)
    profile = pack.get("profile") or {}
    profile_id = profile.get("id", "request")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{profile_id}_{timestamp}.md"
    write_private_text(path, pack_to_markdown(pack))
    json_path = path.with_suffix(".json")
    write_private_text(json_path, json.dumps(pack, ensure_ascii=False, indent=2))
    return path


def pack_to_markdown(pack: dict[str, Any]) -> str:
    lines = [
        f"# {pack['title']}",
        "",
        f"- Created: {pack['created_at']}",
        f"- Database: `{pack['database']}`",
    ]
    profile = pack.get("profile")
    if profile:
        lines.extend(
            [
                f"- Project: {profile['name']}",
                f"- Goals: {', '.join(profile.get('business_goals') or [])}",
                f"- Audiences: {', '.join(profile.get('audiences') or [])}",
                f"- Evidence needs: {', '.join(profile.get('evidence_needs') or [])}",
            ]
        )
    if pack.get("request"):
        lines.append(f"- Request: {pack['request']}")

    lines.extend(["", "## Assistant Instructions", ""])
    lines.extend(f"- {item}" for item in pack["assistant_instructions"])

    lines.extend(["", "## Task Frames", ""])
    for task in pack["tasks"]:
        lines.append(f"### {task['label']}")
        lines.append("")
        lines.append("Questions:")
        lines.extend(f"- {item}" for item in task["questions"])
        lines.append("")
        lines.append("Expected outputs:")
        lines.extend(f"- {item}" for item in task["outputs"])
        lines.append("")

    lines.extend(["", "## Evidence", ""])
    if not pack["evidence"]:
        lines.append("- No evidence found.")
    for index, item in enumerate(pack["evidence"], start=1):
        lines.append(render_material(index, item))

    lines.extend(["", "## Related Materials", ""])
    if not pack["related_materials"]:
        lines.append("- No related materials found.")
    for index, item in enumerate(pack["related_materials"], start=1):
        lines.append(render_material(index, item))

    lines.extend(["", "## Draft Outputs", ""])
    for draft in pack.get("draft_outputs") or []:
        lines.append(render_draft(draft))

    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"- {item}" for item in pack["next_actions"])

    lines.extend(["", "## Prompt For ChatGPT Or Codex", ""])
    lines.append("```text")
    lines.append("このパックの Evidence と Related Materials だけを根拠に、目的に沿った提案・文案・判断材料を作成してください。")
    lines.append("根拠資料のパスを明示し、推測と事実を分け、不足資料を最後に列挙してください。")
    lines.append("元ファイルの移動・削除・リネーム・編集は提案しないでください。")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def render_material(index: int, item: dict[str, Any]) -> str:
    lines = [
        f"### {index}. {item.get('file_name', '')}",
        "",
        f"- Path: `{item.get('full_path', '')}`",
        f"- Category: `{item.get('ai_category', '')}`",
        f"- Media type: `{item.get('media_type_candidate', '')}`",
        f"- Modified: `{item.get('modified_at', '')}`",
        f"- Score: `{item.get('score', '')}`",
    ]
    tags = item.get("generated_tags") or []
    if tags:
        lines.append(f"- Tags: {', '.join(str(tag) for tag in tags[:16])}")
    if item.get("snippet"):
        lines.extend(["", "Snippet:", "", str(item["snippet"])])
    lines.append("")
    return "\n".join(lines)


def generate_draft_outputs(
    profile: dict[str, Any] | None,
    task_names: list[str],
    request: str,
    evidence: list[dict[str, Any]],
    related: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        generate_task_draft(task_name, profile, request, evidence, related)
        for task_name in task_names
    ]


def generate_task_draft(
    task_name: str,
    profile: dict[str, Any] | None,
    request: str,
    evidence: list[dict[str, Any]],
    related: list[dict[str, Any]],
) -> dict[str, Any]:
    project_name = profile["name"] if profile else "対象プロジェクト"
    materials = evidence + related
    top_evidence = evidence[:6]
    evidence_lines = evidence_bullets(top_evidence)
    missing = missing_evidence(profile, evidence)

    if task_name == "planning":
        body = [
            f"目的: {project_name}の過去資料を根拠に、次の企画の方向性を定める。",
            "企画趣旨:",
            f"- {project_name}の既存実績・記録を活かし、文化的価値と実施可能性を両立する企画として整理する。",
            "対象者:",
            f"- {', '.join(profile.get('audiences') or ['関係者', '観客', '協働先']) if profile else '関係者、観客、協働先'}",
            "実施内容案:",
            "- 既存資料から確認できる強みを中心に、開催実績、記録写真、提案資料、報告資料を組み合わせて企画骨子を作る。",
            "根拠資料:",
            *evidence_lines,
        ]
    elif task_name == "marketing":
        body = [
            f"訴求軸: {project_name}の実績、文化的意義、記録性を前面に出す。",
            "ターゲット:",
            f"- {', '.join(profile.get('audiences') or ['観客', '文化関係者', '協働先']) if profile else '観客、文化関係者、協働先'}",
            "告知文案たたき台:",
            f"- {project_name}のこれまでの活動と記録をもとに、新しい参加・鑑賞・協働の入口をつくります。",
            "素材候補:",
            *media_bullets(materials),
            "根拠資料:",
            *evidence_lines,
        ]
    elif task_name == "sales":
        body = [
            "提案骨子:",
            f"- {project_name}は、文化的価値と実施実績をもとに、協働先へ具体的な提案ができる事業として提示する。",
            "相手にとってのメリット:",
            "- 文化的信用、地域・観客との接点、広報素材、実績活用の余地を整理して提示する。",
            "提案メール下書き:",
            f"- {project_name}の過去実績と関連資料をもとに、貴団体との協働可能性についてご相談したくご連絡しました。",
            "添付資料候補:",
            *evidence_lines,
        ]
    elif task_name == "grant":
        body = [
            "申請書骨子:",
            f"- 事業名: {project_name}",
            "- 目的: 過去実績と記録資料を根拠に、文化的・公共的意義を持つ事業として申請する。",
            "- 社会的意義: 地域文化、舞台芸術、国際性、教育性、観客開発のいずれかを資料根拠に沿って明確化する。",
            "- 実施内容: 既存資料から確認できる活動実績、提案書、報告書、写真記録を組み合わせて説明する。",
            "- 成果指標: 参加者数、連携先数、制作物、記録資料、広報到達、次年度展開を候補とする。",
            "必要添付資料候補:",
            *evidence_lines,
        ]
    elif task_name == "documents":
        body = [
            "資料構成案:",
            "1. 表紙: 事業名、目的、対象者",
            "2. 背景: なぜ今必要か",
            "3. 実績: 根拠資料に基づく過去活動",
            "4. 提案内容: 何を実施するか",
            "5. 体制・スケジュール",
            "6. 参考資料・証拠資料一覧",
            "引用・図版候補:",
            *evidence_lines,
        ]
    elif task_name == "presentation":
        body = [
            "スライド構成案:",
            "1. タイトル: 事業名と一言価値",
            "2. 背景: なぜこの事業が必要か",
            "3. 実績: 過去資料から見える強み",
            "4. 企画: 今回実施する内容",
            "5. 対象者・届け方",
            "6. 期待成果",
            "7. 協力・支援のお願い",
            "8. 参考資料",
            "図版候補:",
            *media_bullets(materials),
            "根拠資料:",
            *evidence_lines,
        ]
    else:
        body = [
            "意思決定メモ:",
            f"- 判断対象: {request or project_name}",
            "- 推奨: 根拠資料がある範囲で進め、不足資料がある部分は推測として扱う。",
            "判断材料:",
            *evidence_lines,
            "リスク:",
            "- 資料が不足している領域は、申請・営業・広報の確定文に使う前に追加確認が必要。",
        ]

    body.extend(["不足資料・追加検索候補:", *missing])
    return {
        "task": task_name,
        "label": TASKS[task_name]["label"],
        "body": body,
    }


def evidence_bullets(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- 現在のインデックスでは直接使える根拠資料が不足している。"]
    bullets = []
    for item in items:
        category = item.get("ai_category") or "unknown"
        media_type = item.get("media_type_candidate") or "unknown"
        bullets.append(f"- {item.get('file_name', '')} [{category}/{media_type}] `{item.get('full_path', '')}`")
    return bullets


def media_bullets(items: list[dict[str, Any]]) -> list[str]:
    media = [
        item for item in items
        if item.get("media_type_candidate") in {"image", "video", "presentation", "document", "spreadsheet"}
    ][:6]
    if not media:
        return ["- 画像・動画・提案資料などの素材候補は追加検索が必要。"]
    return evidence_bullets(media)


def missing_evidence(profile: dict[str, Any] | None, evidence: list[dict[str, Any]]) -> list[str]:
    needs = profile.get("evidence_needs") if profile else []
    if not needs:
        return ["- 目的別に不足資料を追加検索する。"]
    evidence_text = " ".join(
        [
            str(item.get("file_name", "")) + " " + " ".join(map(str, item.get("generated_tags") or []))
            for item in evidence
        ]
    )
    missing = [
        f"- {need}"
        for need in needs
        if str(need).casefold() not in evidence_text.casefold()
    ]
    return missing or ["- 現時点の主要な証拠資料は一通り候補化されている。"]


def render_draft(draft: dict[str, Any]) -> str:
    lines = [f"### {draft['label']}", ""]
    lines.extend(draft["body"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
