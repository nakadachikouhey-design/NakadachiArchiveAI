from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import storage_search


class StorageSearchTests(unittest.TestCase):
    def test_normalise_handles_width_case_and_spaces(self) -> None:
        self.assertEqual(storage_search.normalise(" 防災 博士 "), storage_search.normalise("防災博士"))
        self.assertEqual(storage_search.normalise("ＡＢＣ"), "abc")

    def test_search_finds_named_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Trancend"
            target = root / "古い" / "防災博士"
            target.mkdir(parents=True)
            (target / "IMG_0001.MOV").write_bytes(b"x")

            with mock.patch.object(storage_search, "discover_storage_roots", return_value=([root], [], [])):
                result = storage_search.search_local_storage("防災博士", max_results=20)

            self.assertEqual(result["status"], "ok")
            self.assertTrue(
                any(item["type"] == "directory" and item["path"] == str(target) for item in result["results"])
            )

    def test_search_matches_parent_path_for_generic_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Trancend"
            target = root / "古い" / "防災博士"
            target.mkdir(parents=True)
            generic = target / "IMG_0001.MOV"
            generic.write_bytes(b"x")

            with mock.patch.object(storage_search, "discover_storage_roots", return_value=([root], [], [])):
                result = storage_search.search_local_storage(
                    "防災博士", include_directories=False, extensions=["mov"]
                )

            paths = {item["path"] for item in result["results"]}
            self.assertIn(str(generic), paths)

    def test_search_extension_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "archive"
            target = root / "防災博士"
            target.mkdir(parents=True)
            mov = target / "防災博士.mov"
            pdf = target / "防災博士.pdf"
            mov.write_bytes(b"mov")
            pdf.write_bytes(b"pdf")

            with mock.patch.object(storage_search, "discover_storage_roots", return_value=([root], [], [])):
                result = storage_search.search_local_storage(
                    "防災博士", include_directories=False, extensions=["mov"]
                )

            paths = {item["path"] for item in result["results"]}
            self.assertIn(str(mov), paths)
            self.assertNotIn(str(pdf), paths)


if __name__ == "__main__":
    unittest.main()
