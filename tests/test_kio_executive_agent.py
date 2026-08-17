from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import kio_executive_agent as agent


class ExecutiveAgentTests(unittest.TestCase):
    def test_date_validation(self) -> None:
        self.assertEqual(agent.normalize_date("2026-08-15"), "2026-08-15")
        self.assertEqual(agent.normalize_date("15/08/2026"), "")

    def test_cli_accepts_runtime_options_after_subcommand(self) -> None:
        with mock.patch.object(agent, "validate_installation", return_value={"ok": True}):
            self.assertEqual(agent.main(["validate", "--kps-root", "/tmp/kps", "--format", "json"]), 0)

    def test_decision_lifecycle_writes_kps_runtime_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            case = make_case("KIO-TEST-001")
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
            self.assertTrue((state / "registry.json").is_file())
            self.assertTrue((state / "active-cases.md").is_file())
            self.assertTrue((state / "decisions" / "KIO-TEST-001-DEC-01.json").is_file())

    def test_retrieved_evidence_is_not_a_fact_until_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            source = state / "proposal.pdf"
            source.write_bytes(b"approved proposal")
            case = make_case("KIO-TEST-002")
            case["evidence_candidates"] = [{
                "evidence_id": "EVD-01", "file_name": "proposal.pdf", "path": str(source),
                "verification_status": "candidate", "verification_reason": "", "verified_fact": "",
                "verified_at": "", "verified_by": "", "sha256": ""
            }]
            agent.save_case(state, case)
            self.assertNotIn("proposal.pdf", " ".join(case["facts"]))
            result = agent.verify_evidence(
                state, case["case_id"], "EVD-01", "提案は承認済みである", "正式版を原本で確認", "中立公平"
            )
            self.assertTrue(result["ok"])
            updated = agent.load_case(state, case["case_id"])
            self.assertEqual(updated["evidence_candidates"][0]["verification_status"], "verified")
            self.assertIn("proposal.pdf", " ".join(updated["facts"]))
            self.assertEqual(len(updated["evidence_candidates"][0]["sha256"]), 64)

    def test_verify_evidence_rejects_unavailable_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            case = make_case("KIO-TEST-004")
            case["evidence_candidates"] = [{
                "evidence_id": "EVD-01", "file_name": "missing.pdf", "path": "/missing/source.pdf",
                "verification_status": "candidate", "verification_reason": "", "verified_fact": "",
                "verified_at": "", "verified_by": "", "sha256": ""
            }]
            agent.save_case(state, case)
            result = agent.verify_evidence(
                state, case["case_id"], "EVD-01", "確認した事実", "正式版確認", "中立公平"
            )
            self.assertFalse(result["ok"])

    def test_complete_requires_verified_evidence_and_blocks_failed_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            case = make_case("KIO-TEST-003")
            case["actions"] = [{"id": "A", "type": "validate-kps", "status": "failed", "result": "failed"}]
            case["evidence_candidates"] = [{
                "evidence_id": "EVD-01", "file_name": "verified.pdf", "path": "/archive/verified.pdf",
                "verification_status": "verified", "verification_reason": "checked"
            }]
            agent.save_case(state, case)
            self.assertFalse(agent.complete_case(state, case["case_id"], "Done")["ok"])

            case["actions"] = []
            case["evidence_candidates"] = [{
                "evidence_id": "EVD-01", "file_name": "candidate.pdf", "path": "/archive/candidate.pdf",
                "verification_status": "candidate", "verification_reason": ""
            }]
            agent.save_case(state, case)
            self.assertFalse(agent.complete_case(state, case["case_id"], "Done")["ok"])

            case["due_date"] = "2000-01-01"
            case["evidence_candidates"][0]["verification_status"] = "verified"
            agent.save_case(state, case)
            result = agent.complete_case(state, case["case_id"], "Done")
            self.assertTrue(result["ok"])
            completed = agent.load_case(state, case["case_id"])
            self.assertEqual(completed["deadline_status"], "closed")

    def test_case_id_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            self.assertIsNone(agent.load_case(state, "../../secret"))
            bad = make_case("../../secret")
            with self.assertRaises(ValueError):
                agent.save_case(state, bad)

    def test_deadline_status(self) -> None:
        self.assertEqual(agent.deadline_status("2000-01-01"), "overdue")
        self.assertEqual(agent.deadline_status("2000-01-01", "completed"), "closed")

    def test_external_action_record_requires_accepted_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            case = make_case("KIO-TEST-005")
            agent.save_case(state, case)
            blocked = agent.record_action_result(
                state, case["case_id"], "send-email", "completed", "sent", True, "KIO-TEST-005-DEC-01"
            )
            self.assertFalse(blocked["ok"])
            case["decisions"] = [{"decision_id": "KIO-TEST-005-DEC-01", "status": "accepted"}]
            agent.save_case(state, case)
            recorded = agent.record_action_result(
                state, case["case_id"], "send-email", "completed", "sent", True, "KIO-TEST-005-DEC-01"
            )
            self.assertTrue(recorded["ok"])
            self.assertEqual(recorded["action"]["authorization_decision_id"], "KIO-TEST-005-DEC-01")

    def test_create_case_end_to_end_with_sqlite_and_kps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "archive.sqlite"
            profiles = root / "profiles.json"
            kps = root / "kps"
            make_database(db_path)
            profiles.write_text('{"profiles": []}\n', encoding="utf-8")
            (kps / "projects").mkdir(parents=True)
            (kps / "projects" / "registry.md").write_text("# Projects\n", encoding="utf-8")
            state = agent.resolve_state_dir("", kps)
            args = argparse.Namespace(
                request="KIO proposal", project="", project_id="PRJ-001", due="2099-08-15", limit=5,
                execute_safe=False, db=str(db_path), profiles=str(profiles),
            )
            result = agent.create_case(args, state, kps)
            self.assertTrue(result["ok"])
            case = result["case"]
            self.assertEqual(case["evidence_candidates"][0]["verification_status"], "candidate")
            self.assertNotIn("proposal.pdf", " ".join(case["facts"]))
            self.assertTrue((kps / ".kps-runtime" / "executive-agent" / "registry.json").is_file())

    def test_private_runtime_permissions_are_enforced_with_public_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "kps" / ".kps-runtime" / "executive-agent"
            previous_umask = os.umask(0o022)
            try:
                agent.save_case(state, make_case("KIO-TEST-006"))
            finally:
                os.umask(previous_umask)

            self.assertEqual((state.parent.stat().st_mode & 0o777), 0o700)
            for path in (state, *state.rglob("*")):
                if path.is_symlink():
                    continue
                expected = 0o700 if path.is_dir() else 0o600
                self.assertEqual(path.stat().st_mode & 0o777, expected, str(path))

    def test_refresh_lock_is_blocked_not_completed(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"status":"skipped_locked","returncode":0}\n',
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            archive_root = Path(temporary)
            script = archive_root / "scripts" / "run_auto_update.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.object(agent.subprocess, "run", return_value=completed):
                result = agent.execute_allowlisted_action("refresh-archive-index", archive_root, archive_root)
        self.assertEqual(result["status"], "blocked")


def make_case(case_id: str) -> dict:
    return {
        "case_id": case_id, "status": "waiting-decision", "decisions": [], "actions": [],
        "facts": [], "inferences": [], "evidence_candidates": [], "request": "test",
        "kps_project_id": "PRJ-001", "due_date": "2099-08-15", "decision_required": "Proceed?",
        "outcome": "", "updated_at": "", "created_at": "",
    }


def make_database(path: Path) -> None:
    fields = {
        "file_name": "TEXT", "extension": "TEXT", "size_bytes": "INTEGER", "modified_at": "TEXT",
        "full_path": "TEXT", "parent_folder": "TEXT", "source_root": "TEXT", "source_role": "TEXT",
        "duplicate_group": "TEXT", "project_candidates": "TEXT", "person_candidates": "TEXT",
        "organization_candidates": "TEXT", "event_candidates": "TEXT", "year_candidates": "TEXT",
        "media_type_candidate": "TEXT", "importance_candidate": "TEXT", "generated_tags": "TEXT",
        "ai_category": "TEXT", "ai_subcategory": "TEXT", "ai_confidence": "REAL", "ai_reason": "TEXT",
        "text_excerpt": "TEXT", "ocr_text": "TEXT", "ocr_status": "TEXT", "duration_seconds": "TEXT",
        "width": "TEXT", "height": "TEXT", "codec": "TEXT",
    }
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, " + ", ".join(f"{name} {kind}" for name, kind in fields.items()) + ")")
        values = {name: "" for name in fields}
        values.update({
            "file_name": "proposal.pdf", "extension": ".pdf", "modified_at": "2026-08-01T00:00:00+09:00",
            "full_path": "/archive/KIO/proposal.pdf", "parent_folder": "/archive/KIO", "source_root": "/archive",
            "source_role": "google_drive", "project_candidates": json.dumps(["KIO"]),
            "person_candidates": "[]", "organization_candidates": "[]", "event_candidates": "[]",
            "year_candidates": "[2026]", "generated_tags": json.dumps(["KIO", "proposal"]),
            "ai_category": "document", "ai_confidence": 0.91, "text_excerpt": "KIO proposal evidence",
        })
        columns = list(values)
        db.execute(
            f"INSERT INTO files ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [values[name] for name in columns],
        )


if __name__ == "__main__":
    unittest.main()
