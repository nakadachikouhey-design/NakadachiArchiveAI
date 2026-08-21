import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_ceo_dashboard.py"
spec = importlib.util.spec_from_file_location("build_ceo_dashboard", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_refresh_calculates_summary():
    source = {
        "mode": "test",
        "sections": {
            "decisions": [{"title": "A"}],
            "projects": [{"title": "B", "status": "期限超過"}],
            "sales": [{"title": "C", "priority": "high"}],
            "grants": [{"title": "D", "label": "重点"}],
            "brand": [],
            "risks": [{"title": "E"}],
        },
    }
    out = mod.refresh(source)
    assert out["summary"]["decisions"] == 1
    assert out["summary"]["overdue"] == 1
    assert out["summary"]["opportunities"] == 2
    assert out["summary"]["risks"] == 1
    assert set(out["sections"]) == set(mod.SECTION_KEYS)
