from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


DEFAULT_DB = "~/NakadachiArchiveAI/output/archive_index.sqlite"
RESULT_FIELDS = [
    "id",
    "file_name",
    "extension",
    "size_bytes",
    "modified_at",
    "full_path",
    "parent_folder",
    "source_root",
    "source_role",
    "duplicate_group",
    "project_candidates",
    "person_candidates",
    "organization_candidates",
    "event_candidates",
    "year_candidates",
    "media_type_candidate",
    "importance_candidate",
    "generated_tags",
    "ai_category",
    "ai_subcategory",
    "ai_confidence",
    "ai_reason",
    "text_excerpt",
    "ocr_text",
    "ocr_status",
    "duration_seconds",
    "width",
    "height",
    "codec",
]
JSON_FIELDS = {
    "project_candidates",
    "person_candidates",
    "organization_candidates",
    "event_candidates",
    "year_candidates",
    "generated_tags",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9_./+-]+|[\u3040-\u30ff\u3400-\u9fffー]{2,}")


def main() -> int:
    argv = normalize_argv(sys.argv[1:])
    parser = argparse.ArgumentParser(description="Search and retrieve Nakadachi Archive AI knowledge bases.")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help="Path to archive_index.sqlite. If missing, the newest archive_index*.sqlite in the folder is used.",
    )
    subparsers = parser.add_subparsers(dest="command")

    search_parser = subparsers.add_parser("search", help="Search file names, paths, tags, text, and OCR text.")
    add_db_arg(search_parser)
    add_search_args(search_parser)

    related_parser = subparsers.add_parser("related", help="Find related files from a query, path, or database id.")
    add_db_arg(related_parser)
    related_parser.add_argument("query", nargs="?", default="", help="Seed query text.")
    related_parser.add_argument("--path", help="Seed file path.")
    related_parser.add_argument("--id", type=int, help="Seed database row id.")
    related_parser.add_argument("--limit", type=int, default=20)
    related_parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")

    context_parser = subparsers.add_parser("context", help="Create an AI-ready context packet from search results.")
    add_db_arg(context_parser)
    add_search_args(context_parser)
    context_parser.add_argument("--title", default="Nakadachi Archive AI Context")
    context_parser.set_defaults(format="markdown")

    inspect_parser = subparsers.add_parser("inspect", help="Show database statistics and available categories.")
    add_db_arg(inspect_parser)
    inspect_parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")

    parser.add_argument("legacy_query", nargs="?", help="Backward compatible search query.")
    parser.add_argument("--limit", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command is None:
        if not args.legacy_query:
            parser.print_help()
            return 0
        args.command = "search"
        args.query = args.legacy_query
        args.mode = "all"
        args.limit = args.limit or 20
        args.format = "text"
        args.category = None
        args.media_type = None
        args.source_role = None
        args.extension = None
        args.ocr_only = False

    db_path = resolve_db_path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    with connect_readonly(db_path) as db:
        db.row_factory = sqlite3.Row
        if args.command == "search":
            results = search(db, args)
            render_results(results, args.format, title=f"Search: {args.query}")
        elif args.command == "context":
            results = search(db, args)
            render_context(results, args.title, args.query)
        elif args.command == "related":
            results = related(db, args)
            render_results(results, args.format, title="Related Materials")
        elif args.command == "inspect":
            stats = inspect_database(db, db_path)
            render_stats(stats, args.format)
    return 0


def add_search_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", help="Search query.")
    parser.add_argument("--mode", choices=["all", "name", "path", "text", "ocr", "tags"], default="all")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    parser.add_argument("--category")
    parser.add_argument("--media-type")
    parser.add_argument("--source-role")
    parser.add_argument("--extension")
    parser.add_argument("--ocr-only", action="store_true")


def add_db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help="Path to archive_index.sqlite. If missing, the newest archive_index*.sqlite in the folder is used.",
    )


def normalize_argv(argv: list[str]) -> list[str]:
    commands = {"search", "related", "context", "inspect"}
    if not argv:
        return argv
    first_positional_index = next((index for index, item in enumerate(argv) if not item.startswith("-")), None)
    if first_positional_index is None:
        return argv
    if argv[first_positional_index] in commands:
        return argv
    return [*argv[:first_positional_index], "search", *argv[first_positional_index:]]


def resolve_db_path(path_value: str) -> Path:
    db_path = Path(path_value).expanduser().resolve()
    if db_path.exists():
        return db_path
    output_dir = db_path.parent
    if output_dir.exists():
        matches = sorted(output_dir.glob("archive_index*.sqlite"), key=lambda path: path.stat().st_mtime)
        if matches:
            return matches[-1]
    return db_path


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def search(db: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    query = args.query.strip()
    limit = max(1, int(args.limit or 20))
    filters, filter_values = build_filters(args)
    results: dict[int, dict[str, Any]] = {}

    fts_query = build_fts_query(query)
    if fts_query and args.mode in {"all", "name", "text", "ocr", "tags"}:
        fts_columns = {
            "name": "file_name",
            "text": "text_excerpt",
            "ocr": "ocr_text",
            "tags": "generated_tags",
        }
        column_filter = f"{fts_columns[args.mode]}:" if args.mode in fts_columns else ""
        sql = f"""
            SELECT f.*, bm25(files_fts) AS rank_score
            FROM files_fts
            JOIN files f ON files_fts.rowid = f.id
            WHERE files_fts MATCH ?
            {filters}
            ORDER BY rank_score
            LIMIT ?
        """
        try:
            for row in db.execute(sql, [column_filter + fts_query, *filter_values, limit]):
                result = row_to_result(row, query=query, method="fts", score=float(row["rank_score"] or 0))
                results[result["id"]] = result
        except sqlite3.OperationalError:
            pass

    like_rows = like_search(db, query, args.mode, filters, filter_values, limit * 3)
    for row in like_rows:
        result = row_to_result(row, query=query, method="like", score=like_score(row, query, args.mode))
        existing = results.get(result["id"])
        if existing is None or result["score"] > existing["score"]:
            results[result["id"]] = result

    ordered = sorted(results.values(), key=lambda item: (-item["score"], item["file_name"], item["full_path"]))
    return ordered[:limit]


def like_search(
    db: sqlite3.Connection,
    query: str,
    mode: str,
    filters: str,
    filter_values: list[Any],
    limit: int,
) -> list[sqlite3.Row]:
    columns = {
        "all": ["file_name", "full_path", "parent_folder", "generated_tags", "ai_category", "text_excerpt", "ocr_text"],
        "name": ["file_name"],
        "path": ["full_path", "parent_folder"],
        "text": ["text_excerpt"],
        "ocr": ["ocr_text"],
        "tags": ["generated_tags", "project_candidates", "person_candidates", "organization_candidates", "event_candidates"],
    }[mode]
    tokens = tokenize(query) or [query]
    where_parts = []
    values: list[Any] = []
    for token in tokens:
        token_parts = []
        for column in columns:
            token_parts.append(f"f.{column} LIKE ?")
            values.append(f"%{token}%")
        where_parts.append("(" + " OR ".join(token_parts) + ")")

    sql = f"""
        SELECT *
        FROM files f
        WHERE {' AND '.join(where_parts)}
        {filters}
        ORDER BY modified_at DESC, file_name
        LIMIT ?
    """
    return db.execute(sql, [*values, *filter_values, limit]).fetchall()


def related(db: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    seed = get_seed_record(db, args)
    if not seed:
        if args.query:
            search_args = argparse.Namespace(
                query=args.query,
                mode="all",
                limit=1,
                format="text",
                category=None,
                media_type=None,
                source_role=None,
                extension=None,
                ocr_only=False,
            )
            seeds = search(db, search_args)
            if seeds:
                seed = seeds[0]
    if not seed:
        return []

    terms = related_terms(seed)
    if not terms:
        return []

    scores: dict[int, dict[str, Any]] = {}
    for term in terms[:20]:
        search_args = argparse.Namespace(
            query=term,
            mode="all",
            limit=max(10, args.limit * 2),
            format="text",
            category=None,
            media_type=None,
            source_role=None,
            extension=None,
            ocr_only=False,
        )
        for result in search(db, search_args):
            if result["full_path"] == seed.get("full_path"):
                continue
            entry = scores.setdefault(result["id"], result)
            entry.setdefault("related_reasons", [])
            if term not in entry["related_reasons"]:
                entry["related_reasons"].append(term)
            entry["score"] = float(entry.get("score") or 0) + 1

    ordered = sorted(scores.values(), key=lambda item: (-float(item.get("score") or 0), item["file_name"]))
    return ordered[: max(1, args.limit)]


def get_seed_record(db: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any] | None:
    row = None
    if args.id is not None:
        row = db.execute("SELECT * FROM files WHERE id = ?", (args.id,)).fetchone()
    elif args.path:
        row = db.execute("SELECT * FROM files WHERE full_path = ?", (args.path,)).fetchone()
    if row:
        return row_to_result(row, method="seed", query="", score=1)
    return None


def related_terms(seed: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for field in [
        "project_candidates",
        "person_candidates",
        "organization_candidates",
        "event_candidates",
        "year_candidates",
        "generated_tags",
    ]:
        for value in seed.get(field) or []:
            if value and value not in terms:
                terms.append(str(value))
    for field in ["ai_category", "media_type_candidate", "parent_folder"]:
        value = seed.get(field)
        if value and value not in terms:
            terms.append(str(value))
    return [term for term in terms if len(term) >= 2]


def build_filters(args: argparse.Namespace) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    values: list[Any] = []
    for attr, column in [
        ("category", "ai_category"),
        ("media_type", "media_type_candidate"),
        ("source_role", "source_role"),
    ]:
        value = getattr(args, attr, None)
        if value:
            clauses.append(f"AND f.{column} = ?" if column.startswith("ai_") or column in {"media_type_candidate", "source_role"} else f"AND {column} = ?")
            values.append(value)
    extension = getattr(args, "extension", None)
    if extension:
        clauses.append("AND f.extension = ?")
        values.append(extension if extension.startswith(".") else f".{extension}")
    if getattr(args, "ocr_only", False):
        clauses.append("AND f.ocr_text != ''")
    return "\n".join(clauses), values


def build_fts_query(query: str) -> str:
    tokens = tokenize(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token.replace(chr(34), chr(34) + chr(34))}"' for token in tokens[:12])


def tokenize(text: str) -> list[str]:
    tokens = [token.strip() for token in TOKEN_RE.findall(text) if token.strip()]
    if not tokens and text.strip():
        tokens = [text.strip()]
    return list(dict.fromkeys(tokens))


def row_to_result(row: sqlite3.Row, query: str = "", method: str = "", score: float = 0) -> dict[str, Any]:
    result = {field: restore_field(field, row[field]) for field in RESULT_FIELDS if field in row.keys()}
    result["score"] = round(score, 4)
    result["match_method"] = method
    result["snippet"] = make_snippet(result, query)
    return result


def restore_field(field: str, value: Any) -> Any:
    if field in JSON_FIELDS:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return [str(value)]
        return parsed if isinstance(parsed, list) else [str(parsed)]
    return value if value is not None else ""


def like_score(row: sqlite3.Row, query: str, mode: str) -> float:
    tokens = tokenize(query) or [query]
    weighted_columns = [
        ("file_name", 6),
        ("generated_tags", 5),
        ("ai_category", 4),
        ("parent_folder", 3),
        ("full_path", 2),
        ("text_excerpt", 1.5),
        ("ocr_text", 1.5),
    ]
    if mode == "ocr":
        weighted_columns = [("ocr_text", 8), ("file_name", 2)]
    elif mode == "text":
        weighted_columns = [("text_excerpt", 8), ("file_name", 2)]
    elif mode == "name":
        weighted_columns = [("file_name", 8), ("parent_folder", 2)]
    elif mode == "tags":
        weighted_columns = [("generated_tags", 8), ("ai_category", 4)]

    score = 0.0
    for token in tokens:
        folded = token.casefold()
        for column, weight in weighted_columns:
            if folded in str(row[column] if column in row.keys() else "").casefold():
                score += weight
    return score


def make_snippet(result: dict[str, Any], query: str, width: int = 260) -> str:
    for field in ["text_excerpt", "ocr_text", "full_path"]:
        text = str(result.get(field) or "")
        if not text:
            continue
        token = next((item for item in tokenize(query) if item.casefold() in text.casefold()), "")
        if token:
            index = text.casefold().find(token.casefold())
            start = max(0, index - width // 3)
            return compact_text(text[start : start + width])
        if field != "full_path":
            return compact_text(text[:width])
    return ""


def compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def render_results(results: list[dict[str, Any]], output_format: str, title: str) -> None:
    if output_format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if output_format == "markdown":
        print(f"# {title}\n")
        for index, result in enumerate(results, start=1):
            print(markdown_result(index, result))
        print(f"\nResults: {len(results)}")
        return

    for result in results:
        print(f"{result['file_name']} | {result['ai_category']} | {result['media_type_candidate']} | score={result['score']}")
        print(result["full_path"])
        if result.get("snippet"):
            print(result["snippet"])
        if result.get("related_reasons"):
            print("Related by: " + ", ".join(result["related_reasons"][:8]))
        print()
    print(f"Results: {len(results)}")


def render_context(results: list[dict[str, Any]], title: str, query: str) -> None:
    print(f"# {title}")
    print()
    print(f"Query: {query}")
    print(f"Results: {len(results)}")
    print()
    for index, result in enumerate(results, start=1):
        print(markdown_result(index, result, include_context=True))


def markdown_result(index: int, result: dict[str, Any], include_context: bool = False) -> str:
    lines = [
        f"## {index}. {result['file_name']}",
        "",
        f"- Path: `{result['full_path']}`",
        f"- Category: `{result.get('ai_category', '')}`",
        f"- Media type: `{result.get('media_type_candidate', '')}`",
        f"- Modified: `{result.get('modified_at', '')}`",
        f"- Score: `{result.get('score', '')}`",
    ]
    tags = result.get("generated_tags") or []
    if tags:
        lines.append(f"- Tags: {', '.join(str(tag) for tag in tags[:20])}")
    if result.get("duplicate_group"):
        lines.append(f"- Duplicate group: `{result['duplicate_group']}`")
    if result.get("related_reasons"):
        lines.append(f"- Related by: {', '.join(result['related_reasons'][:10])}")
    snippet = result.get("snippet")
    if snippet:
        lines.extend(["", "Snippet:", "", snippet])
    if include_context:
        text = compact_text((result.get("text_excerpt") or result.get("ocr_text") or "")[:1200])
        if text and text != snippet:
            lines.extend(["", "Context Text:", "", text])
    lines.append("")
    return "\n".join(lines)


def inspect_database(db: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {"database": str(db_path)}
    stats["file_count"] = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    stats["duplicate_groups"] = db.execute("SELECT COUNT(*) FROM duplicate_groups").fetchone()[0]
    stats["ocr_text_count"] = db.execute("SELECT COUNT(*) FROM files WHERE ocr_text != ''").fetchone()[0]
    stats["text_excerpt_count"] = db.execute("SELECT COUNT(*) FROM files WHERE text_excerpt != ''").fetchone()[0]
    try:
        stats["ai_search_documents_count"] = db.execute("SELECT COUNT(*) FROM ai_search_documents").fetchone()[0]
    except sqlite3.OperationalError:
        stats["ai_search_documents_count"] = 0
    stats["categories"] = table_counts(db, "ai_category")
    stats["media_types"] = table_counts(db, "media_type_candidate")
    stats["ocr_status"] = table_counts(db, "ocr_status")
    return stats


def table_counts(db: sqlite3.Connection, column: str) -> list[dict[str, Any]]:
    rows = db.execute(
        f"""
        SELECT {column} AS label, COUNT(*) AS count
        FROM files
        GROUP BY {column}
        ORDER BY count DESC, label
        """
    ).fetchall()
    return [{"label": row["label"], "count": row["count"]} for row in rows]


def render_stats(stats: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    if output_format == "markdown":
        print("# Nakadachi Archive AI Database\n")
        print(f"- Database: `{stats['database']}`")
        print(f"- Files: {stats['file_count']}")
        print(f"- Duplicate groups: {stats['duplicate_groups']}")
        print(f"- AI search view rows: {stats['ai_search_documents_count']}")
        print(f"- Text excerpts: {stats['text_excerpt_count']}")
        print(f"- OCR text rows: {stats['ocr_text_count']}")
        for title, key in [("Categories", "categories"), ("Media Types", "media_types"), ("OCR Status", "ocr_status")]:
            print(f"\n## {title}\n")
            for item in stats[key]:
                print(f"- {item['label']}: {item['count']}")
        return
    print(f"Database: {stats['database']}")
    print(f"Files: {stats['file_count']}")
    print(f"Duplicate groups: {stats['duplicate_groups']}")
    print(f"AI search view rows: {stats['ai_search_documents_count']}")
    print(f"Text excerpts: {stats['text_excerpt_count']}")
    print(f"OCR text rows: {stats['ocr_text_count']}")


if __name__ == "__main__":
    raise SystemExit(main())
