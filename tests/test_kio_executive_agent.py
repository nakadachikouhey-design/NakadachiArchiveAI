from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import kio_executive_agent as agent


class ExecutiveAgentTests(unittest.TestCase):
    def test_date_validation(self) -> None:
        self.assertEqual(agent.normalize_date("2026-08-15"), "2026-08-15")
        self.assertEqual(agent.normalize_date("15/08/2026"), "")

    def test_decision_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            case = {
                "case_id": "KIO-TEST-001",
                "status": "waiting-decision",
                "decisions": [],
                "actions": [],
                "facts": [],
                "inferences": [],
                "kps_project_id": "PRJ-001",
                "due_date": "2026-08-15",
                "decision_required": "Proceed?",
                "outcome": "",
                "updated_at": "",
            }
            agent.save_case(state, case)
            args = argparse.Namespace(
                case_id="KIO-TEST-001", decision="Proceed", status="accepted",
                reason="Evidence is sufficient", review_date="2026-09-01"
            )
            result = agent.record_decision(state, args)
            self.assertTrue(result["ok"])
            updated = agent.load_case(state, "KIO-TEST-001")
            self.assertEqual(updated["status"], "in-progress")
            self.assertEqual(updated["decisions"][0]["status"], "accepted")

    def test_complete_blocks_failed_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            case = {
                "case_id": "KIO-TEST-002", "status": "in-progress", "decisions": [],
                "actions": [{"id": "A", "status": "failed"}], "facts": [], "inferences": [],
                "kps_project_id": "PRJ-001", "due_date": "", "decision_required": "", "outcome": ""
            }
            agent.save_case(state, case)
            result = agent.complete_case(state, "KIO-TEST-002", "Done")
            self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
