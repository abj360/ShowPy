#!/usr/bin/env python3

"""
test_fsck.py --- tests fsck on fresh layout-2 repositories damaged in one known way each

Contains:
    test_fsck_of_a_clean_repository_is_ok: finds nothing in repositories written by the backend
    test_fsck_of_an_empty_root_is_ok: passes an empty root and one a first commit left at tmp/
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
    test_findings_are_sorted_by_show_code_and_path: orders findings deterministically
"""

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.errors import ConflictError
from e8s.examples import two_set_show
from e8s.show.backend import digest
from e8s.show.document_dump import dump_json
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.fsck import fsck
from e8s.show.fsck_findings import is_torn_manifest
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder
from tests.fixtures.fsck_repos import (
    AT,
    DAY,
    OTHER_ID,
    SHOW_ID,
    STATIC_BYTES,
    age_by,
    blob_path,
    codes,
    landed_revisions,
    numbered_change,
    record_path,
    relative,
    show_path,
    slot_path,
    snapshot,
)

pytestmark = pytest.mark.usefixtures("no_reread_pauses")


def test_fsck_of_a_clean_repository_is_ok(tmp_path: Path) -> None:
    repo = DocumentRepository(FileBackend(tmp_path))
    first = repo.put_show("show_1", two_set_show(), expected_revision="", at=AT)
    renamed = two_set_show().replace(title="renamed")
    repo.put_show("show_1", renamed, expected_revision=first.id, at=AT + DAY)
    landed_revisions(tmp_path, 3)
    report = fsck(tmp_path)
    assert report.findings == ()
    assert report.ok


def test_fsck_of_an_empty_root_is_ok(tmp_path: Path) -> None:
    assert fsck(tmp_path).ok
    (tmp_path / "tmp").mkdir()
    assert fsck(tmp_path) == fsck(tmp_path, at=AT)
    assert fsck(tmp_path).findings == ()


def test_fsck_changes_nothing(tmp_path: Path) -> None:
    landed = landed_revisions(tmp_path, 3)
    slot_path(tmp_path, 2).unlink()
    record_path(tmp_path, landed[2]).write_bytes(b"{}")
    (tmp_path / "tmp" / ".tmp-old").write_bytes(b"left")
    age_by(tmp_path / "tmp" / ".tmp-old", 2 * DAY)
    slot_path(tmp_path, 4).write_bytes(b'{"id":')
    before = snapshot(tmp_path)
    assert not fsck(tmp_path, at=AT).ok
    assert snapshot(tmp_path) == before


def test_fsck_reports_missing_blob(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 2)
    blob = blob_path(tmp_path, b"render 0")
    blob.unlink()
    report = fsck(tmp_path)
    assert [(f.code, f.path, f.show) for f in report.findings] == [
        ("MISSING_BLOB", relative(tmp_path, blob), SHOW_ID)
    ]
    assert not report.ok


def test_fsck_reports_bad_blob(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 2)
    blob = blob_path(tmp_path, STATIC_BYTES)
    blob.write_bytes(b"swapped")
    report = fsck(tmp_path)
    assert codes(tmp_path) == [("BAD_BLOB", relative(tmp_path, blob))]
    assert "digest" in report.findings[0].message
    assert not report.ok


def _wrong_seq(root: Path, landed: list[Revision]) -> Path:
    path = record_path(root, landed[1])
    path.write_bytes(path.read_bytes().replace(b'"seq": 2', b'"seq": 3'))
    return path


def _slot_not_canonical(root: Path, _landed: list[Revision]) -> Path:
    path = slot_path(root, 2)
    path.write_bytes(path.read_bytes().replace(b'","', b'", "', 1))
    return path


def _record_missing(root: Path, landed: list[Revision]) -> Path:
    record_path(root, landed[1]).unlink()
    return slot_path(root, 2)


def _other_manifest(root: Path, _landed: list[Revision]) -> Path:
    path = show_path(root) / "show.json"
    path.write_bytes(dump_json({"id": OTHER_ID}))
    return path


def _manifest_missing(root: Path, _landed: list[Revision]) -> Path:
    path = show_path(root) / "show.json"
    path.unlink()
    return path


def _manifest_not_a_file(root: Path, _landed: list[Revision]) -> Path:
    path = show_path(root) / "show.json"
    path.unlink()
    path.mkdir()
    return path


def _wrong_parent(root: Path, landed: list[Revision]) -> Path:
    """Lands a third revision whose parent is the first, so its own record and ID still hold."""
    slot_path(root, 3).unlink()
    record_path(root, landed[2]).unlink()
    revision, tree, blobs = numbered_change(landed[0].id, 7)
    for name, data in blobs.items():
        if not (show_path(root) / "objects" / name).exists():
            (show_path(root) / "objects" / name).write_bytes(data)
    record = {"revision": revision.to_json(), "seq": 3, "tree": tree}
    record_path(root, revision).write_bytes(dump_json(record))
    slot_path(root, 3).write_bytes(jcs(revision.to_json()) + b"\n")
    return slot_path(root, 3)


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
    landed = landed_revisions(tmp_path, 3)
    path = damage(tmp_path, landed)
    report = fsck(tmp_path)
    assert ("BAD_RECORD", relative(tmp_path, path)) in codes(tmp_path)
    assert {item.code for item in report.findings} <= {"BAD_RECORD"}
    assert not report.ok


def test_fsck_reports_a_slot_whose_parent_is_not_the_slot_before(tmp_path: Path) -> None:
    landed = landed_revisions(tmp_path, 3)
    path = _wrong_parent(tmp_path, landed)
    findings = fsck(tmp_path).findings
    assert next((f.code, f.path) for f in findings if f.code == "BAD_RECORD") == (
        "BAD_RECORD",
        relative(tmp_path, path),
    )
    assert "parent" in findings[0].message


def test_regression_fsck_reports_an_unchanged_entry_injected_into_a_tree(tmp_path: Path) -> None:
    """A record's tree gains a path its revision did not change; its ID still verifies."""
    landed = landed_revisions(tmp_path, 3)
    path = record_path(tmp_path, landed[1])
    stored = json.loads(path.read_bytes())
    stored["tree"]["/injected.txt"] = digest(STATIC_BYTES)
    path.write_bytes(dump_json(stored))
    backend = FileBackend(tmp_path)
    assert "/injected.txt" in backend.tree(SHOW_ID, landed[1].id)
    report = fsck(tmp_path)
    assert codes(tmp_path) == [
        ("BAD_RECORD", relative(tmp_path, path)),
        ("BAD_RECORD", relative(tmp_path, record_path(tmp_path, landed[2]))),
    ]
    assert "previous tree" in report.findings[0].message
    assert not report.ok


def test_fsck_reports_slot_gap(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 3)
    slot_path(tmp_path, 2).unlink()
    report = fsck(tmp_path)
    assert codes(tmp_path) == [("SLOT_GAP", relative(tmp_path, slot_path(tmp_path, 2)))]
    assert report.findings[0].message == "slot 2 missing before slot 3"
    assert not report.ok


def test_regression_fsck_reports_a_two_slot_gap_below_the_head(tmp_path: Path) -> None:
    """Two deleted slots below the head let a cold head stop early; fsck reports the run."""
    landed_revisions(tmp_path, 7)
    for seq in (2, 3, 5):
        slot_path(tmp_path, seq).unlink()
    report = fsck(tmp_path)
    assert [(f.code, f.path, f.message) for f in report.findings] == [
        (
            "SLOT_GAP",
            relative(tmp_path, slot_path(tmp_path, 2)),
            "slots 2 to 3 missing before slot 4",
        ),
        ("SLOT_GAP", relative(tmp_path, slot_path(tmp_path, 5)), "slot 5 missing before slot 6"),
    ]
    assert not report.ok


@pytest.mark.parametrize("torn", [b"", b'{"at":"2026'])
def test_fsck_reports_torn_slot(tmp_path: Path, torn: bytes) -> None:
    landed_revisions(tmp_path, 2)
    slot_path(tmp_path, 3).write_bytes(torn)
    report = fsck(tmp_path)
    assert codes(tmp_path) == [("TORN_SLOT", relative(tmp_path, slot_path(tmp_path, 3)))]
    assert "last slot" in report.findings[0].message
    assert not report.ok
    slot_path(tmp_path, 4).write_bytes(slot_path(tmp_path, 2).read_bytes())
    middle = fsck(tmp_path).findings
    assert ("TORN_SLOT", relative(tmp_path, slot_path(tmp_path, 3))) in [
        (f.code, f.path) for f in middle
    ]
    assert "restore it from a backup" in middle[-1].message


def test_fsck_reports_unreachable(tmp_path: Path) -> None:
    landed = landed_revisions(tmp_path, 1)
    lost, tree, blobs = numbered_change("", 9)
    with pytest.raises(ConflictError):
        FileBackend(tmp_path).commit(SHOW_ID, lost, tree, blobs)
    stray = show_path(tmp_path) / "records" / f"{'f' * 64}.json"
    stray.write_bytes(b"{}")
    report = fsck(tmp_path)
    assert codes(tmp_path) == [
        ("UNREACHABLE", relative(tmp_path, blob_path(tmp_path, b"render 9"))),
        ("UNREACHABLE", relative(tmp_path, stray)),
    ]
    assert report.ok
    assert record_path(tmp_path, landed[0]).exists()


def test_fsck_reports_stale_temp(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 1)
    old, young = tmp_path / "tmp" / ".tmp-old", tmp_path / "tmp" / ".tmp-young"
    old.write_bytes(b"left")
    young.write_bytes(b"in flight")
    age_by(old, DAY + timedelta(seconds=1))
    age_by(young, DAY - timedelta(seconds=1))
    report = fsck(tmp_path, at=AT)
    assert codes(tmp_path, at=AT) == [("STALE_TEMP", "tmp/.tmp-old")]
    assert report.ok
    assert codes(tmp_path) == [("STALE_TEMP", "tmp/.tmp-old"), ("STALE_TEMP", "tmp/.tmp-young")]


def _stray_places(root: Path) -> list[Path]:
    folder = show_path(root)
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
    landed_revisions(tmp_path, 1)
    strays = _stray_places(tmp_path)
    for path in strays:
        path.write_bytes(b"?")
    (show_path(tmp_path) / "objects" / ("a" * 64)).mkdir()
    report = fsck(tmp_path)
    expected = {relative(tmp_path, path) for path in strays}
    expected.add(relative(tmp_path, show_path(tmp_path) / "objects" / ("a" * 64)))
    assert {f.path for f in report.findings} == expected
    assert {f.code for f in report.findings} == {"UNKNOWN_FILE"}
    assert not report.ok


def test_fsck_skips_housekeeping_names_and_assets(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 1)
    folder = show_path(tmp_path)
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
    assert codes(tmp_path) == [("TORN_FILE", "layout.json")]
    assert not report.ok
    (tmp_path / "layout.json").unlink()
    landed_revisions(tmp_path, 1)
    folder = show_path(tmp_path, OTHER_ID)
    for name in ("records", "objects"):
        (folder / name).mkdir(parents=True)
    (folder / "show.json").write_bytes(b'{\n  "id": "other_')
    assert codes(tmp_path) == [("TORN_FILE", relative(tmp_path, folder / "show.json"))]


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


def test_findings_are_sorted_by_show_code_and_path(tmp_path: Path) -> None:
    landed_revisions(tmp_path, 3)
    landed_revisions(tmp_path, 3, OTHER_ID)
    for show_id in (OTHER_ID, SHOW_ID):
        objects = show_path(tmp_path, show_id) / "objects"
        (objects / digest(b"render 2")).unlink()
        (objects / digest(b"render 1")).write_bytes(b"swapped")
    (tmp_path / "stray.txt").write_bytes(b"?")
    findings = fsck(tmp_path).findings
    keys = [(f.show, f.code, f.path) for f in findings]
    assert keys == sorted(keys)
    assert [f.show for f in findings] == ["", SHOW_ID, SHOW_ID, OTHER_ID, OTHER_ID]
