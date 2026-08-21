import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


build = load_module("build_ceo_dashboard", "scripts/build_ceo_dashboard.py")
sync = load_module("sync_ceo_dashboard_asana", "scripts/sync_ceo_dashboard_asana.py")


def test_refresh_calculates_v02_summary():
    source = {
        "mode": "test",
        "summary": {"overdue": 0},
        "sections": {
            "decisions": [{"title": "A"}],
            "projects": [],
            "sales": [{"title": "C", "priority": "high"}],
            "grants": [{"title": "D", "label": "重点"}],
            "brand": [],
            "risks": [{"title": "E", "label": "期限超過", "source": "Asana", "due": "2026-08-20"}],
        },
    }
    out = build.refresh(source, today=date(2026, 8, 22))
    assert out["summary"]["decisions"] == 1
    assert out["summary"]["overdue"] == 1
    assert out["summary"]["opportunities"] == 2
    assert out["summary"]["risks"] == 1
    assert set(out["sections"]) == set(build.SECTION_KEYS)


def test_asana_sync_uses_ceo_section_and_counts_overdue():
    base = {
        "sections": {
            "sales": [{"title": "Sales", "priority": "high"}],
            "grants": [],
            "brand": [],
            "decisions": [],
            "projects": [],
            "risks": [],
        }
    }
    tasks = {
        sync.CEO_PROJECT_GID: [
            {
                "name": "【CEO判断】Test",
                "completed": False,
                "due_on": "2026-08-21",
                "memberships": [{"section": {"name": sync.CEO_SECTION_NAME}}],
                "permalink_url": "https://app.asana.com/test/decision",
            },
            {
                "name": "AIが事実確認・選択肢・リスク・推奨案を整理",
                "completed": False,
                "memberships": [{"section": {"name": "10｜AI整理・調査"}}],
            },
        ]
    }
    for gid in sync.TRACKED_PROJECTS:
        tasks.setdefault(gid, [])

    out = sync.sync(base, tasks, today=date(2026, 8, 22))
    assert out["summary"]["decisions"] == 1
    assert out["summary"]["overdue"] == 1
    assert out["sections"]["decisions"][0]["title"] == "【CEO判断】Test"
    assert out["sections"]["risks"][0]["label"] == "期限超過"
