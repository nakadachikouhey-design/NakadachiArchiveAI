from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import assistant_ai


class TactStrictIdentityGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        profiles = json.loads((ROOT / "config" / "project_profiles.json").read_text(encoding="utf-8"))["profiles"]
        cls.profile = next(item for item in profiles if item["id"] == "tact_fest")

    def test_tact_requires_identity_match(self) -> None:
        self.assertTrue(self.profile["require_identity_match"])
        self.assertIn("FSTF", self.profile["identity_terms"])
        self.assertNotIn("KIO_実績マスター", self.profile["identity_terms"])

    def test_generic_context_document_is_rejected(self) -> None:
        relevant, reason, _ = assistant_ai.project_relevance(
            {
                "file_name": "想定質問（博物館誘客促進）.docx",
                "full_path": "/Users/example/Documents/想定質問（博物館誘客促進）.docx",
                "snippet": "国際 教育 劇場 参加者 報告書",
                "generated_tags": ["国際", "教育", "劇場"],
            },
            self.profile,
        )
        self.assertFalse(relevant)
        self.assertEqual(reason, "missing_required_project_identity")

    def test_fstf_document_is_accepted(self) -> None:
        relevant, reason, score = assistant_ai.project_relevance(
            {
                "file_name": "FSTF 2017.pdf",
                "full_path": "/Volumes/Archive/TACT/FSTF 2017.pdf",
                "snippet": "FSTF 2017 program",
            },
            self.profile,
        )
        self.assertTrue(relevant)
        self.assertEqual(reason, "project_identity_match")
        self.assertGreaterEqual(score, 5)

    def test_cross_project_master_remains_allowed(self) -> None:
        relevant, reason, score = assistant_ai.project_relevance(
            {
                "file_name": "KIO_実績マスター.xlsx",
                "full_path": "/Users/example/Documents/劇団KIO/outputs/KIO_実績マスター.xlsx",
            },
            self.profile,
        )
        self.assertTrue(relevant)
        self.assertEqual(reason, "approved_cross_project_master")
        self.assertEqual(score, 100)

    def test_reference_context_is_not_media_candidate(self) -> None:
        bullets = assistant_ai.media_bullets(
            [
                {
                    "file_name": "KNOWLEDGE_INDEX.md",
                    "full_path": "/Users/example/Documents/Codex/00_CHURITSU_HUB/KNOWLEDGE_INDEX.md",
                    "media_type_candidate": "document",
                    "evidence_eligible": False,
                    "project_relevant": True,
                },
                {
                    "file_name": "FSTF 2017.pdf",
                    "full_path": "/Volumes/Archive/TACT/FSTF 2017.pdf",
                    "media_type_candidate": "document",
                    "evidence_eligible": True,
                    "project_relevant": True,
                    "ai_category": "grant_report",
                },
            ]
        )
        rendered = "\n".join(bullets)
        self.assertNotIn("KNOWLEDGE_INDEX.md", rendered)
        self.assertIn("FSTF 2017.pdf", rendered)


if __name__ == "__main__":
    unittest.main()
