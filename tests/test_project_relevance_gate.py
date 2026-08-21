from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import assistant_ai


TACT_PROFILE = {
    "id": "tact_fest",
    "name": "TACT/FEST",
    "aliases": ["TACT/FEST", "TACT", "TACT FEST", "FSTF", "国際児童青少年舞台芸術フェスティバル", "KIO_実績マスター"],
    "keywords": ["児童青少年", "舞台芸術", "フェスティバル", "国際", "教育", "劇場", "事業報告", "助成", "プログラム", "公演資料"],
    "evidence_needs": ["開催実績", "参加者情報", "公演資料", "教育資料", "写真・映像", "報告書", "助成金資料", "実績マスター", "事業報告", "プログラム"],
}


class ProjectRelevanceGateTests(unittest.TestCase):
    def test_tact_named_material_is_relevant(self) -> None:
        item = {"file_name": "FSTF 2017.pdf", "full_path": "/archive/TACT/FSTF 2017.pdf", "generated_tags": ["festival"]}
        relevant, reason, score = assistant_ai.project_relevance(item, TACT_PROFILE)
        self.assertTrue(relevant)
        self.assertEqual(reason, "project_identity_match")
        self.assertGreater(score, 0)

    def test_unrelated_elephant_script_is_not_tact_evidence(self) -> None:
        item = {"file_name": "台本_ゾウ台本_2018.doc", "full_path": "/archive/KIO/ゾウの休日/台本_ゾウ台本_2018.doc", "generated_tags": ["舞台芸術"]}
        relevant, reason, _ = assistant_ai.project_relevance(item, TACT_PROFILE)
        self.assertFalse(relevant)
        self.assertEqual(reason, "insufficient_project_relevance")

    def test_cross_project_master_is_allowed(self) -> None:
        item = {"file_name": "KIO_実績マスター.xlsx", "full_path": "/archive/KIO/KIO_実績マスター.xlsx"}
        relevant, reason, score = assistant_ai.project_relevance(item, TACT_PROFILE)
        self.assertTrue(relevant)
        self.assertEqual(reason, "approved_cross_project_master")
        self.assertEqual(score, 100)

    def test_inspect_sidecar_is_not_evidence(self) -> None:
        eligible, reason = assistant_ai.evidence_eligibility({"file_name": "KIO_実績マスター.xlsx.inspect.ndjson", "full_path": "/archive/KIO/KIO_実績マスター.xlsx.inspect.ndjson", "extension": ".ndjson", "source_role": ""})
        self.assertFalse(eligible)
        self.assertEqual(reason, "derived_sidecar_metadata")

    def test_preview_paths_are_not_evidence(self) -> None:
        eligible, reason = assistant_ai.evidence_eligibility({"file_name": "KIO_実績マスター_page1.png", "full_path": "/archive/KIO/previews/KIO_実績マスター_page1.png", "extension": ".png", "source_role": ""})
        self.assertFalse(eligible)
        self.assertEqual(reason, "temporary_preview_or_working_file")


if __name__ == "__main__":
    unittest.main()
