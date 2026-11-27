#!/usr/bin/env python3

"""
test_gc.py --- tests gc on layout-2 repositories holding unreachable files and stale temp files

Contains:
    test_gc_without_confirm_removes_nothing: reports what it would remove and changes nothing
    test_gc_removes_only_unreachable_files: removes the lost commit's blob, a stray record and a
        stale temp file, after which fsck is clean and every revision still loads
    test_gc_is_idempotent: removes nothing on a second run
    test_gc_keeps_files_younger_than_a_day: keeps unreachable and temp files a running writer
        may still need
    test_regression_gc_keeps_the_files_of_a_commit_in_flight: keeps the record and blobs a commit
        wrote before its slot lands, and the commit lands
    test_gc_leaves_a_damaged_show_untouched: skips a show with a failing finding
    test_gc_leaves_every_show_when_the_layout_file_is_torn: skips every show of a root whose
        layout file is torn
    test_gc_skips_a_show_whose_head_moved: removes nothing from a show that landed a slot while gc
        ran
    test_gc_keeps_a_file_that_changed_after_the_check: keeps a file a writer touched or replaced
    test_regression_gc_never_trusts_an_inode_of_zero: tells a replaced or retimed file from the
        listed one by size and time where the file system reports inode 0
    test_gc_never_takes_a_file_with_inode_0_for_another_unless_all_else_matches: keeps a file
        when only one of listing and lookup reports inode 0, or both do and device or ctime differ
    test_regression_gc_keeps_a_file_replaced_with_a_reused_inode: keeps a file replaced by one
        the file system gave the freed inode, which differs in size, mtime or ctime only
    test_regression_gc_keeps_a_file_replaced_with_a_reused_inode_as_it_moves: keeps such a file
        replaced between gc's last check of its name and its move aside
    test_regression_gc_removes_files_a_listing_reports_without_inodes: removes unreachable and
        stale temp files where folder listings, as on Windows, report device and inode 0
    test_gc_never_follows_a_link: keeps old temp files behind a symlinked tmp folder
    test_gc_refuses_layout_1_and_a_time_without_a_zone: raises VersionError and SchemaError
"""

import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.core.errors import ConflictError, SchemaError, VersionError
from e8s.examples import two_set_show
from e8s.show import durable_io, durable_reads, fsck_scan
from e8s.show import gc as gc_module
from e8s.show.backend import digest
from e8s.show.document_repository import DocumentRepository, revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.fsck_findings import SET_ASIDE_PREFIX, Leftover
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.layout1 import _FileBackend
from e8s.show.show_folders import show_folder
from tests.fixtures.fsck_repos import list_like_windows, with_stat, with_zero_inode

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "show_1"
OTHER_ID = "other_show"
LOST_PATH = "/renders/lost.txt"
TITLES = [two_set_show().title, "second"]
MOVED = "a slot landed while gc ran; run gc again"
DAY = timedelta(days=1)
OLD = 2 * DAY
SKIP_LINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")


def _age(path: Path, by: timedelta, at: datetime = AT) -> None:
    then = (at - by).timestamp()
    os.utime(path, (then, then))


def _folder(root: Path, show_id: str = SHOW_ID) -> Path:
    return root / "shows" / show_folder(show_id)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        _relative(root, path): (
            path.read_bytes() if path.is_file() else b"",
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }


def _repository(root: Path) -> tuple[DocumentRepository, list[Revision]]:
    repo = DocumentRepository(FileBackend(root))
    first = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    second = repo.put_show(
        SHOW_ID, two_set_show().replace(title=TITLES[1]), expected_revision=first.id, at=AT
    )
    return repo, [first, second]


def _lost_commit(root: Path, parent: Revision) -> Path:
    """Commits on a parent that is no longer the head; the loser's blob stays, unreachable."""
    backend = FileBackend(root)
    data = b"lost render"
    tree = {**backend.tree(SHOW_ID, parent.id), LOST_PATH: digest(data)}
    changes = {LOST_PATH: digest(data)}
    revision = Revision(
        id=revision_id(parent.id, AT, changes), parent=parent.id, at=AT, changed=(LOST_PATH,)
    )
    with pytest.raises(ConflictError):
        backend.commit(SHOW_ID, revision, tree, {digest(data): data})
    return _folder(root) / "objects" / digest(data)


def _garbage(root: Path, landed: list[Revision]) -> list[Path]:
    """Leaves a lost commit's blob, a stray record and a stale temp file, each two days old."""
    lost = _lost_commit(root, landed[0])
    stray = _folder(root) / "records" / f"{'f' * 64}.json"
    stray.write_bytes(b"{}")
    temp = root / "tmp" / ".tmp-crashed"
    temp.write_bytes(b"left behind")
    garbage = [lost, stray, temp]
    for path in garbage:
        _age(path, OLD)
    return garbage


def _replace(path: Path) -> None:
    """Replaces a file with a new one under the same name, as old as the one it replaces."""
    path.unlink()
    path.write_bytes(b"new")
    _age(path, OLD)


def _every_revision_loads(root: Path, landed: list[Revision], titles: list[str]) -> None:
    repo = DocumentRepository(FileBackend(root))
    assert repo.revisions(SHOW_ID) == landed
    assert [repo.get_show(SHOW_ID, revision.id).title for revision in landed] == titles


def test_gc_without_confirm_removes_nothing(tmp_path: Path) -> None:
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    before = _snapshot(tmp_path)
    report = gc(tmp_path, at=AT)
    assert report.dry_run
    assert report.removed == tuple(sorted(_relative(tmp_path, path) for path in garbage))
    assert report.bytes_freed == sum(path.stat().st_size for path in garbage)
    assert report.skipped == ()
    assert _snapshot(tmp_path) == before


def test_gc_removes_only_unreachable_files(tmp_path: Path) -> None:
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "old").write_bytes(b"lane m")
    _age(tmp_path / "assets" / "old", OLD)
    before = _snapshot(tmp_path)
    report = gc(tmp_path, at=AT, confirm=True)
    after = _snapshot(tmp_path)
    expected = {_relative(tmp_path, path) for path in garbage}
    assert not report.dry_run
    assert set(report.removed) == expected
    assert set(before) - set(after) == expected
    files = [path for path in after if (tmp_path / path).is_file()]
    assert {path: before[path] for path in files} == {path: after[path] for path in files}
    assert report.bytes_freed == sum(before[path][0].__len__() for path in expected)
    clean = fsck(tmp_path, at=AT)
    assert clean.ok
    assert clean.findings == ()
    _every_revision_loads(tmp_path, landed, TITLES)


def test_gc_is_idempotent(tmp_path: Path) -> None:
    _, landed = _repository(tmp_path)
    _garbage(tmp_path, landed)
    assert gc(tmp_path, at=AT, confirm=True).removed
    before = _snapshot(tmp_path)
    again = gc(tmp_path, at=AT, confirm=True)
    assert again.removed == ()
    assert again.bytes_freed == 0
    assert _snapshot(tmp_path) == before


def test_gc_keeps_files_younger_than_a_day(tmp_path: Path) -> None:
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    for path in garbage:
        _age(path, DAY - timedelta(seconds=1))
    before = _snapshot(tmp_path)
    assert gc(tmp_path, at=AT).removed == ()
    assert gc(tmp_path, at=AT, confirm=True).removed == ()
    assert _snapshot(tmp_path) == before
    for path in garbage:
        _age(path, DAY + timedelta(seconds=1))
    assert len(gc(tmp_path, at=AT, confirm=True).removed) == len(garbage)


def test_regression_gc_keeps_the_files_of_a_commit_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gc runs after a commit wrote its record and blobs and before it claims its slot."""
    repo, landed = _repository(tmp_path)
    claim = FileBackend._claim_slot
    reports = []

    def claim_after_gc(backend: FileBackend, show_id: str, revision: Revision, seq: int) -> None:
        record = _folder(tmp_path) / "records" / f"{revision.id}.json"
        written_at = datetime.fromtimestamp(record.stat().st_mtime, UTC)
        reports.append(gc(tmp_path, at=written_at, confirm=True))
        claim(backend, show_id, revision, seq)

    monkeypatch.setattr(FileBackend, "_claim_slot", claim_after_gc)
    third = repo.put_show(
        SHOW_ID, two_set_show().replace(title="third"), expected_revision=landed[1].id, at=AT
    )
    monkeypatch.undo()
    assert [report.removed for report in reports] == [()]
    assert fsck(tmp_path).findings == ()
    _every_revision_loads(tmp_path, [*landed, third], [*TITLES, "third"])


def test_gc_leaves_a_damaged_show_untouched(tmp_path: Path) -> None:
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    tree = FileBackend(tmp_path).tree(SHOW_ID, landed[0].id)
    (_folder(tmp_path) / "objects" / min(tree.values())).unlink()
    report = gc(tmp_path, at=AT, confirm=True)
    assert report.skipped == (
        (_relative(tmp_path, _folder(tmp_path)), "fsck finds MISSING_BLOB; run fsck(root)"),
    )
    assert report.removed == ("tmp/.tmp-crashed",)
    assert all(path.exists() for path in garbage[:-1])


def test_gc_leaves_every_show_when_the_layout_file_is_torn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    (tmp_path / "layout.json").write_bytes(b'{\n  "format"')
    report = gc(tmp_path, at=AT, confirm=True)
    assert report.skipped == (
        (_relative(tmp_path, _folder(tmp_path)), "layout.json is torn; run fsck(root)"),
    )
    assert report.removed == ("tmp/.tmp-crashed",)
    assert all(path.exists() for path in garbage[:-1])


def test_gc_skips_a_show_whose_head_moved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    scan = fsck_scan.scan_repository

    def scan_then_commit(root: Path, show_id: str | None, at: datetime | None) -> object:
        found = scan(root, show_id, at)
        repo.put_show(
            SHOW_ID,
            two_set_show().replace(title="later"),
            expected_revision=landed[1].id,
            at=AT - 3 * DAY,
        )
        return found

    monkeypatch.setattr(gc_module, "scan_repository", scan_then_commit)
    report = gc(tmp_path, at=AT, confirm=True)
    assert report.skipped == ((_relative(tmp_path, _folder(tmp_path)), MOVED),)
    assert report.removed == ("tmp/.tmp-crashed",)
    assert all(path.exists() for path in garbage[:-1])


@pytest.mark.parametrize(
    "change",
    [
        lambda path: _age(path, timedelta(0)),
        lambda path: _replace(path),
        lambda path: path.unlink(),
    ],
    ids=["touched", "replaced", "removed"],
)
def test_gc_keeps_a_file_that_changed_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], object]
) -> None:
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    stray = garbage[-2]
    scan = fsck_scan.scan_repository

    def scan_then_change(root: Path, show_id: str | None, at: datetime | None) -> object:
        found = scan(root, show_id, at)
        change(stray)
        return found

    monkeypatch.setattr(gc_module, "scan_repository", scan_then_change)
    report = gc(tmp_path, at=AT, confirm=True)
    assert _relative(tmp_path, stray) not in report.removed
    assert len(report.removed) == len(garbage) - 1


@pytest.mark.parametrize(
    "change", [_replace, lambda path: _age(path, OLD + DAY)], ids=["replaced", "retimed"]
)
def test_regression_gc_never_trusts_an_inode_of_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], object]
) -> None:
    """Where the file system reports inode 0, size and time tell a replaced file apart."""
    stray = tmp_path / "stray"
    stray.write_bytes(b"{}")
    _age(stray, OLD)
    listed = Leftover(stray, with_zero_inode(os.lstat(stray)))
    lookup = durable_reads.lstat_info

    def zero_inode(show_id: str, path: Path) -> os.stat_result | None:
        info = lookup(show_id, path)
        return None if info is None else with_zero_inode(info)

    monkeypatch.setattr(gc_module, "lstat_info", zero_inode)
    cutoff = AT - DAY
    assert gc_module._is_unchanged(stray, listed, cutoff) is True
    change(stray)
    assert gc_module._is_unchanged(stray, listed, cutoff) is False


def _with_other_ctime(info: os.stat_result) -> os.stat_result:
    return with_stat(with_zero_inode(info), st_ctime_ns=info.st_ctime_ns + 1)


def _with_other_device(info: os.stat_result) -> os.stat_result:
    return with_stat(with_zero_inode(info), st_dev=info.st_dev + 1)


@pytest.mark.parametrize(
    ("listing", "lookup"),
    [
        (with_zero_inode, lambda info: info),
        (lambda info: info, with_zero_inode),
        (with_zero_inode, _with_other_ctime),
        (with_zero_inode, _with_other_device),
    ],
    ids=["listing_zero", "lookup_zero", "both_zero_other_ctime", "both_zero_other_device"],
)
def test_gc_never_takes_a_file_with_inode_0_for_another_unless_all_else_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    listing: Callable[[os.stat_result], os.stat_result],
    lookup: Callable[[os.stat_result], os.stat_result],
) -> None:
    """An inode of 0 on one side only never matches; on both, device, size and times must."""
    stray = tmp_path / "stray"
    stray.write_bytes(b"{}")
    _age(stray, OLD)
    listed = Leftover(stray, listing(os.lstat(stray)))
    real = durable_reads.lstat_info

    def looked_up(show_id: str, path: Path) -> os.stat_result | None:
        info = real(show_id, path)
        return None if info is None else lookup(info)

    monkeypatch.setattr(gc_module, "lstat_info", looked_up)
    assert gc_module._is_unchanged(stray, listed, AT - DAY) is False


def _as_written(_listed: os.stat_result) -> dict[str, int]:
    return {}


def _only_ctime_differs(listed: os.stat_result) -> dict[str, int]:
    return {
        "st_size": listed.st_size,
        "st_mtime_ns": listed.st_mtime_ns,
        "st_ctime_ns": listed.st_ctime_ns + 1,
    }


def _only_size_differs(listed: os.stat_result) -> dict[str, int]:
    return {
        "st_size": listed.st_size + 1,
        "st_mtime_ns": listed.st_mtime_ns,
        "st_ctime_ns": listed.st_ctime_ns,
    }


def _only_mtime_differs(listed: os.stat_result) -> dict[str, int]:
    return {
        "st_size": listed.st_size,
        "st_mtime_ns": listed.st_mtime_ns + 1,
        "st_ctime_ns": listed.st_ctime_ns,
    }


def _replace_reusing_inode(
    monkeypatch: pytest.MonkeyPatch,
    stray: Path,
    fields: Callable[[os.stat_result], dict[str, int]],
) -> Callable[[], None]:
    """Returns a replace of `stray` whose new file gc sees with the listed device and inode."""
    names = {stray, stray.with_name(SET_ASIDE_PREFIX + stray.name)}
    listed = os.lstat(stray)
    lookup = durable_reads.lstat_info
    replaced: list[bool] = []

    def reused_inode(show_id: str, path: Path) -> os.stat_result | None:
        info = lookup(show_id, path)
        if info is None or path not in names or not replaced:
            return info
        return with_stat(info, st_dev=listed.st_dev, st_ino=listed.st_ino, **fields(listed))

    def replace() -> None:
        _replace(stray)
        replaced.append(True)

    monkeypatch.setattr(gc_module, "lstat_info", reused_inode)
    return replace


@pytest.mark.parametrize(
    "fields",
    [_as_written, _only_ctime_differs, _only_size_differs, _only_mtime_differs],
    ids=["replaced", "only_ctime", "only_size", "only_mtime"],
)
def test_regression_gc_keeps_a_file_replaced_with_a_reused_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fields: Callable[[os.stat_result], dict[str, int]],
) -> None:
    """Linux and Windows file systems may give a replaced file's freed inode to its replacement.

    The replacement reports the listed file's device and inode, under its name and once set
    aside, so only its size, modification time or change time tells it apart.
    """
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    stray = garbage[-2]
    scan = fsck_scan.scan_repository
    replace = _replace_reusing_inode(monkeypatch, stray, fields)

    def scan_then_replace(root: Path, show_id: str | None, at: datetime | None) -> object:
        found = scan(root, show_id, at)
        replace()
        return found

    monkeypatch.setattr(gc_module, "scan_repository", scan_then_replace)
    report = gc(tmp_path, at=AT, confirm=True)
    assert _relative(tmp_path, stray) not in report.removed
    assert stray.read_bytes() == b"new"
    assert len(report.removed) == len(garbage) - 1


@pytest.mark.parametrize(
    "fields",
    [_as_written, _only_size_differs, _only_mtime_differs],
    ids=["replaced", "only_size", "only_mtime"],
)
def test_regression_gc_keeps_a_file_replaced_with_a_reused_inode_as_it_moves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fields: Callable[[os.stat_result], dict[str, int]],
) -> None:
    """The replacement lands after gc's last check of the name, just before gc moves it aside."""
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    stray = garbage[-2]
    move = durable_io.move_new
    replace = _replace_reusing_inode(monkeypatch, stray, fields)

    def replace_then_move(source: Path, target: Path) -> bool:
        if source == stray and not target.exists():
            replace()
        return move(source, target)

    monkeypatch.setattr(durable_io, "move_new", replace_then_move)
    report = gc(tmp_path, at=AT, confirm=True)
    assert _relative(tmp_path, stray) not in report.removed
    assert stray.read_bytes() == b"new"
    assert len(report.removed) == len(garbage) - 1


def test_regression_gc_removes_files_a_listing_reports_without_inodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows listings report device and inode 0, which no lookup of the file matches."""
    _, landed = _repository(tmp_path)
    garbage = _garbage(tmp_path, landed)
    list_like_windows(monkeypatch)
    report = gc(tmp_path, at=AT, confirm=True)
    assert set(report.removed) == {_relative(tmp_path, path) for path in garbage}
    assert not any(path.exists() for path in garbage)


@SKIP_LINKS
def test_gc_never_follows_a_link(tmp_path: Path) -> None:
    root, outside = tmp_path / "repo", tmp_path / "outside"
    _repository(root)
    (root / "tmp").rename(outside)
    (root / "tmp").symlink_to(outside, target_is_directory=True)
    temp = outside / ".tmp-crashed"
    temp.write_bytes(b"left behind")
    _age(temp, OLD)
    assert gc(root, at=AT, confirm=True).removed == ()
    assert temp.exists()


def test_gc_refuses_layout_1_and_a_time_without_a_zone(tmp_path: Path) -> None:
    DocumentRepository(_FileBackend(tmp_path)).transaction(
        "old", {"/renders/a.txt": b"x"}, expected_revision="", at=AT
    )
    with pytest.raises(VersionError):
        gc(tmp_path, at=AT, confirm=True)
    with pytest.raises(SchemaError):
        gc(tmp_path, at=AT.replace(tzinfo=None))
