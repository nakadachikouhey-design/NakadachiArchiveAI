from __future__ import annotations

from pathlib import Path

import storage_search


def test_normalise_handles_case_width_and_spaces():
    assert storage_search.normalise(" 防災 博士 ") == storage_search.normalise("防災博士")
    assert storage_search.normalise("ＡＢＣ") == "abc"


def test_discover_storage_roots_includes_runtime_mounts(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    configured.mkdir()
    volumes = tmp_path / "Volumes"
    mounted = volumes / "Trancend"
    mounted.mkdir(parents=True)
    cloud = tmp_path / "CloudStorage"
    gdrive = cloud / "GoogleDrive-test"
    gdrive.mkdir(parents=True)

    monkeypatch.setattr(storage_search.automation, "configured_scan_roots", lambda: ([configured], [], []))
    original_path = storage_search.Path

    def fake_path(value):
        if str(value) == "/Volumes":
            return volumes
        return original_path(value)

    monkeypatch.setattr(storage_search, "Path", fake_path)
    monkeypatch.setattr(storage_search.Path, "home", lambda: tmp_path)

    roots, _, _ = storage_search.discover_storage_roots()
    root_strings = {str(root) for root in roots}
    assert str(configured.resolve()) in root_strings
    assert str(mounted.resolve()) in root_strings
    assert str(gdrive.resolve()) in root_strings


def test_search_finds_named_directory_before_generic_media(tmp_path, monkeypatch):
    root = tmp_path / "Trancend"
    target = root / "古い" / "防災博士"
    target.mkdir(parents=True)
    (target / "IMG_0001.MOV").write_bytes(b"x")

    monkeypatch.setattr(storage_search, "discover_storage_roots", lambda: ([root], [], []))
    result = storage_search.search_local_storage("防災博士", max_results=20)

    assert result["status"] == "ok"
    assert any(item["type"] == "directory" and item["path"] == str(target) for item in result["results"])


def test_search_extension_filter(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    target = root / "防災博士"
    target.mkdir(parents=True)
    (target / "防災博士.mov").write_bytes(b"mov")
    (target / "防災博士.pdf").write_bytes(b"pdf")

    monkeypatch.setattr(storage_search, "discover_storage_roots", lambda: ([root], [], []))
    result = storage_search.search_local_storage(
        "防災博士", include_directories=False, extensions=["mov"]
    )

    paths = {item["path"] for item in result["results"]}
    assert str(target / "防災博士.mov") in paths
    assert str(target / "防災博士.pdf") not in paths
