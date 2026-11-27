#!/usr/bin/env python3

"""
test_fsck_inputs.py --- tests fsck on the roots, show IDs, times, links and FIFOs it is given

Contains:
    test_fsck_of_a_missing_root_is_an_io_error: never reports a mistyped root clean
    test_fsck_of_a_layout_1_root_is_a_version_error: raises OLD_LAYOUT with the upgrade call and
        changes nothing
    test_fsck_of_a_folder_that_is_no_repository_is_corrupt: raises what reading its layout raises
    test_fsck_never_follows_a_link_under_the_root: reports a symlinked folder without reading
        through it
    test_fsck_reports_a_linked_show_folder: reports a symlinked show folder, whole root or one show
    test_fsck_follows_a_symlinked_root: checks a repository through a symlinked root
    test_fsck_of_one_show_checks_only_that_show: leaves other shows out and refuses an unknown one
    test_regression_fsck_of_a_show_id_no_show_can_have_is_an_unknown_ref: refuses a show ID
        that cannot be encoded with a teaching error, never a raw UnicodeEncodeError
    test_fsck_reports_a_fifo_in_place_of_a_stored_file: reports a FIFO as a blob, record, slot,
        manifest or temp file, or refuses it as the layout file, without blocking on it
    test_fsck_refuses_a_time_without_a_zone: refuses a naive `at`
"""

import json
import os
import signal
import sys
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, UnknownRefError, VersionError
from e8s.show.fsck import fsck
from e8s.show.history_models import Revision
from tests.fixtures.fsck_repos import (
    AT,
    OTHER_ID,
    SHOW_ID,
    blob_path,
    codes,
    landed_revisions,
    record_path,
    relative,
    show_path,
    slot_path,
    snapshot,
)
from tests.fixtures.layout1 import stored_layout1

FIFO_TIMEOUT_S = 10
SKIP_LINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")


pytestmark = pytest.mark.usefixtures("no_reread_pauses")


def test_fsck_of_a_missing_root_is_an_io_error(tmp_path: Path) -> None:
    with pytest.raises(StorageError) as caught:
        fsck(tmp_path / "typo")
    assert caught.value.code == ErrorCode.IO
    assert not (tmp_path / "typo").exists()


def test_fsck_of_a_layout_1_root_is_a_version_error(tmp_path: Path) -> None:
    stored_layout1(tmp_path, "old", {"/renders/a.txt": b"x"}, at=AT)
    before = snapshot(tmp_path)
    with pytest.raises(VersionError) as caught:
        fsck(tmp_path)
    assert caught.value.context["layout"] == 1
    assert caught.value.code == ErrorCode.OLD_LAYOUT
    assert "upgrade_repository(Path(" in caught.value.hint
    assert snapshot(tmp_path) == before


def test_fsck_of_a_folder_that_is_no_repository_is_corrupt(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine")
    with pytest.raises(StorageError) as caught:
        fsck(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT
    (tmp_path / "notes.txt").unlink()
    (tmp_path / "layout.json").write_text('{"format": "e8.repository", "layout": 3}')
    with pytest.raises(VersionError):
        fsck(tmp_path)


@SKIP_LINKS
@pytest.mark.parametrize("name", ["log", "records", "objects"])
def test_fsck_never_follows_a_link_under_the_root(tmp_path: Path, name: str) -> None:
    root, outside = tmp_path / "repo", tmp_path / "outside"
    landed_revisions(root, 2)
    folder = show_path(root) / name
    folder.rename(outside)
    folder.symlink_to(outside, target_is_directory=True)
    before = snapshot(outside)
    report = fsck(root)
    assert ("UNKNOWN_FILE", relative(root, folder)) in [(f.code, f.path) for f in report.findings]
    assert not any(f.code == "UNREACHABLE" for f in report.findings)
    assert not report.ok
    assert snapshot(outside) == before
    shows = root / "shows"
    shows.rename(tmp_path / "shows_elsewhere")
    shows.symlink_to(tmp_path / "shows_elsewhere", target_is_directory=True)
    assert codes(root) == [("UNKNOWN_FILE", "shows")]


@SKIP_LINKS
def test_fsck_reports_a_linked_show_folder(tmp_path: Path) -> None:
    root, outside = tmp_path / "repo", tmp_path / "outside"
    landed_revisions(root, 1)
    folder = show_path(root)
    folder.rename(outside)
    folder.symlink_to(outside, target_is_directory=True)
    expected = [("UNKNOWN_FILE", relative(root, folder))]
    assert codes(root) == expected
    assert [(f.code, f.path) for f in fsck(root, SHOW_ID).findings] == expected


@SKIP_LINKS
def test_fsck_follows_a_symlinked_root(tmp_path: Path) -> None:
    real, linked = tmp_path / "real", tmp_path / "linked"
    landed_revisions(real, 2)
    linked.symlink_to(real, target_is_directory=True)
    assert fsck(linked).ok
    blob_path(real, b"render 1").unlink()
    assert codes(linked) == [("MISSING_BLOB", relative(real, blob_path(real, b"render 1")))]


def test_fsck_of_one_show_checks_only_that_show(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 2)
    landed_revisions(tmp_path, 1, OTHER_ID)
    blob_path(tmp_path, b"render 1").unlink()
    (tmp_path / "stray.txt").write_bytes(b"?")
    assert fsck(tmp_path, OTHER_ID).findings == ()
    assert [f.code for f in fsck(tmp_path, SHOW_ID).findings] == ["MISSING_BLOB"]
    with pytest.raises(UnknownRefError) as caught:
        fsck(tmp_path, "no_such_show")
    assert caught.value.context["show"] == "no_such_show"


@pytest.mark.parametrize("show_id", ["\ud800", "", "../x"], ids=["surrogate", "empty", "dots"])
def test_regression_fsck_of_a_show_id_no_show_can_have_is_an_unknown_ref(
    tmp_path: Path, show_id: str
) -> None:
    landed_revisions(tmp_path, 1)
    with pytest.raises(UnknownRefError) as caught:
        fsck(tmp_path, show_id)
    assert json.loads(json.dumps(caught.value.to_dict()))["code"] == "UNKNOWN_REF"
    assert "fsck(root)" in caught.value.hint


def _fifo_places(root: Path, landed: list[Revision]) -> dict[str, tuple[Path, str | None]]:
    return {
        "blob": (blob_path(root, b"render 1"), "BAD_BLOB"),
        "record": (record_path(root, landed[1]), "BAD_RECORD"),
        "slot": (slot_path(root, 2), "BAD_RECORD"),
        "manifest": (show_path(root) / "show.json", "BAD_RECORD"),
        "temp": (root / "tmp" / ".tmp-fifo", "UNKNOWN_FILE"),
        "layout": (root / "layout.json", None),
    }


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no FIFOs")
@pytest.mark.parametrize("place", ["blob", "record", "slot", "manifest", "temp", "layout"])
def test_fsck_reports_a_fifo_in_place_of_a_stored_file(tmp_path: Path, place: str) -> None:
    """A FIFO opened for reading blocks until a writer comes; fsck must never wait on one."""
    landed = landed_revisions(tmp_path, 2)
    path, code = _fifo_places(tmp_path, landed)[place]
    path.unlink(missing_ok=True)
    os.mkfifo(path)

    def blocked(_signal: int, _frame: object) -> None:
        raise TimeoutError(f"fsck blocked on the FIFO at {place}")

    previous = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(FIFO_TIMEOUT_S)
    try:
        if code is None:
            with pytest.raises(StorageError):
                fsck(tmp_path)
        else:
            report = fsck(tmp_path)
            assert (code, relative(tmp_path, path)) in [(f.code, f.path) for f in report.findings]
            assert not report.ok
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_fsck_refuses_a_time_without_a_zone(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        fsck(tmp_path, at=AT.replace(tzinfo=None))
