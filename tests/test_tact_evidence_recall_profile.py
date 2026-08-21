from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config" / "project_profiles.json"


class TactEvidenceRecallProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        data = json.loads(PROFILES.read_text(encoding="utf-8"))
        cls.profile = next(item for item in data["profiles"] if item["id"] == "tact_fest")

    def test_historical_aliases_cover_fstf_and_year_variants(self) -> None:
        aliases = set(self.profile["aliases"])
        self.assertIn("FSTF", aliases)
        self.assertIn("FSTF 2017", aliases)
        self.assertIn("TACT 2017", aliases)

    def test_cross_project_master_is_a_retrieval_alias(self) -> None:
        self.assertIn("KIO_実績マスター", self.profile["aliases"])

    def test_business_evidence_terms_are_in_keywords(self) -> None:
        keywords = set(self.profile["keywords"])
        for term in ("事業報告", "報告書", "助成", "プログラム", "公演資料", "実績マスター"):
            self.assertIn(term, keywords)

    def test_evidence_needs_include_master_and_program(self) -> None:
        needs = set(self.profile["evidence_needs"])
        self.assertIn("実績マスター", needs)
        self.assertIn("事業報告", needs)
        self.assertIn("プログラム", needs)


if __name__ == "__main__":
    unittest.main()
