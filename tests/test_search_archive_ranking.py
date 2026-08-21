from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import search_archive


class SearchArchiveRankingTests(unittest.TestCase):
    def test_fts_relevance_score_inverts_bm25_direction(self) -> None:
        self.assertGreater(
            search_archive.fts_relevance_score(-5.0),
            search_archive.fts_relevance_score(-1.0),
        )

    def test_primary_evidence_gets_positive_quality_adjustment(self) -> None:
        result = {
            "file_name": "KIO_実績マスター.xlsx",
            "full_path": "/Users/example/Documents/劇団KIO/outputs/KIO_実績マスター.xlsx",
            "ai_category": "grant_report",
            "media_type_candidate": "spreadsheet",
            "source_role": "original",
        }
        self.assertGreater(search_archive.source_quality_adjustment(result), 10.0)

    def test_generated_hub_index_is_demoted(self) -> None:
        result = {
            "file_name": "PROJECT_INDEX.md",
            "full_path": "/Users/example/Documents/Codex/00_CHURITSU_HUB/PROJECT_INDEX.md",
            "ai_category": "production",
            "media_type_candidate": "document",
            "source_role": "generated",
        }
        self.assertLess(search_archive.source_quality_adjustment(result), -10.0)

    def test_primary_source_outranks_meta_document_at_equal_base_score(self) -> None:
        primary = {
            "file_name": "2026事業報告書.pdf",
            "full_path": "/Users/example/Documents/OsakaFringe/2026事業報告書.pdf",
            "ai_category": "grant_report",
            "media_type_candidate": "document",
            "source_role": "original",
            "score": 5.0,
        }
        meta = {
            "file_name": "KNOWLEDGE_INDEX.md",
            "full_path": "/Users/example/Documents/Codex/00_CHURITSU_HUB/KNOWLEDGE_INDEX.md",
            "ai_category": "production",
            "media_type_candidate": "document",
            "source_role": "generated",
            "score": 5.0,
        }
        search_archive.apply_source_quality(primary)
        search_archive.apply_source_quality(meta)
        self.assertGreater(primary["score"], meta["score"])
        self.assertEqual(primary["base_score"], 5.0)
        self.assertEqual(meta["base_score"], 5.0)


if __name__ == "__main__":
    unittest.main()
