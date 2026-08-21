#!/usr/bin/env python3
"""Refresh KIO CEO Dashboard display JSON without creating a new source of truth.

The dashboard JSON is a disposable read model. Source adapters (currently
Asana) replace the sections they own; this script only recalculates display
metrics from the current read model.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "dashboard" / "data" / "dashboard.json"
SECTION_KEYS = ("decisions", "projects", "sales", "grants", "brand", "risks")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def refresh(source: dict, today: date | None = None) -> dict:
    today = today or datetime.now(ZoneInfo("Asia/Tokyo")).date()
    sections = source.get("sections", {})
    normalized = {key: list(sections.get(key, [])) for key in SECTION_KEYS}

    decisions = len(normalized["decisions"])
    risks = len(normalized["risks"])
    opportunities = sum(
        1
        for key in ("sales", "grants", "brand")
        for item in normalized[key]
        if item.get("priority") == "high" or item.get("label") in {"成長機会", "重点"}
    )

    # In v0.2 risk rows retain their project name in `status`, so count overdue
    # by label / due date rather than assuming status == '期限超過'.
    overdue_ids = set()
    for key in SECTION_KEYS:
        for item in normalized[key]:
            due = item.get("due")
            if item.get("label") == "期限超過" or (due and date.fromisoformat(due) < today and item.get("source") == "Asana"):
                overdue_ids.add(item.get("url") or f"{key}:{item.get('title')}:{due}")
    overdue = max(len(overdue_ids), int(source.get("summary", {}).get("overdue", 0)))

    source["generated_at"] = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    source["summary"] = {
        "decisions": decisions,
        "overdue": overdue,
        "opportunities": opportunities,
        "risks": risks,
    }
    source["sections"] = normalized
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    output = refresh(load_json(args.path))
    args.path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CEO Dashboard refreshed: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
