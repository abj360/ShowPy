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
    test_read_layout_requires_the_root_link_of_a_layout_1_show: needs next/root
    test_read_layout_refuses_folders_without_a_root_link: raises CORRUPT
    test_read_layout_finds_a_layout_1_show_among_other_entries: ignores stray entries
    test_read_layout_reads_a_root_that_holds_both_forms_as_the_file: file wins
    test_read_layout_follows_a_symlinked_root: follows a symlinked root
    test_read_layout_maps_a_too_long_name_to_io: raises IO
    test_read_layout_maps_an_unreadable_root_to_io: raises IO
    test_read_layout_maps_an_unreadable_layout_file_to_io: raises IO
    test_regression_root_holding_only_ds_store_is_empty: reads a root holding only .DS_Store as an
        empty root of the current layout
    test_regression_unreadable_lost_and_found_is_skipped: reads past a lost+found it cannot open
    test_read_layout_skips_housekeeping_names_only: skips every housekeeping name beside a layout-1
        show, and still refuses any other stray entry
    test_read_layout_reads_a_root_holding_only_tmp_as_current: reads the root a first commit left
        before linking its layout file as the current layout
    test_read_layout_reads_a_layout_file_linked_while_listing: reads a layout file a concurrent
        first commit linked after its name was looked up
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, VersionError
from e8s.show.layout import (
    CURRENT_LAYOUT,
    HOUSEKEEPING_NAMES,
    LAYOUT_FILE,
    LAYOUT_FORMAT,
    TEMP_DIR,
    read_layout,
)

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


def _legacy_show(root: Path, name: str = "s", *, with_root_link: bool = True) -> None:
    (root / name / "next").mkdir(parents=True)
    (root / name / "objects").mkdir()
    (root / name / "trees").mkdir()
    if with_root_link:
        (root / name / "next" / "root").write_text("0" * 64)


def test_read_layout_requires_the_root_link_of_a_layout_1_show(tmp_path: Path) -> None:
    _legacy_show(tmp_path)
    assert read_layout(tmp_path) == 1


@pytest.mark.parametrize("subdirs", [("next",), ("objects",), ("trees",), ("next", "objects")])
def test_read_layout_refuses_folders_without_a_root_link(
    tmp_path: Path, subdirs: tuple[str, ...]
) -> None:
    for name in subdirs:
        (tmp_path / "s" / name).mkdir(parents=True)
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_finds_a_layout_1_show_among_other_entries(tmp_path: Path) -> None:
    (tmp_path / "stray.txt").write_text("x")
    (tmp_path / "empty-folder").mkdir()
    _legacy_show(tmp_path, "s")
    assert read_layout(tmp_path) == 1


def test_read_layout_reads_a_root_that_holds_both_forms_as_the_file(tmp_path: Path) -> None:
    _legacy_show(tmp_path)
    _write_layout(tmp_path, _layout_json(2))
    assert read_layout(tmp_path) == 2


def test_read_layout_follows_a_symlinked_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _legacy_show(real)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    assert read_layout(tmp_path / "link") == 1


def test_read_layout_maps_a_too_long_name_to_io(tmp_path: Path) -> None:
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path / ("a" * 300))
    assert caught.value.code == ErrorCode.IO


CANNOT_CHMOD = sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0)


@pytest.mark.skipif(CANNOT_CHMOD, reason="needs permission checks that bind this user")
def test_read_layout_maps_an_unreadable_root_to_io(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "x").write_text("x")
    root.chmod(0)
    try:
        with pytest.raises(StorageError) as caught:
            read_layout(root)
    finally:
        root.chmod(0o700)
    assert caught.value.code == ErrorCode.IO


@pytest.mark.skipif(CANNOT_CHMOD, reason="needs permission checks that bind this user")
def test_read_layout_maps_an_unreadable_layout_file_to_io(tmp_path: Path) -> None:
    path = _write_layout(tmp_path, _layout_json(2))
    path.chmod(0)
    try:
        with pytest.raises(StorageError) as caught:
            read_layout(tmp_path)
    finally:
        path.chmod(0o600)
    assert caught.value.code == ErrorCode.IO


def test_regression_root_holding_only_ds_store_is_empty(tmp_path: Path) -> None:
    """Reads a root that holds only Finder's .DS_Store as an empty root, not as corrupt."""
    (tmp_path / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    assert read_layout(tmp_path) == CURRENT_LAYOUT


@pytest.mark.skipif(CANNOT_CHMOD, reason="needs permission checks that bind this user")
def test_regression_unreadable_lost_and_found_is_skipped(tmp_path: Path) -> None:
    """Reads past a lost+found folder it cannot open, alone or beside a layout-1 show."""
    lost = tmp_path / "lost+found"
    lost.mkdir()
    (lost / "next").mkdir()
    lost.chmod(0)
    try:
        assert read_layout(tmp_path) == CURRENT_LAYOUT
        _legacy_show(tmp_path)
        assert read_layout(tmp_path) == 1
    finally:
        lost.chmod(0o700)


def test_read_layout_skips_housekeeping_names_only(tmp_path: Path) -> None:
    for name in sorted(HOUSEKEEPING_NAMES):
        (tmp_path / name).mkdir()
    assert read_layout(tmp_path) == CURRENT_LAYOUT
    (tmp_path / ".DS_Store_old").write_text("x")
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_reads_a_root_holding_only_tmp_as_current(tmp_path: Path) -> None:
    (tmp_path / TEMP_DIR).mkdir()
    (tmp_path / TEMP_DIR / ".tmp-left").write_bytes(b"x")
    assert read_layout(tmp_path) == CURRENT_LAYOUT
    (tmp_path / TEMP_DIR / ".tmp-left").unlink()
    (tmp_path / TEMP_DIR).rmdir()
    (tmp_path / TEMP_DIR).write_bytes(b"not a folder")
    with pytest.raises(StorageError) as caught:
        read_layout(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT


def test_read_layout_reads_a_layout_file_linked_while_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reads a layout file that a racing first commit linked after its name was looked up."""
    _write_layout(tmp_path, _layout_json(3))
    (tmp_path / TEMP_DIR).mkdir()
    real_lstat = Path.lstat
    looked_up: list[Path] = []

    def not_yet_linked(path: Path) -> os.stat_result:
        if path.name == LAYOUT_FILE and not looked_up:
            looked_up.append(path)
            raise FileNotFoundError(path)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", not_yet_linked)
    with pytest.raises(VersionError):
        read_layout(tmp_path)
