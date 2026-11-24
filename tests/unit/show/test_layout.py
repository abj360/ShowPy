#!/usr/bin/env python3

"""
test_layout.py --- tests the layout version file reader

Contains:
    test_read_layout_reports_layout_1_for_a_v040_root: reads the layout-1 fixture root
    test_read_layout_reports_the_current_layout_for_an_empty_or_missing_root: expects layout 2
    test_read_layout_reads_a_layout_2_file: reads a layout.json of layout 2
    test_read_layout_refuses_a_newer_layout: raises VersionError naming both layouts
    test_read_layout_refuses_other_content: raises CORRUPT for every other file content
    test_read_layout_refuses_a_root_that_is_neither_form: raises CORRUPT for a stray file
    test_read_layout_refuses_a_root_that_is_a_file: raises CORRUPT
    test_read_layout_does_not_follow_a_symlinked_layout_file: raises CORRUPT
    test_read_layout_prefers_the_file_over_layout_1_folders: reads layout.json first
"""

import json
import shutil
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, VersionError
from e8s.show.layout import CURRENT_LAYOUT, LAYOUT_FILE, LAYOUT_FORMAT, read_layout

FIXTURE_REPO = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.4.0" / "repo"


def _write_layout(root: Path, content: str | bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / LAYOUT_FILE
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


def _layout_json(layout: object) -> str:
    return json.dumps({"format": LAYOUT_FORMAT, "layout": layout})


def test_read_layout_reports_layout_1_for_a_v040_root() -> None:
    assert read_layout(FIXTURE_REPO) == 1


def test_read_layout_reports_the_current_layout_for_an_empty_or_missing_root(
    tmp_path: Path,
) -> None:
    assert read_layout(tmp_path / "missing") == CURRENT_LAYOUT
    (tmp_path / "empty").mkdir()
    assert read_layout(tmp_path / "empty") == CURRENT_LAYOUT


def test_read_layout_reads_a_layout_2_file(tmp_path: Path) -> None:
    _write_layout(tmp_path, _layout_json(2))
    assert read_layout(tmp_path) == 2


def test_read_layout_refuses_a_newer_layout(tmp_path: Path) -> None:
    _write_layout(tmp_path, _layout_json(3))
    with pytest.raises(VersionError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.VERSION
    assert "layout 3" in str(caught.value)
    assert f"layout {CURRENT_LAYOUT}" in str(caught.value)


OTHER_CONTENT = {
    "wrong format": json.dumps({"format": "other", "layout": 2}),
    "layout 1 in the file": _layout_json(1),
    "layout 0": _layout_json(0),
    "string layout": _layout_json("2"),
    "float layout": _layout_json(2.5),
    "bool layout": json.dumps({"format": LAYOUT_FORMAT, "layout": True}),
    "extra key": json.dumps({"format": LAYOUT_FORMAT, "layout": 2, "extra": 1}),
    "missing key": json.dumps({"format": LAYOUT_FORMAT}),
    "not an object": "[2]",
    "unparsable": "{",
    "empty": "",
    "oversized": " " * 2048 + _layout_json(2),
}


@pytest.mark.parametrize("content", OTHER_CONTENT.values(), ids=OTHER_CONTENT.keys())
def test_read_layout_refuses_other_content(tmp_path: Path, content: str) -> None:
    _write_layout(tmp_path, content)
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_refuses_a_root_that_is_neither_form(tmp_path: Path) -> None:
    (tmp_path / "stray.txt").write_text("x")
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_refuses_a_root_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "root").write_text("x")
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path / "root")
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_does_not_follow_a_symlinked_layout_file(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text(_layout_json(2))
    root = tmp_path / "root"
    root.mkdir()
    (root / LAYOUT_FILE).symlink_to(target)
    with pytest.raises(StorageError) as caught:
        read_layout(root)
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_prefers_the_file_over_layout_1_folders(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE_REPO, tmp_path / "repo")
    assert read_layout(tmp_path / "repo") == 1
    _write_layout(tmp_path / "repo", _layout_json(2))
    assert read_layout(tmp_path / "repo") == 2
