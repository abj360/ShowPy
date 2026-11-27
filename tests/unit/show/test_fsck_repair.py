#!/usr/bin/env python3

"""
test_fsck_repair.py --- tests that fsck repair removes only what a crashed writer left torn

Contains:
    test_fsck_repair_removes_only_a_torn_last_slot: removes the torn last slot of a damaged root
        and nothing else, then reports what is left
    test_torn_slot_hint_names_a_repair_that_works: runs the call the TORN_SLOT hint names, after
        which the show reads and commits again
    test_fsck_repair_needs_confirm_and_at: refuses a repair without confirm=True or `at`
    test_fsck_refuses_confirm_without_repair: refuses confirm=True without repair=True
    test_fsck_repair_keeps_a_torn_slot_a_writer_may_still_fill: keeps a torn slot younger than a
        day before `at`
    test_fsck_repair_never_removes_a_torn_slot_that_is_not_last: keeps a torn slot before another
    test_fsck_repair_keeps_a_torn_last_slot_after_a_torn_slot: removes neither of two torn slots
    test_fsck_repair_removes_a_torn_layout_file_left_by_a_first_commit: clears a root a crashed
        first commit left, which then takes a commit
    test_fsck_repair_keeps_a_torn_layout_file_once_shows_exist: keeps it once a show folder exists
    test_fsck_repair_removes_a_torn_manifest_left_by_a_first_commit: clears a show folder with no
        slot, which then takes a commit
    test_fsck_repair_keeps_a_torn_manifest_whose_show_has_slots: keeps the manifest of a show
        with slots
    test_regression_repair_keeps_a_file_replaced_after_its_check: keeps a torn file another
        writer replaced between the repair's checks and its removal
    test_fsck_repair_keeps_a_torn_file_something_relies_on_after_the_check: keeps a torn slot once
        a later slot exists, and a torn manifest once its show has a slot
    test_repair_rechecks_each_file_just_before_removing_it: keeps a file that finished, grew,
        vanished, is no file or is no longer safe to remove
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import fsck as fsck_module
from e8s.show import fsck_scan
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.fsck import fsck, removed_torn_files
from e8s.show.fsck_findings import Repairable
from e8s.show.storage_models import FsckReport
from e8s.show.torn_reads import is_short_of
from tests.fixtures.fsck_repos import (
    AT,
    DAY,
    OTHER_ID,
    SHOW_ID,
    age_by,
    blob_path,
    landed_revisions,
    numbered_change,
    relative,
    show_path,
    slot_path,
    snapshot,
)


@pytest.fixture(autouse=True)
def _no_reread_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def _repair(root: Path, *, at: datetime = AT) -> FsckReport:
    return fsck(root, repair=True, confirm=True, at=at)


def _torn_last_slot(root: Path, landed: int = 2) -> Path:
    landed_revisions(root, landed)
    slot = slot_path(root, landed + 1)
    slot.write_bytes(b'{"at":"2026')
    age_by(slot, 2 * DAY)
    return slot


def test_fsck_repair_removes_only_a_torn_last_slot(tmp_path: Path) -> None:
    slot = _torn_last_slot(tmp_path, 3)
    blob_path(tmp_path, b"render 0").unlink()
    slot_path(tmp_path, 2).unlink()
    (tmp_path / "stray.txt").write_bytes(b"?")
    stale = tmp_path / "tmp" / ".tmp-old"
    stale.write_bytes(b"left")
    age_by(stale, 2 * DAY)
    before = snapshot(tmp_path)
    found = fsck(tmp_path, at=AT)
    report = _repair(tmp_path)
    after = snapshot(tmp_path)
    assert set(before) - set(after) == {relative(tmp_path, slot)}
    files = [path for path in after if (tmp_path / path).is_file()]
    assert {path: before[path] for path in files} == {path: after[path] for path in files}
    assert report.repaired == (relative(tmp_path, slot),)
    assert [f for f in found.findings if f.code != "TORN_SLOT"] == list(report.findings)
    assert not report.ok


def test_torn_slot_hint_names_a_repair_that_works(tmp_path: Path) -> None:
    landed = landed_revisions(tmp_path, 2)
    slot = slot_path(tmp_path, 3)
    slot.write_bytes(b"")
    age_by(slot, 2 * DAY)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == ErrorCode.TORN_SLOT
    assert "fsck(root, repair=True, confirm=True, at=" in caught.value.hint
    report = _repair(tmp_path)
    assert report.ok
    assert report.findings == ()
    assert report.repaired == (relative(tmp_path, slot),)
    assert FileBackend(tmp_path).log(SHOW_ID) == landed
    revision, tree, blobs = numbered_change(landed[-1].id, 2)
    FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert fsck(tmp_path).ok


@pytest.mark.parametrize(
    ("confirm", "at"), [(False, AT), (True, None), (False, None)], ids=["confirm", "at", "both"]
)
def test_fsck_repair_needs_confirm_and_at(
    tmp_path: Path, *, confirm: bool, at: datetime | None
) -> None:
    _torn_last_slot(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(StorageError) as caught:
        fsck(tmp_path, repair=True, confirm=confirm, at=at)
    assert caught.value.code == ErrorCode.NOT_CONFIRMED
    assert "confirm=True" in caught.value.hint
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("at", [AT, None], ids=["at", "no_at"])
def test_fsck_refuses_confirm_without_repair(tmp_path: Path, at: datetime | None) -> None:
    """confirm=True alone reads like a repair request; it repairs nothing, so it is refused."""
    _torn_last_slot(tmp_path)
    before = snapshot(tmp_path)
    with pytest.raises(StorageError) as caught:
        fsck(tmp_path, confirm=True, at=at)
    assert caught.value.code == ErrorCode.NOT_CONFIRMED
    assert "repair=True" in caught.value.hint
    assert snapshot(tmp_path) == before


def test_fsck_repair_keeps_a_torn_slot_a_writer_may_still_fill(tmp_path: Path) -> None:
    """A paused writer's slot is torn too; repair waits until it is a day older than `at`."""
    slot = _torn_last_slot(tmp_path)
    age_by(slot, DAY - timedelta(seconds=1))
    report = _repair(tmp_path)
    assert report.repaired == ()
    assert [f.code for f in report.findings] == ["TORN_SLOT"]
    assert slot.exists()
    age_by(slot, DAY + timedelta(seconds=1))
    assert _repair(tmp_path).repaired == (relative(tmp_path, slot),)


def test_fsck_repair_never_removes_a_torn_slot_that_is_not_last(tmp_path: Path) -> None:
    slot = _torn_last_slot(tmp_path)
    slot_path(tmp_path, 4).write_bytes(slot_path(tmp_path, 2).read_bytes())
    before = snapshot(tmp_path)
    report = _repair(tmp_path)
    assert report.repaired == ()
    assert ("TORN_SLOT", relative(tmp_path, slot)) in [(f.code, f.path) for f in report.findings]
    assert snapshot(tmp_path) == before


def test_fsck_repair_keeps_a_torn_last_slot_after_a_torn_slot(tmp_path: Path) -> None:
    """Removing the last of two torn slots would make the earlier one a torn last slot."""
    first = _torn_last_slot(tmp_path)
    second = slot_path(tmp_path, 4)
    second.write_bytes(b"")
    age_by(second, 2 * DAY)
    before = snapshot(tmp_path)
    report = _repair(tmp_path)
    assert report.repaired == ()
    assert [(f.code, f.path) for f in report.findings] == [
        ("TORN_SLOT", relative(tmp_path, first)),
        ("TORN_SLOT", relative(tmp_path, second)),
    ]
    assert snapshot(tmp_path) == before


def test_fsck_repair_removes_a_torn_layout_file_left_by_a_first_commit(tmp_path: Path) -> None:
    (tmp_path / "tmp").mkdir()
    layout = tmp_path / "layout.json"
    layout.write_bytes(b"")
    age_by(layout, 2 * DAY)
    report = _repair(tmp_path)
    assert report.repaired == ("layout.json",)
    assert report.ok
    landed_revisions(tmp_path, 1)
    assert fsck(tmp_path).ok


def test_fsck_repair_keeps_a_torn_layout_file_once_shows_exist(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 1)
    layout = tmp_path / "layout.json"
    layout.write_bytes(b'{\n  "format"')
    age_by(layout, 2 * DAY)
    report = _repair(tmp_path)
    assert report.repaired == ()
    assert ("TORN_FILE", "layout.json") in [(f.code, f.path) for f in report.findings]
    assert layout.read_bytes() == b'{\n  "format"'


@pytest.mark.parametrize(
    "made", [("log", "records", "objects"), ("records", "objects")], ids=["all", "no_log"]
)
def test_fsck_repair_removes_a_torn_manifest_left_by_a_first_commit(
    tmp_path: Path, made: tuple[str, ...]
) -> None:
    landed_revisions(tmp_path, 1)
    folder = show_path(tmp_path, OTHER_ID)
    for name in made:
        (folder / name).mkdir(parents=True)
    manifest = folder / "show.json"
    manifest.write_bytes(b'{\n  "id": "other_sh')
    age_by(manifest, 2 * DAY)
    report = _repair(tmp_path)
    assert report.repaired == (relative(tmp_path, manifest),)
    assert report.ok
    landed_revisions(tmp_path, 2, OTHER_ID)
    assert fsck(tmp_path).ok
    assert FileBackend(tmp_path).shows() == [SHOW_ID, OTHER_ID]


def test_fsck_repair_keeps_a_torn_manifest_whose_show_has_slots(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 2)
    manifest = show_path(tmp_path) / "show.json"
    manifest.write_bytes(b"")
    age_by(manifest, 2 * DAY)
    report = _repair(tmp_path)
    assert report.repaired == ()
    assert ("TORN_FILE", relative(tmp_path, manifest)) in [
        (f.code, f.path) for f in report.findings
    ]
    assert manifest.exists()


def test_repair_rechecks_each_file_just_before_removing_it(tmp_path: Path) -> None:
    """A file that changed, grew, vanished, is no longer safe or is a link since the check stays."""
    torn, cutoff = tmp_path / "torn", AT - DAY
    is_short = is_short_of(b"complete")

    def item(path: Path, *, is_safe: bool = True) -> Repairable:
        return Repairable(path, 64, is_short, lambda: is_safe)

    for name, data in (("torn", b"comp"), ("finished", b"complete"), ("unsafe", b"co")):
        (tmp_path / name).write_bytes(data)
        age_by(tmp_path / name, 2 * DAY)
    (tmp_path / "too_big").write_bytes(b"c" * 65)
    age_by(tmp_path / "too_big", 2 * DAY)
    items = [
        item(torn),
        item(tmp_path / "finished"),
        item(tmp_path / "unsafe", is_safe=False),
        item(tmp_path / "too_big"),
        item(tmp_path / "gone"),
        item(tmp_path),
    ]
    assert removed_torn_files(tmp_path, items, cutoff) == ["torn"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["finished", "too_big", "unsafe"]


def test_regression_repair_keeps_a_file_replaced_after_its_check(tmp_path: Path) -> None:
    """After the last check another writer puts a new file, as torn and as old, in its place."""
    torn = tmp_path / "torn"
    torn.write_bytes(b"comp")
    age_by(torn, 2 * DAY)

    def replaced() -> bool:
        torn.unlink()
        torn.write_bytes(b"co")
        age_by(torn, 2 * DAY)
        return True

    item = Repairable(torn, 64, is_short_of(b"complete"), replaced)
    assert removed_torn_files(tmp_path, [item], AT - DAY) == []
    assert torn.read_bytes() == b"co"


def _slot_after(root: Path) -> None:
    slot_path(root, 4).write_bytes(slot_path(root, 2).read_bytes())


def _first_slot_of_other(root: Path) -> None:
    log = show_path(root, OTHER_ID) / "log"
    log.mkdir(exist_ok=True)
    (log / f"{1:0{SLOT_DIGITS}d}").write_bytes(slot_path(root, 1).read_bytes())


@pytest.mark.parametrize("change", [_slot_after, _first_slot_of_other], ids=["slot", "manifest"])
def test_fsck_repair_keeps_a_torn_file_something_relies_on_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], None]
) -> None:
    slot = _torn_last_slot(tmp_path)
    manifest = show_path(tmp_path, OTHER_ID) / "show.json"
    manifest.parent.mkdir()
    manifest.write_bytes(b"")
    age_by(manifest, 2 * DAY)
    scan = fsck_scan.scan_repository

    def scan_then_change(
        root: Path, show_id: str | None, at: datetime | None
    ) -> fsck_scan.RepositoryScan:
        found = scan(root, show_id, at)
        change(root)
        return found

    monkeypatch.setattr(fsck_module, "scan_repository", scan_then_change)
    kept = slot if change is _slot_after else manifest
    removed = manifest if change is _slot_after else slot
    report = _repair(tmp_path)
    assert report.repaired == (relative(tmp_path, removed),)
    assert kept.exists()
