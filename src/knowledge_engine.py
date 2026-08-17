from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import assistant_ai
from private_storage import ensure_private_directory, harden_private_tree, secure_file, set_private_umask, write_private_text
import search_archive


DEFAULT_OUTPUT_DIR = "~/NakadachiArchiveAI/knowledge_engine"


def main() -> int:
    set_private_umask()
    parser = argparse.ArgumentParser(description="Build the Nakadachi Archive AI Knowledge Engine.")
    parser.add_argument("--db", default=search_archive.DEFAULT_DB)
    parser.add_argument("--profiles", default=assistant_ai.DEFAULT_PROFILES)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build AI-ready knowledge maps and manifest.")
    add_common_args(build_parser)
    build_parser.add_argument("--limit", type=int, default=24)
    build_parser.add_argument("--task", choices=["all", *assistant_ai.TASKS.keys()], default="all")

    status_parser = subparsers.add_parser("status", help="Show current knowledge-base status.")
    add_common_args(status_parser)
    status_parser.add_argument("--format", choices=["text", "json", "markdown"], default="markdown")

    args = parser.parse_args()
    db_path = search_archive.resolve_db_path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    profiles = assistant_ai.load_profiles(Path(args.profiles))
    with search_archive.connect_readonly(db_path) as db:
        db.row_factory = sqlite3.Row
        if args.command == "status":
            stats = build_status(db, db_path, profiles)
            render_status(stats, args.format)
            return 0

        output_dir = Path(args.output_dir).expanduser()
        result = build_knowledge_engine(
            db=db,
            db_path=db_path,
            profiles=profiles,
            output_dir=output_dir,
            task=args.task,
            limit=args.limit,
        )
        print(f"Knowledge Engine built: {result['run_dir']}")
        print(f"Manifest: {result['manifest_path']}")
        print(f"SQLite: {result['sqlite_path']}")
    return 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=argparse.SUPPRESS)
    parser.add_argument("--profiles", default=argparse.SUPPRESS)
    parser.add_argument("--output-dir", default=argparse.SUPPRESS)


def build_knowledge_engine(
    db: sqlite3.Connection,
    db_path: Path,
    profiles: list[dict[str, Any]],
    output_dir: Path,
    task: str,
    limit: int,
) -> dict[str, str]:
    created_at = datetime.now().astimezone()
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"run_{timestamp}"
    maps_dir = run_dir / "project_maps"
    briefs_dir = run_dir / "task_briefs"
    ensure_private_directory(output_dir, harden_existing=True)
    ensure_private_directory(run_dir)
    ensure_private_directory(maps_dir)
    ensure_private_directory(briefs_dir)

    stats = build_status(db, db_path, profiles)
    project_maps = [
        build_project_map(db, db_path, profile, task=task, limit=limit)
        for profile in profiles
    ]
    graph = build_graph(project_maps)
    manifest = build_manifest(created_at, db_path, stats, project_maps, graph)

    write_json(run_dir / "knowledge_manifest.json", manifest)
    write_json(run_dir / "knowledge_graph.json", graph)
    write_text(run_dir / "AI_KNOWLEDGE_ENGINE.md", engine_markdown(manifest, project_maps))

    for project_map in project_maps:
        project_id = project_map["profile"]["id"]
        write_json(maps_dir / f"{project_id}.json", project_map)
        write_text(maps_dir / f"{project_id}.md", project_map_markdown(project_map))
        for brief in project_map["task_briefs"]:
            write_json(briefs_dir / f"{project_id}_{brief['task']}.json", brief)
            write_text(briefs_dir / f"{project_id}_{brief['task']}.md", task_brief_markdown(project_map, brief))

    sqlite_path = run_dir / "knowledge_engine.sqlite"
    write_engine_sqlite(sqlite_path, manifest, project_maps, graph)
    secure_file(sqlite_path)
    harden_private_tree(run_dir)

    return {
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "knowledge_manifest.json"),
        "sqlite_path": str(sqlite_path),
    }


def build_status(db: sqlite3.Connection, db_path: Path, profiles: list[dict[str, Any]]) -> dict[str, Any]:
    stats = search_archive.inspect_database(db, db_path)
    stats["profile_count"] = len(profiles)
    stats["profiles"] = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "aliases": profile.get("aliases") or [],
            "business_goals": profile.get("business_goals") or [],
        }
        for profile in profiles
    ]
    return stats


def build_project_map(
    db: sqlite3.Connection,
    db_path: Path,
    profile: dict[str, Any],
    task: str,
    limit: int,
) -> dict[str, Any]:
    pack = assistant_ai.build_brief(db, profile, task, "", limit, db_path)
    evidence = pack["evidence"]
    related = pack["related_materials"]
    materials = dedupe_materials([*evidence, *related])
    coverage = evidence_coverage(profile, materials)
    task_briefs = build_task_briefs(profile, pack, materials, coverage)

    return {
        "profile": profile,
        "created_at": pack["created_at"],
        "database": pack["database"],
        "retrieval_queries": assistant_ai.profile_queries(profile),
        "summary": {
            "evidence_count": len(evidence),
            "related_count": len(related),
            "material_count": len(materials),
            "categories": counter_for(materials, "ai_category"),
            "media_types": counter_for(materials, "media_type_candidate"),
            "source_roles": counter_for(materials, "source_role"),
            "years": counter_list_for(materials, "year_candidates"),
            "top_tags": counter_list_for(materials, "generated_tags", limit=25),
        },
        "knowledge_state": knowledge_state(profile, materials, coverage),
        "evidence_coverage": coverage,
        "priority_materials": [material_summary(item) for item in materials[: min(20, len(materials))]],
        "task_briefs": task_briefs,
        "assistant_packet": pack,
    }


def build_task_briefs(
    profile: dict[str, Any],
    pack: dict[str, Any],
    materials: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gaps = [item["need"] for item in coverage if item["status"] != "covered"]
    briefs = []
    for draft in pack.get("draft_outputs") or []:
        task = draft["task"]
        materials_for_task = rank_materials_for_task(materials, task)
        briefs.append(
            {
                "project_id": profile["id"],
                "project_name": profile["name"],
                "task": task,
                "label": draft["label"],
                "purpose": task_purpose(task, profile),
                "usable_evidence": [material_summary(item) for item in materials_for_task[:10]],
                "draft": draft["body"],
                "knowledge_gaps": gaps,
                "ai_operating_rule": "根拠資料のパスを明示し、推測と事実を分け、元資料は変更しない。",
            }
        )
    return briefs


def evidence_coverage(profile: dict[str, Any], materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage = []
    for need in profile.get("evidence_needs") or []:
        matches = [
            item for item in materials
            if evidence_need_matches(str(need), item)
        ]
        if matches:
            status = "covered"
        else:
            status = "needs_more_evidence"
        coverage.append(
            {
                "need": need,
                "status": status,
                "matched_count": len(matches),
                "examples": [material_summary(item) for item in matches[:5]],
                "next_search": f"{profile['name']} {need}",
            }
        )
    return coverage


def evidence_need_matches(need: str, material: dict[str, Any]) -> bool:
    if term_in_material(need, material):
        return True

    category = str(material.get("ai_category") or "")
    media_type = str(material.get("media_type_candidate") or "")
    need_rules = {
        "実績一覧": {"terms": ["実績", "master", "マスター", "一覧"], "categories": {"grant_report", "archive_index", "spreadsheet"}},
        "写真・記録": {"terms": ["写真", "記録", "photo", "documentation"], "categories": {"photo_documentation", "video_documentation"}},
        "事業報告": {"terms": ["報告", "report", "実績"], "categories": {"grant_report"}},
        "予算・精算": {"terms": ["予算", "精算", "budget", "請求", "見積"], "categories": {"contract_finance"}},
        "企画書": {"terms": ["企画", "提案", "proposal", "plan"], "categories": {"production", "document", "grant_report"}},
        "過去企画": {"terms": ["企画", "過去", "project", "plan"], "categories": {"production", "document", "archive_index"}},
        "写真・映像": {"terms": ["写真", "映像", "動画", "photo", "video"], "categories": {"photo_documentation", "video_documentation"}},
        "広報物": {"terms": ["広報", "チラシ", "フライヤー", "press", "poster"], "categories": {"design_publicity"}},
        "関係者情報": {"terms": ["関係者", "出演", "参加者", "artist", "member"], "categories": {"meeting_notes", "document"}},
        "成果記録": {"terms": ["成果", "記録", "報告", "result"], "categories": {"grant_report", "photo_documentation", "video_documentation"}},
        "開催実績": {"terms": ["開催", "実績", "報告"], "categories": {"grant_report", "archive_index"}},
        "参加者情報": {"terms": ["参加者", "artist", "出演", "応募"], "categories": {"document", "meeting_notes"}},
        "広報資料": {"terms": ["広報", "press", "poster", "flyer", "告知"], "categories": {"design_publicity", "document"}},
        "予算資料": {"terms": ["予算", "budget", "見積", "精算"], "categories": {"contract_finance", "spreadsheet"}},
        "報告書": {"terms": ["報告", "report"], "categories": {"grant_report"}},
        "記録写真": {"terms": ["写真", "記録", "photo"], "categories": {"photo_documentation"}},
        "関係者資料": {"terms": ["関係者", "artist", "partner", "出演"], "categories": {"document", "meeting_notes"}},
        "提案書": {"terms": ["提案", "proposal", "提出"], "categories": {"document", "grant_report"}},
        "提出資料": {"terms": ["提出", "副本", "提案"], "categories": {"document", "grant_report"}},
        "地域資源": {"terms": ["地域", "資源", "観光", "文化"], "categories": {"document", "production"}},
        "実施体制": {"terms": ["体制", "運営", "組織", "担当"], "categories": {"document", "meeting_notes"}},
        "過去受賞記録": {"terms": ["受賞", "記録", "過去"], "categories": {"archive_index", "grant_report"}},
        "推薦資料": {"terms": ["推薦", "候補", "nomination"], "categories": {"document"}},
        "会議資料": {"terms": ["会議", "議事", "打合", "meeting"], "categories": {"meeting_notes", "document"}},
        "公演資料": {"terms": ["公演", "舞台", "performance", "program"], "categories": {"production", "document"}},
        "教育資料": {"terms": ["教育", "学校", "ワークショップ", "教材"], "categories": {"production", "document"}},
        "助成金資料": {"terms": ["助成", "申請", "grant"], "categories": {"grant_report", "document"}},
        "指定管理実績": {"terms": ["指定管理", "阿倍野", "実績"], "categories": {"grant_report", "archive_index", "document"}},
        "運営資料": {"terms": ["運営", "管理", "施設", "指定管理"], "categories": {"document", "meeting_notes"}},
        "地域連携資料": {"terms": ["地域", "連携", "協力", "区民"], "categories": {"document", "production"}},
    }
    rule = need_rules.get(need)
    if not rule:
        return False
    if category in rule["categories"]:
        return True
    if media_type in rule["categories"]:
        return True
    return any(term_in_material(term, material) for term in rule["terms"])


def knowledge_state(
    profile: dict[str, Any],
    materials: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    covered = sum(1 for item in coverage if item["status"] == "covered")
    total = len(coverage) or 1
    readiness = round(covered / total, 2)
    high_value = [
        item for item in materials
        if item.get("importance_candidate") in {"high", "medium"}
        or item.get("ai_category") in {"grant_report", "production", "design_publicity", "photo_documentation"}
    ]
    return {
        "readiness": readiness,
        "readiness_label": readiness_label(readiness),
        "what_ai_can_do_now": [
            f"{goal}の根拠資料検索と初稿作成"
            for goal in profile.get("business_goals", [])
        ],
        "high_value_material_count": len(high_value),
        "knowledge_gaps": [item["need"] for item in coverage if item["status"] != "covered"],
        "recommended_next_searches": [item["next_search"] for item in coverage if item["status"] != "covered"],
    }


def build_graph(project_maps: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()

    for project_map in project_maps:
        profile = project_map["profile"]
        project_node = f"project:{profile['id']}"
        add_node(nodes, seen_nodes, project_node, "project", profile["name"])
        for material in project_map["priority_materials"]:
            material_node = f"file:{material['id'] or material['path']}"
            add_node(nodes, seen_nodes, material_node, "material", material["file_name"], material)
            edges.append(
                {
                    "source": project_node,
                    "target": material_node,
                    "type": "uses_as_evidence",
                    "score": material.get("score", 0),
                }
            )
            for tag in material.get("tags", [])[:8]:
                tag_node = f"tag:{tag}"
                add_node(nodes, seen_nodes, tag_node, "tag", str(tag))
                edges.append({"source": material_node, "target": tag_node, "type": "has_tag"})
    return {"nodes": nodes, "edges": edges}


def build_manifest(
    created_at: datetime,
    db_path: Path,
    stats: dict[str, Any],
    project_maps: list[dict[str, Any]],
    graph: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": "Nakadachi Archive AI Knowledge Engine",
        "created_at": created_at.isoformat(timespec="seconds"),
        "database": str(db_path),
        "safety_policy": [
            "Source files are read-only inputs.",
            "Never move, delete, rename, or edit source archive files.",
            "Generated knowledge outputs may be rebuilt from the SQLite index.",
            "Evidence paths must be preserved in AI outputs.",
        ],
        "update_command": "./scripts/run_full_update.sh",
        "search_commands": [
            "./scripts/run_assistant.sh ask \"KIOの助成金申請骨子を作る\"",
            "./scripts/run_assistant.sh brief osaka_fringe --task all",
            "python3 -B src/search_archive.py context \"大阪文化万博 助成金\" --limit 8",
        ],
        "stats": stats,
        "projects": [
            {
                "id": project_map["profile"]["id"],
                "name": project_map["profile"]["name"],
                "readiness": project_map["knowledge_state"]["readiness"],
                "readiness_label": project_map["knowledge_state"]["readiness_label"],
                "material_count": project_map["summary"]["material_count"],
                "knowledge_gaps": project_map["knowledge_state"]["knowledge_gaps"],
            }
            for project_map in project_maps
        ],
        "graph": {
            "node_count": len(graph["nodes"]),
            "edge_count": len(graph["edges"]),
        },
    }


def write_engine_sqlite(
    sqlite_path: Path,
    manifest: dict[str, Any],
    project_maps: list[dict[str, Any]],
    graph: dict[str, Any],
) -> None:
    db = sqlite3.connect(sqlite_path)
    db.executescript(
        """
        CREATE TABLE manifest (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );
        CREATE TABLE project_maps (
            project_id TEXT PRIMARY KEY,
            project_name TEXT NOT NULL,
            readiness REAL,
            material_count INTEGER,
            map_json TEXT NOT NULL
        );
        CREATE TABLE evidence_links (
            project_id TEXT,
            file_id TEXT,
            file_name TEXT,
            full_path TEXT,
            ai_category TEXT,
            media_type TEXT,
            score REAL,
            tags_json TEXT
        );
        CREATE TABLE task_briefs (
            project_id TEXT,
            task TEXT,
            label TEXT,
            brief_json TEXT,
            PRIMARY KEY(project_id, task)
        );
        CREATE TABLE graph (
            kind TEXT,
            item_json TEXT NOT NULL
        );
        """
    )
    db.execute("INSERT INTO manifest(key, value_json) VALUES (?, ?)", ("manifest", dumps(manifest)))
    for project_map in project_maps:
        profile = project_map["profile"]
        db.execute(
            """
            INSERT INTO project_maps(project_id, project_name, readiness, material_count, map_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                profile["id"],
                profile["name"],
                project_map["knowledge_state"]["readiness"],
                project_map["summary"]["material_count"],
                dumps(project_map),
            ),
        )
        for material in project_map["priority_materials"]:
            db.execute(
                """
                INSERT INTO evidence_links(
                    project_id, file_id, file_name, full_path, ai_category, media_type, score, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["id"],
                    str(material["id"]),
                    material["file_name"],
                    material["path"],
                    material["category"],
                    material["media_type"],
                    material["score"],
                    dumps(material.get("tags") or []),
                ),
            )
        for brief in project_map["task_briefs"]:
            db.execute(
                """
                INSERT INTO task_briefs(project_id, task, label, brief_json)
                VALUES (?, ?, ?, ?)
                """,
                (profile["id"], brief["task"], brief["label"], dumps(brief)),
            )
    for node in graph["nodes"]:
        db.execute("INSERT INTO graph(kind, item_json) VALUES (?, ?)", ("node", dumps(node)))
    for edge in graph["edges"]:
        db.execute("INSERT INTO graph(kind, item_json) VALUES (?, ?)", ("edge", dumps(edge)))
    db.commit()
    db.close()


def rank_materials_for_task(materials: list[dict[str, Any]], task: str) -> list[dict[str, Any]]:
    task_categories = {
        "planning": {"production", "document", "grant_report", "archive_index"},
        "marketing": {"design_publicity", "photo_documentation", "video_documentation", "document"},
        "sales": {"grant_report", "photo_documentation", "document", "production"},
        "grant": {"grant_report", "contract_finance", "photo_documentation", "document", "spreadsheet"},
        "documents": {"document", "presentation", "spreadsheet", "photo_documentation"},
        "presentation": {"presentation", "photo_documentation", "video_documentation", "document"},
        "decision": {"grant_report", "contract_finance", "meeting_notes", "document", "archive_index"},
    }
    preferred = task_categories.get(task, set())
    return sorted(
        materials,
        key=lambda item: (
            item.get("ai_category") not in preferred,
            -float(item.get("score") or 0),
            item.get("file_name", ""),
        ),
    )


def term_in_material(term: str, material: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(material.get("file_name", "")),
            str(material.get("full_path", "")),
            str(material.get("parent_folder", "")),
            str(material.get("ai_category", "")),
            str(material.get("media_type_candidate", "")),
            " ".join(map(str, material.get("generated_tags") or [])),
            str(material.get("snippet", "")),
        ]
    ).casefold()
    tokens = search_archive.tokenize(term) or [term]
    return any(token.casefold() in haystack for token in tokens)


def material_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "file_name": item.get("file_name", ""),
        "path": item.get("full_path", ""),
        "category": item.get("ai_category", ""),
        "media_type": item.get("media_type_candidate", ""),
        "modified_at": item.get("modified_at", ""),
        "source_role": item.get("source_role", ""),
        "score": item.get("score", 0),
        "tags": (item.get("generated_tags") or [])[:16],
        "snippet": item.get("snippet", ""),
    }


def dedupe_materials(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped = []
    for item in items:
        key = item.get("full_path") or str(item.get("id"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def counter_for(items: list[dict[str, Any]], field: str, limit: int = 20) -> dict[str, int]:
    counter = Counter(str(item.get(field) or "unknown") for item in items)
    return dict(counter.most_common(limit))


def counter_list_for(items: list[dict[str, Any]], field: str, limit: int = 20) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        counter.update(str(value) for value in item.get(field) or [])
    return dict(counter.most_common(limit))


def readiness_label(readiness: float) -> str:
    if readiness >= 0.8:
        return "operational"
    if readiness >= 0.5:
        return "usable_with_gaps"
    if readiness > 0:
        return "early_stage"
    return "needs_indexed_evidence"


def task_purpose(task: str, profile: dict[str, Any]) -> str:
    labels = {
        "planning": "過去資料から企画骨子を作る",
        "marketing": "写真・広報・記録資料から訴求軸を作る",
        "sales": "実績資料を根拠に提案先へ説明する",
        "grant": "助成金申請の骨子と添付資料候補を作る",
        "documents": "引用根拠つきの資料構成を作る",
        "presentation": "営業・説明用のスライド構成を作る",
        "decision": "根拠、リスク、不足資料を分けて判断材料を作る",
    }
    return f"{profile['name']}について、{labels.get(task, '資料を活用する')}。"


def add_node(
    nodes: list[dict[str, Any]],
    seen: set[str],
    node_id: str,
    node_type: str,
    label: str,
    extra: dict[str, Any] | None = None,
) -> None:
    if node_id in seen:
        return
    seen.add(node_id)
    node = {"id": node_id, "type": node_type, "label": label}
    if extra:
        node.update(extra)
    nodes.append(node)


def render_status(stats: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(dumps(stats))
        return
    if output_format == "markdown":
        print("# Nakadachi Archive AI Knowledge Engine Status\n")
        print(f"- Database: `{stats['database']}`")
        print(f"- Files: {stats['file_count']}")
        print(f"- Text excerpts: {stats['text_excerpt_count']}")
        print(f"- OCR text rows: {stats['ocr_text_count']}")
        print(f"- Project profiles: {stats['profile_count']}")
        print("\n## Projects\n")
        for profile in stats["profiles"]:
            print(f"- {profile['id']}: {profile['name']}")
        return
    print(f"Database: {stats['database']}")
    print(f"Files: {stats['file_count']}")
    print(f"Project profiles: {stats['profile_count']}")


def engine_markdown(manifest: dict[str, Any], project_maps: list[dict[str, Any]]) -> str:
    lines = [
        "# Nakadachi Archive AI Knowledge Engine",
        "",
        f"- Created: {manifest['created_at']}",
        f"- Database: `{manifest['database']}`",
        f"- Graph nodes: {manifest['graph']['node_count']}",
        f"- Graph edges: {manifest['graph']['edge_count']}",
        "",
        "## Operating Rules",
        "",
    ]
    lines.extend(f"- {item}" for item in manifest["safety_policy"])
    lines.extend(["", "## Projects", ""])
    for project_map in project_maps:
        profile = project_map["profile"]
        state = project_map["knowledge_state"]
        lines.append(f"### {profile['name']}")
        lines.append("")
        lines.append(f"- Readiness: {state['readiness_label']} ({state['readiness']})")
        lines.append(f"- Materials: {project_map['summary']['material_count']}")
        if state["knowledge_gaps"]:
            lines.append(f"- Knowledge gaps: {', '.join(state['knowledge_gaps'])}")
        else:
            lines.append("- Knowledge gaps: none detected in configured evidence needs")
        lines.append("")
    lines.extend(
        [
            "## AI Entry Points",
            "",
            f"- Full refresh: `{manifest['update_command']}`",
            "- Use `knowledge_manifest.json` first, then open the relevant project map and task brief.",
            "- Use evidence paths exactly as written when creating proposals, applications, and decks.",
            "",
        ]
    )
    return "\n".join(lines)


def project_map_markdown(project_map: dict[str, Any]) -> str:
    profile = project_map["profile"]
    state = project_map["knowledge_state"]
    lines = [
        f"# {profile['name']} Knowledge Map",
        "",
        f"- Created: {project_map['created_at']}",
        f"- Readiness: {state['readiness_label']} ({state['readiness']})",
        f"- Materials: {project_map['summary']['material_count']}",
        "",
        "## What AI Can Do Now",
        "",
    ]
    lines.extend(f"- {item}" for item in state["what_ai_can_do_now"])
    lines.extend(["", "## Evidence Coverage", ""])
    for item in project_map["evidence_coverage"]:
        lines.append(f"- {item['need']}: {item['status']} ({item['matched_count']})")
    lines.extend(["", "## Priority Materials", ""])
    for index, material in enumerate(project_map["priority_materials"], start=1):
        lines.append(f"{index}. `{material['path']}`")
        lines.append(f"   - {material['category']} / {material['media_type']}")
    lines.extend(["", "## Task Briefs", ""])
    for brief in project_map["task_briefs"]:
        lines.append(f"- {brief['label']}: `task_briefs/{profile['id']}_{brief['task']}.md`")
    lines.append("")
    return "\n".join(lines)


def task_brief_markdown(project_map: dict[str, Any], brief: dict[str, Any]) -> str:
    lines = [
        f"# {brief['project_name']} - {brief['label']}",
        "",
        f"- Purpose: {brief['purpose']}",
        f"- Rule: {brief['ai_operating_rule']}",
        "",
        "## Usable Evidence",
        "",
    ]
    for material in brief["usable_evidence"]:
        lines.append(f"- {material['file_name']}: `{material['path']}`")
    lines.extend(["", "## Draft", ""])
    lines.extend(str(item) for item in brief["draft"])
    lines.extend(["", "## Knowledge Gaps", ""])
    if brief["knowledge_gaps"]:
        lines.extend(f"- {item}" for item in brief["knowledge_gaps"])
    else:
        lines.append("- None detected in configured evidence needs.")
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, value: Any) -> None:
    write_private_text(path, dumps(value))


def write_text(path: Path, value: str) -> None:
    write_private_text(path, value)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
