from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from exporter import ArchiveExporter
from private_storage import ensure_private_directory, harden_private_tree, write_private_text


class PrivateStorageTests(unittest.TestCase):
    def test_harden_existing_tree_is_idempotent_and_does_not_follow_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private.mkdir(mode=0o755)
            private.chmod(0o755)
            inside = private / "record.json"
            inside.write_text("{}", encoding="utf-8")
            inside.chmod(0o644)
            outside = root / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            outside.chmod(0o644)
            (private / "outside-link").symlink_to(outside)

            harden_private_tree(private)
            harden_private_tree(private)

            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            self.assertEqual(inside.stat().st_mode & 0o777, 0o600)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o644)

    def test_archive_exports_are_private_with_public_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            previous_umask = os.umask(0o022)
            try:
                exporter = ArchiveExporter(output, datetime.now().astimezone(), {"commit_every": 1000})
                exporter.finish(datetime.now().astimezone(), [], [])
            finally:
                os.umask(previous_umask)

            for path in (output, *output.rglob("*")):
                if path.is_symlink():
                    continue
                expected = 0o700 if path.is_dir() else 0o600
                self.assertEqual(path.stat().st_mode & 0o777, expected, str(path))

    def test_private_writer_rejects_file_symlink_without_overwriting_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.txt"
            outside.write_text("unchanged", encoding="utf-8")
            private = root / "private"
            private.mkdir()
            link = private / "record.json"
            link.symlink_to(outside)

            with self.assertRaises(ValueError):
                write_private_text(link, "OVERWRITTEN")
            self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")

    def test_private_directory_rejects_symlink_without_writing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            link = root / "private"
            link.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                ensure_private_directory(link)
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
