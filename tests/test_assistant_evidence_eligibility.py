from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import assistant_ai


class AssistantEvidenceEligibilityTests(unittest.TestCase):
    def test_hub_indexes_are_reference_context_not_evidence(self) -> None:
        eligible, reason = assistant_ai.evidence_eligibility(
            {
                "file_name": "KNOWLEDGE_INDEX.md",
                "full_path": "/Users/example/Documents/Codex/00_CHURITSU_HUB/KNOWLEDGE_INDEX.md",
                "extension": ".md",
                "source_role": "generated",
            }
        )
        self.assertFalse(eligible)
        self.assertIn(reason, {"generated_index_or_hub_document", "generated_reference_context"})

    def test_codex_tmp_scripts_are_not_evidence(self) -> None:
        eligible, reason = assistant_ai.evidence_eligibility(
            {
                "file_name": "build_kio_master.mjs",
                "full_path": "/Users/example/Documents/劇団KIO/.codex_tmp/kio_jisseki_master/build_kio_master.mjs",
                "extension": ".mjs",
                "source_role": "",
            }
        )
        self.assertFalse(eligible)
        self.assertEqual(reason, "temporary_or_working_file")

    def test_business_master_spreadsheet_is_evidence(self) -> None:
        eligible, reason = assistant_ai.evidence_eligibility(
            {
                "file_name": "KIO_実績マスター.xlsx",
                "full_path": "/Users/example/Documents/劇団KIO/outputs/kio_jisseki_master/KIO_実績マスター.xlsx",
                "extension": ".xlsx",
                "source_role": "original",
            }
        )
        self.assertTrue(eligible)
        self.assertEqual(reason, "eligible_primary_or_business_evidence")

    def test_generated_reference_is_annotated(self) -> None:
        item = assistant_ai.annotate_eligibility(
            {
                "file_name": "PROJECT_INDEX.md",
                "full_path": "/Users/example/Documents/Codex/00_CHURITSU_HUB/PROJECT_INDEX.md",
                "extension": ".md",
                "source_role": "generated",
            }
        )
        self.assertFalse(item["evidence_eligible"])
        self.assertTrue(item["evidence_eligibility_reason"])


if __name__ == "__main__":
    unittest.main()
