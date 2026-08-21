#!/usr/bin/env python3
"""Sync KIO CEO Dashboard from Asana without creating a new source of truth.

Asana remains authoritative for tasks, assignees, due dates, and project state.
This script reads selected KIO projects through the Asana REST API and updates
only dashboard/data/dashboard.json, which is a disposable read model.

Required environment variable:
  ASANA_ACCESS_TOKEN
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "data" / "dashboard.json"
API_BASE = "https://app.asana.com/api/1.0"

TRACKED_PROJECTS = {
    "1217094584555870": "イベント企画・実行",
    "1217095398969718": "なにわ大賞・なにわ名物てれび",
    "1217112836869270": "大阪フリンジ／大阪文化万博",
    "1217143821018879": "Osaka Fringe Production 1.0",
    "1217475284698028": "AI Chief of Staff / PMO",
}

CEO_PROJECT_GID = "1217475284698028"
CEO_SECTION_NAME = "20｜CEO判断待ち"
IGNORED_EXACT_NAMES = {
    "CEOには判断事項だけを上げる",
    "新規案件はまずここへ集約",
    "決定済み案件を担当者が実行",
    "AIが事実確認・選択肢・リスク・推奨案を整理",
    "返信・承認・外部条件待ちを隔離",
}
MAX_RISKS = 10


def api_get(path: str, token: str, params: dict[str, str] | None = None) -> list[dict] | dict:
    query = urllib.parse.urlencode(params or {})
    url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return payload["data"]


def project_tasks(project_gid: str, token: str) -> list[dict]:
    data = api_get(
        f"/projects/{project_gid}/tasks",
        token,
        {
            "completed_since": "now",
            "limit": "100",
            "opt_fields": "name,assignee.name,due_on,completed,modified_at,memberships.section.name,permalink_url",
        },
    )
    return [task for task in data if not task.get("completed")]


def section_name(task: dict) -> str:
    memberships = task.get("memberships") or []
    for membership in memberships:
        section = membership.get("section") or {}
        if section.get("name"):
            return section["name"]
    return ""


def assignee_name(task: dict) -> str | None:
    assignee = task.get("assignee") or {}
    return assignee.get("name")


def item_from_task(task: dict, *, label: str, priority: str = "medium", status: str | None = None) -> dict:
    item = {
        "title": task.get("name", "(名称なし)"),
        "priority": priority,
        "label": label,
        "source": "Asana",
        "url": task.get("permalink_url"),
    }
    if assignee_name(task):
        item["owner"] = assignee_name(task)
    if task.get("due_on"):
        item["due"] = task["due_on"]
    if status:
        item["status"] = status
    return item


def sync(base: dict, tasks_by_project: dict[str, list[dict]], today: date) -> dict:
    sections = base.setdefault("sections", {})

    # CEO decisions: only real tasks sitting in the dedicated Asana section.
    decisions = []
    for task in tasks_by_project.get(CEO_PROJECT_GID, []):
        if task.get("name") in IGNORED_EXACT_NAMES:
            continue
        if section_name(task) != CEO_SECTION_NAME:
            continue
        decisions.append(item_from_task(task, label="CEO判断", priority="high", status="判断待ち"))
    decisions.sort(key=lambda x: (x.get("due") is None, x.get("due") or "9999-12-31"))
    sections["decisions"] = decisions

    # Project overview: counts and nearest due date from authoritative Asana tasks.
    projects = []
    for project_gid, project_name in TRACKED_PROJECTS.items():
        tasks = tasks_by_project.get(project_gid, [])
        if not tasks:
            continue
        dues = sorted(task["due_on"] for task in tasks if task.get("due_on"))
        overdue_count = sum(1 for task in tasks if task.get("due_on") and date.fromisoformat(task["due_on"]) < today)
        status = f"未完了 {len(tasks)}件"
        if overdue_count:
            status += f" / 期限超過 {overdue_count}件"
        item = {
            "title": project_name,
            "status": status,
            "priority": "high" if overdue_count else "medium",
            "label": "要確認" if overdue_count else "進行中",
            "source": "Asana",
        }
        if dues:
            item["due"] = dues[0]
        projects.append(item)
    projects.sort(key=lambda x: (0 if x["priority"] == "high" else 1, x.get("due") or "9999-12-31"))
    sections["projects"] = projects

    # Risks: overdue tasks and explicit blocker-section tasks. Keep output short.
    risk_candidates: list[tuple[str, dict]] = []
    overdue_total = 0
    for project_gid, project_name in TRACKED_PROJECTS.items():
        for task in tasks_by_project.get(project_gid, []):
            if task.get("name") in IGNORED_EXACT_NAMES:
                continue
            due = task.get("due_on")
            is_overdue = bool(due and date.fromisoformat(due) < today)
            is_blocker = "Blocker" in section_name(task)
            if is_overdue:
                overdue_total += 1
            if not (is_overdue or is_blocker):
                continue
            reason = "期限超過" if is_overdue else "Blocker"
            item = item_from_task(task, label=reason, priority="high", status=project_name)
            risk_candidates.append((due or "9999-12-31", item))
    risk_candidates.sort(key=lambda pair: pair[0])
    sections["risks"] = [item for _, item in risk_candidates[:MAX_RISKS]]

    # Sales / grants / brand remain from the existing read model until their adapters exist.
    opportunities = sum(
        1
        for key in ("sales", "grants", "brand")
        for item in sections.get(key, [])
        if item.get("priority") == "high" or item.get("label") in {"成長機会", "重点"}
    )

    base["summary"] = {
        "decisions": len(decisions),
        "overdue": overdue_total,
        "opportunities": opportunities,
        "risks": len(sections["risks"]),
    }
    base["generated_at"] = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M JST")
    base["mode"] = "v0.2 Asana live read model"
    return base


def main() -> int:
    token = os.getenv("ASANA_ACCESS_TOKEN")
    if not token:
        print("ASANA_ACCESS_TOKEN is required", file=sys.stderr)
        return 2
    if not DASHBOARD.exists():
        print(f"Dashboard file not found: {DASHBOARD}", file=sys.stderr)
        return 2

    base = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    tasks_by_project = {gid: project_tasks(gid, token) for gid in TRACKED_PROJECTS}
    output = sync(base, tasks_by_project, datetime.now(ZoneInfo("Asia/Tokyo")).date())
    DASHBOARD.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CEO Dashboard synced from Asana: {DASHBOARD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
