#!/usr/bin/env python3
"""Build KIO CEO Dashboard display JSON without creating a new source of truth.

v0.1 reads a small snapshot definition and calculates dashboard metrics.
Future source adapters may populate the same section schema from Asana, Gmail,
Slack, Drive, and GitHub. The dashboard file itself remains a disposable view.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "dashboard" / "data" / "dashboard_source.json"
DEFAULT_OUTPUT = ROOT / "dashboard" / "data" / "dashboard.json"
SECTION_KEYS = ("decisions", "projects", "sales", "grants", "brand", "risks")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def build(source: dict) -> dict:
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
    overdue = sum(
        1
        for key in SECTION_KEYS
        for item in normalized[key]
        if item.get("status") == "期限超過"
    )

    return {
        "generated_at": datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST"),
        "mode": source.get("mode", "v0.1 snapshot"),
        "summary": {
            "decisions": decisions,
            "overdue": overdue,
            "opportunities": opportunities,
            "risks": risks,
        },
        "sections": normalized,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source = load_json(args.input)
    output = build(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CEO Dashboard generated: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
