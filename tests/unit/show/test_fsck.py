#!/usr/bin/env python3

"""
test_fsck.py --- tests fsck on fresh layout-2 repositories damaged in one known way each

Contains:
    test_fsck_of_a_clean_repository_is_ok: finds nothing in repositories written by the backend
    test_fsck_of_an_empty_root_is_ok: passes an empty root and one a first commit left at tmp/
    test_fsck_of_a_missing_root_is_an_io_error: never reports a mistyped root clean
    test_fsck_of_a_layout_1_root_is_a_version_error: names layout 1 and changes nothing
    test_fsck_of_a_folder_that_is_no_repository_is_corrupt: raises what reading its layout raises
    test_fsck_changes_nothing: leaves every name, byte and time of a damaged root as it was
    test_fsck_reports_missing_blob: reports a blob a tree names that is gone
    test_fsck_reports_bad_blob: reports a blob whose bytes no longer hash to its name
    test_fsck_reports_bad_record: reports each damaged slot, record and manifest
    test_fsck_reports_a_slot_whose_parent_is_not_the_slot_before: reports a slot that forks the
        chain while its own record and ID hold
    test_regression_fsck_reports_an_unchanged_entry_injected_into_a_tree: reports a record whose
        tree gained a path its revision did not change, which reads still accept
    test_fsck_reports_slot_gap: reports one missing slot below the head
    test_regression_fsck_reports_a_two_slot_gap_below_the_head: reports a run of two deleted slots
        as one gap naming both, and two runs as two gaps
    test_fsck_reports_torn_slot: reports a torn last slot, and a torn slot before another
    test_fsck_reports_unreachable: lists a lost commit's blob and a stray record without failing
    test_fsck_reports_stale_temp: lists temp files older than a day before `at`, or all without it
    test_fsck_reports_unknown_file: reports a stray entry at every level of the layout
    test_fsck_skips_housekeeping_names_and_assets: reports no OS entry and nothing under assets/
    test_fsck_reports_torn_file: reports a torn layout file and a torn manifest
    test_torn_manifest_is_only_a_shorter_start_of_this_folders_manifest: tells a torn manifest
        from any other damaged one
    test_fsck_never_follows_a_link_under_the_root: reports a symlinked folder without reading
        through it
    test_fsck_reports_a_linked_show_folder: reports a symlinked show folder, whole root or one show
    test_fsck_follows_a_symlinked_root: checks a repository through a symlinked root
    test_fsck_of_one_show_checks_only_that_show: leaves other shows out and refuses an unknown one
    test_findings_are_sorted_by_show_code_and_path: orders findings deterministically
    test_fsck_refuses_a_time_without_a_zone: refuses a naive `at`
"""

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, SchemaError, StorageError, UnknownRefError, VersionError
from e8s.examples import two_set_show
from e8s.show.backend import digest
from e8s.show.document_dump import dump_json
from e8s.show.document_repository import DocumentRepository, revision_id
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.fsck import fsck
from e8s.show.fsck_show import is_torn_manifest
from e8s.show.history_models import Revision
from e8s.show.layout1 import _FileBackend
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "fsck_show"
OTHER_ID = "other_show"
STATIC = "/static.txt"
RENDER = "/render.txt"
STATIC_BYTES = b"static"
DAY = timedelta(days=1)
SKIP_LINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")


@pytest.fixture(autouse=True)
def _no_reread_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    """Builds revision n: the first writes both paths, every later one changes the render only."""
    render = f"render {n}".encode()
    tree = {STATIC: digest(STATIC_BYTES), RENDER: digest(render)}
    changed = (RENDER, STATIC) if parent == "" else (RENDER,)
    changes = {path: tree[path] for path in changed}
    revision = Revision(
        id=revision_id(parent, AT, changes), parent=parent, at=AT, changed=tuple(sorted(changed))
    )
    return revision, tree, {digest(render): render, digest(STATIC_BYTES): STATIC_BYTES}


def _landed(root: Path, count: int, show_id: str = SHOW_ID) -> list[Revision]:
    backend, parent, landed = FileBackend(root), "", []
    for n in range(count):
        revision, tree, blobs = _change(parent, n)
        backend.commit(show_id, revision, tree, blobs)
        landed.append(revision)
        parent = revision.id
    return landed


def _folder(root: Path, show_id: str = SHOW_ID) -> Path:
    return root / "shows" / show_folder(show_id)


def _slot(root: Path, seq: int) -> Path:
    return _folder(root) / "log" / f"{seq:0{SLOT_DIGITS}d}"


def _record(root: Path, revision: Revision) -> Path:
    return _folder(root) / "records" / f"{revision.id}.json"


def _blob(root: Path, data: bytes) -> Path:
    return _folder(root) / "objects" / digest(data)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _codes(root: Path, *, at: datetime | None = None) -> list[tuple[str, str]]:
    return [(item.code, item.path) for item in fsck(root, at=at).findings]


def _age(path: Path, by: timedelta) -> None:
    then = (AT - by).timestamp()
    os.utime(path, (then, then))


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        _relative(root, path): (
            path.read_bytes() if path.is_file() else b"",
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }


def test_fsck_of_a_clean_repository_is_ok(tmp_path: Path) -> None:
    repo = DocumentRepository(FileBackend(tmp_path))
    first = repo.put_show("show_1", two_set_show(), expected_revision="", at=AT)
    renamed = two_set_show().replace(title="renamed")
    repo.put_show("show_1", renamed, expected_revision=first.id, at=AT + DAY)
    _landed(tmp_path, 3)
    report = fsck(tmp_path)
    assert report.findings == ()
    assert report.ok


def test_fsck_of_an_empty_root_is_ok(tmp_path: Path) -> None:
    assert fsck(tmp_path).ok
    (tmp_path / "tmp").mkdir()
    assert fsck(tmp_path) == fsck(tmp_path, at=AT)
    assert fsck(tmp_path).findings == ()


def test_fsck_of_a_missing_root_is_an_io_error(tmp_path: Path) -> None:
    with pytest.raises(StorageError) as caught:
        fsck(tmp_path / "typo")
    assert caught.value.code == ErrorCode.IO
    assert not (tmp_path / "typo").exists()


def test_fsck_of_a_layout_1_root_is_a_version_error(tmp_path: Path) -> None:
    DocumentRepository(_FileBackend(tmp_path)).transaction(
        "old", {"/renders/a.txt": b"x"}, expected_revision="", at=AT
    )
    before = _snapshot(tmp_path)
    with pytest.raises(VersionError) as caught:
        fsck(tmp_path)
    assert caught.value.context["layout"] == 1
    assert "FileRepository" in caught.value.hint
    assert _snapshot(tmp_path) == before


def test_fsck_of_a_folder_that_is_no_repository_is_corrupt(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine")
    with pytest.raises(StorageError) as caught:
        fsck(tmp_path)
    assert caught.value.code == ErrorCode.CORRUPT
    (tmp_path / "notes.txt").unlink()
    (tmp_path / "layout.json").write_text('{"format": "e8.repository", "layout": 3}')
    with pytest.raises(VersionError):
        fsck(tmp_path)


def test_fsck_changes_nothing(tmp_path: Path) -> None:
    landed = _landed(tmp_path, 3)
    _slot(tmp_path, 2).unlink()
    _record(tmp_path, landed[2]).write_bytes(b"{}")
    (tmp_path / "tmp" / ".tmp-old").write_bytes(b"left")
    _age(tmp_path / "tmp" / ".tmp-old", 2 * DAY)
    _slot(tmp_path, 4).write_bytes(b'{"id":')
    before = _snapshot(tmp_path)
    assert not fsck(tmp_path, at=AT).ok
    assert _snapshot(tmp_path) == before


def test_fsck_reports_missing_blob(tmp_path: Path) -> None:
    _landed(tmp_path, 2)
    blob = _blob(tmp_path, b"render 0")
    blob.unlink()
    report = fsck(tmp_path)
    assert [(f.code, f.path, f.show) for f in report.findings] == [
        ("MISSING_BLOB", _relative(tmp_path, blob), SHOW_ID)
    ]
    assert not report.ok


def test_fsck_reports_bad_blob(tmp_path: Path) -> None:
    _landed(tmp_path, 2)
    blob = _blob(tmp_path, STATIC_BYTES)
    blob.write_bytes(b"swapped")
    report = fsck(tmp_path)
    assert _codes(tmp_path) == [("BAD_BLOB", _relative(tmp_path, blob))]
    assert "digest" in report.findings[0].message
    assert not report.ok


def _wrong_seq(root: Path, landed: list[Revision]) -> Path:
    path = _record(root, landed[1])
    path.write_bytes(path.read_bytes().replace(b'"seq": 2', b'"seq": 3'))
    return path


def _slot_not_canonical(root: Path, _landed: list[Revision]) -> Path:
    path = _slot(root, 2)
    path.write_bytes(path.read_bytes().replace(b'","', b'", "', 1))
    return path


def _record_missing(root: Path, landed: list[Revision]) -> Path:
    _record(root, landed[1]).unlink()
    return _slot(root, 2)


def _other_manifest(root: Path, _landed: list[Revision]) -> Path:
    path = _folder(root) / "show.json"
    path.write_bytes(dump_json({"id": OTHER_ID}))
    return path


def _manifest_missing(root: Path, _landed: list[Revision]) -> Path:
    path = _folder(root) / "show.json"
    path.unlink()
    return path


def _manifest_not_a_file(root: Path, _landed: list[Revision]) -> Path:
    path = _folder(root) / "show.json"
    path.unlink()
    path.mkdir()
    return path


def _wrong_parent(root: Path, landed: list[Revision]) -> Path:
    """Lands a third revision whose parent is the first, so its own record and ID still hold."""
    _slot(root, 3).unlink()
    _record(root, landed[2]).unlink()
    revision, tree, blobs = _change(landed[0].id, 7)
    for name, data in blobs.items():
        if not (_folder(root) / "objects" / name).exists():
            (_folder(root) / "objects" / name).write_bytes(data)
    record = {"revision": revision.to_json(), "seq": 3, "tree": tree}
    _record(root, revision).write_bytes(dump_json(record))
    _slot(root, 3).write_bytes(jcs(revision.to_json()) + b"\n")
    return _slot(root, 3)


@pytest.mark.parametrize(
    "damage",
    [
        _wrong_seq,
        _slot_not_canonical,
        _record_missing,
        _other_manifest,
        _manifest_missing,
        _manifest_not_a_file,
    ],
)
def test_fsck_reports_bad_record(
    tmp_path: Path, damage: Callable[[Path, list[Revision]], Path]
) -> None:
    landed = _landed(tmp_path, 3)
    path = damage(tmp_path, landed)
    report = fsck(tmp_path)
    assert ("BAD_RECORD", _relative(tmp_path, path)) in _codes(tmp_path)
    assert {item.code for item in report.findings} <= {"BAD_RECORD"}
    assert not report.ok


def test_fsck_reports_a_slot_whose_parent_is_not_the_slot_before(tmp_path: Path) -> None:
    landed = _landed(tmp_path, 3)
    path = _wrong_parent(tmp_path, landed)
    findings = fsck(tmp_path).findings
    assert next((f.code, f.path) for f in findings if f.code == "BAD_RECORD") == (
        "BAD_RECORD",
        _relative(tmp_path, path),
    )
    assert "parent" in findings[0].message


def test_regression_fsck_reports_an_unchanged_entry_injected_into_a_tree(tmp_path: Path) -> None:
    """A record's tree gains a path its revision did not change; its ID still verifies."""
    landed = _landed(tmp_path, 3)
    path = _record(tmp_path, landed[1])
    stored = json.loads(path.read_bytes())
    stored["tree"]["/injected.txt"] = digest(STATIC_BYTES)
    path.write_bytes(dump_json(stored))
    backend = FileBackend(tmp_path)
    assert "/injected.txt" in backend.tree(SHOW_ID, landed[1].id)
    report = fsck(tmp_path)
    assert _codes(tmp_path) == [
        ("BAD_RECORD", _relative(tmp_path, path)),
        ("BAD_RECORD", _relative(tmp_path, _record(tmp_path, landed[2]))),
    ]
    assert "previous tree" in report.findings[0].message
    assert not report.ok


def test_fsck_reports_slot_gap(tmp_path: Path) -> None:
    _landed(tmp_path, 3)
    _slot(tmp_path, 2).unlink()
    report = fsck(tmp_path)
    assert _codes(tmp_path) == [("SLOT_GAP", _relative(tmp_path, _slot(tmp_path, 2)))]
    assert report.findings[0].message == "slot 2 missing before slot 3"
    assert not report.ok


def test_regression_fsck_reports_a_two_slot_gap_below_the_head(tmp_path: Path) -> None:
    """Two deleted slots below the head let a cold head stop early; fsck reports the run."""
    _landed(tmp_path, 7)
    for seq in (2, 3, 5):
        _slot(tmp_path, seq).unlink()
    report = fsck(tmp_path)
    assert [(f.code, f.path, f.message) for f in report.findings] == [
        ("SLOT_GAP", _relative(tmp_path, _slot(tmp_path, 2)), "slots 2 to 3 missing before slot 4"),
        ("SLOT_GAP", _relative(tmp_path, _slot(tmp_path, 5)), "slot 5 missing before slot 6"),
    ]
    assert not report.ok


@pytest.mark.parametrize("torn", [b"", b'{"at":"2026'])
def test_fsck_reports_torn_slot(tmp_path: Path, torn: bytes) -> None:
    _landed(tmp_path, 2)
    _slot(tmp_path, 3).write_bytes(torn)
    report = fsck(tmp_path)
    assert _codes(tmp_path) == [("TORN_SLOT", _relative(tmp_path, _slot(tmp_path, 3)))]
    assert "last slot" in report.findings[0].message
    assert not report.ok
    _slot(tmp_path, 4).write_bytes(_slot(tmp_path, 2).read_bytes())
    middle = fsck(tmp_path).findings
    assert ("TORN_SLOT", _relative(tmp_path, _slot(tmp_path, 3))) in [
        (f.code, f.path) for f in middle
    ]
    assert "restore it from a backup" in middle[-1].message


def test_fsck_reports_unreachable(tmp_path: Path) -> None:
    landed = _landed(tmp_path, 1)
    lost, tree, blobs = _change("", 9)
    with pytest.raises(ConflictError):
        FileBackend(tmp_path).commit(SHOW_ID, lost, tree, blobs)
    stray = _folder(tmp_path) / "records" / f"{'f' * 64}.json"
    stray.write_bytes(b"{}")
    report = fsck(tmp_path)
    assert _codes(tmp_path) == [
        ("UNREACHABLE", _relative(tmp_path, _blob(tmp_path, b"render 9"))),
        ("UNREACHABLE", _relative(tmp_path, stray)),
    ]
    assert report.ok
    assert _record(tmp_path, landed[0]).exists()


def test_fsck_reports_stale_temp(tmp_path: Path) -> None:
    _landed(tmp_path, 1)
    old, young = tmp_path / "tmp" / ".tmp-old", tmp_path / "tmp" / ".tmp-young"
    old.write_bytes(b"left")
    young.write_bytes(b"in flight")
    _age(old, DAY + timedelta(seconds=1))
    _age(young, DAY - timedelta(seconds=1))
    report = fsck(tmp_path, at=AT)
    assert _codes(tmp_path, at=AT) == [("STALE_TEMP", "tmp/.tmp-old")]
    assert report.ok
    assert _codes(tmp_path) == [("STALE_TEMP", "tmp/.tmp-old"), ("STALE_TEMP", "tmp/.tmp-young")]


def _stray_places(root: Path) -> list[Path]:
    folder = _folder(root)
    return [
        root / "stray.txt",
        root / "shows" / "Not-A-Folder",
        folder / "extra",
        folder / "log" / "note",
        folder / "log" / f"{0:0{SLOT_DIGITS}d}",
        folder / "records" / "x.txt",
        folder / "objects" / "abc",
        root / "tmp" / "other",
    ]


def test_fsck_reports_unknown_file(tmp_path: Path) -> None:
    _landed(tmp_path, 1)
    strays = _stray_places(tmp_path)
    for path in strays:
        path.write_bytes(b"?")
    (_folder(tmp_path) / "objects" / ("a" * 64)).mkdir()
    report = fsck(tmp_path)
    expected = {_relative(tmp_path, path) for path in strays}
    expected.add(_relative(tmp_path, _folder(tmp_path) / "objects" / ("a" * 64)))
    assert {f.path for f in report.findings} == expected
    assert {f.code for f in report.findings} == {"UNKNOWN_FILE"}
    assert not report.ok


def test_fsck_skips_housekeeping_names_and_assets(tmp_path: Path) -> None:
    _landed(tmp_path, 1)
    folder = _folder(tmp_path)
    for parent in (tmp_path, tmp_path / "shows", folder, folder / "log", folder / "objects"):
        (parent / ".DS_Store").write_bytes(b"finder")
    (tmp_path / "tmp" / "Thumbs.db").write_bytes(b"explorer")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "anything").write_bytes(b"lane m")
    assert fsck(tmp_path, at=AT).findings == ()


def test_fsck_reports_torn_file(tmp_path: Path) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "layout.json").write_bytes(b'{\n  "format": "e8.repo')
    report = fsck(tmp_path)
    assert _codes(tmp_path) == [("TORN_FILE", "layout.json")]
    assert not report.ok
    (tmp_path / "layout.json").unlink()
    _landed(tmp_path, 1)
    folder = _folder(tmp_path, OTHER_ID)
    for name in ("records", "objects"):
        (folder / name).mkdir(parents=True)
    (folder / "show.json").write_bytes(b'{\n  "id": "other_')
    assert _codes(tmp_path) == [("TORN_FILE", _relative(tmp_path, folder / "show.json"))]


@pytest.mark.parametrize(
    ("data", "is_torn"),
    [
        (b"", True),
        (b'{\n  "i', True),
        (b'{\n  "id": "other_', True),
        (b'{\n  "id": "Other_', True),
        (b'{\n  "id": "other_show"\n', True),
        (b'{\n  "id": "other_show"\n}', True),
        (b'{\n  "id": "zzz', False),
        (b'{\n  "id": "other_shoe"\n', False),
        (b'{\n  "id": "other show', False),
        (b'{\n  "id": "other_show"\n}\n', False),
        (b"\0\0\0\0", False),
        (b'{"id": "other_show"', False),
    ],
)
def test_torn_manifest_is_only_a_shorter_start_of_this_folders_manifest(
    data: bytes, *, is_torn: bool
) -> None:
    assert is_torn_manifest(data, show_folder(OTHER_ID)) is is_torn


@SKIP_LINKS
@pytest.mark.parametrize("name", ["log", "records", "objects"])
def test_fsck_never_follows_a_link_under_the_root(tmp_path: Path, name: str) -> None:
    root, outside = tmp_path / "repo", tmp_path / "outside"
    _landed(root, 2)
    folder = _folder(root) / name
    folder.rename(outside)
    folder.symlink_to(outside, target_is_directory=True)
    before = _snapshot(outside)
    report = fsck(root)
    assert ("UNKNOWN_FILE", _relative(root, folder)) in [(f.code, f.path) for f in report.findings]
    assert not any(f.code == "UNREACHABLE" for f in report.findings)
    assert not report.ok
    assert _snapshot(outside) == before
    shows = root / "shows"
    shows.rename(tmp_path / "shows_elsewhere")
    shows.symlink_to(tmp_path / "shows_elsewhere", target_is_directory=True)
    assert _codes(root) == [("UNKNOWN_FILE", "shows")]


@SKIP_LINKS
def test_fsck_reports_a_linked_show_folder(tmp_path: Path) -> None:
    root, outside = tmp_path / "repo", tmp_path / "outside"
    _landed(root, 1)
    folder = _folder(root)
    folder.rename(outside)
    folder.symlink_to(outside, target_is_directory=True)
    expected = [("UNKNOWN_FILE", _relative(root, folder))]
    assert _codes(root) == expected
    assert [(f.code, f.path) for f in fsck(root, SHOW_ID).findings] == expected


@SKIP_LINKS
def test_fsck_follows_a_symlinked_root(tmp_path: Path) -> None:
    real, linked = tmp_path / "real", tmp_path / "linked"
    _landed(real, 2)
    linked.symlink_to(real, target_is_directory=True)
    assert fsck(linked).ok
    _blob(real, b"render 1").unlink()
    assert _codes(linked) == [("MISSING_BLOB", _relative(real, _blob(real, b"render 1")))]


def test_fsck_of_one_show_checks_only_that_show(tmp_path: Path) -> None:
    _landed(tmp_path, 2)
    _landed(tmp_path, 1, OTHER_ID)
    _blob(tmp_path, b"render 1").unlink()
    (tmp_path / "stray.txt").write_bytes(b"?")
    assert fsck(tmp_path, OTHER_ID).findings == ()
    assert [f.code for f in fsck(tmp_path, SHOW_ID).findings] == ["MISSING_BLOB"]
    with pytest.raises(UnknownRefError) as caught:
        fsck(tmp_path, "no_such_show")
    assert caught.value.context["show"] == "no_such_show"


def test_findings_are_sorted_by_show_code_and_path(tmp_path: Path) -> None:
    _landed(tmp_path, 3)
    _landed(tmp_path, 3, OTHER_ID)
    for show_id in (OTHER_ID, SHOW_ID):
        objects = _folder(tmp_path, show_id) / "objects"
        (objects / digest(b"render 2")).unlink()
        (objects / digest(b"render 1")).write_bytes(b"swapped")
    (tmp_path / "stray.txt").write_bytes(b"?")
    findings = fsck(tmp_path).findings
    keys = [(f.show, f.code, f.path) for f in findings]
    assert keys == sorted(keys)
    assert [f.show for f in findings] == ["", SHOW_ID, SHOW_ID, OTHER_ID, OTHER_ID]


def test_fsck_refuses_a_time_without_a_zone(tmp_path: Path) -> None:
    with pytest.raises(SchemaError):
        fsck(tmp_path, at=AT.replace(tzinfo=None))
