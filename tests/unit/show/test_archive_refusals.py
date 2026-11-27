#!/usr/bin/env python3

"""
test_archive_refusals.py --- tests that import_archive refuses a damaged or forged archive whole

Every refusal is checked against a file and a memory repository that already hold another show:
both raise the same code, and neither changes, on disk or in its show list.

Contains:
    test_regression_archive_with_a_bad_blob_is_refused: refuses a blob whose bytes do not hash
        to its name, without echoing the bytes
    test_regression_archive_entry_outside_the_show_is_refused: refuses `../` and every other name
        outside the archive's entries
    test_regression_a_zip_bomb_is_refused: refuses a deflated entry, a declared size over the cap
        and an end record counting too many entries
    test_regression_duplicate_entries_are_refused: refuses a blob written twice
    test_regression_truncated_or_garbage_bytes_are_refused: refuses cut-short and non-ZIP bytes
    test_regression_a_valid_zip_with_the_wrong_manifest_is_refused: refuses a missing, foreign,
        newer, malformed or non-canonical manifest, and a show ID a new show may not take
    test_regression_a_tampered_record_is_refused: refuses a record whose slot number or time was
        changed, naming the revision whose ID no longer recomputes
    test_regression_an_injected_unchanged_tree_entry_is_refused: refuses a tree that gained a
        path its revision did not change
    test_regression_missing_or_unreferenced_entries_are_refused: refuses a missing blob, record
        or slot, and a blob or record no slot reaches
    test_regression_entries_not_in_repository_form_are_refused: refuses a record or slot that
        parses but is not in the bytes a repository writes, and a slot cut short
    test_regression_a_forked_last_slot_is_refused: refuses a last slot whose parent is not the
        slot before, though its own ID, record and tree agree
    test_regression_a_changed_path_outside_the_document_paths_is_refused: refuses a revision
        that changes a path with a `..` segment, though everything else agrees
    test_regression_an_archive_without_slots_is_refused: refuses a manifest with no history
    test_regression_a_history_over_the_revision_cap_is_refused: refuses more slots than a show
        may hold
"""

import json
import struct
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest

from e8s.core.canonical import jcs
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError
from e8s.show import archive_checks
from e8s.show.archive import export_archive, import_archive
from e8s.show.backend import digest
from e8s.show.commit_writes import record_bytes, slot_bytes
from e8s.show.document_repository import revision_id
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision
from e8s.show.memory_backend import InMemoryRepository
from tests.fixtures.archives import SHOW_ID, entry_map, listing, rebuilt, with_history

OTHER_ID = "other_1"
EVIL = b"EVIL-PAYLOAD-never-echoed"
SLOT_2 = "log/000000000002"
SLOT_3 = "log/000000000003"
SLOT_4 = "log/000000000004"

type Tamper = Callable[[dict[str, bytes]], None]


@pytest.fixture(name="entries")
def _entries() -> dict[str, bytes]:
    repo = InMemoryRepository()
    with_history(repo, 4)
    return entry_map(export_archive(repo, SHOW_ID))


def _refused(tmp_path: Path, data: bytes) -> E8sError:
    """Imports into a file and a memory repository; both refuse alike and neither changes."""
    files, memory = FileRepository(tmp_path), InMemoryRepository()
    with_history(files, 1, OTHER_ID)
    with_history(memory, 1, OTHER_ID)
    before = listing(tmp_path)
    errors = []
    for repo in (files, memory):
        with pytest.raises(E8sError) as caught:
            import_archive(repo, data)
        errors.append(caught.value)
        assert repo.shows() == [OTHER_ID]
    assert listing(tmp_path) == before
    assert errors[0].code == errors[1].code
    assert errors[0].message == errors[1].message
    assert EVIL.decode() not in f"{errors[0].message} {errors[0].context} {errors[0].hint}"
    return errors[0]


def _names(entries: dict[str, bytes], prefix: str) -> list[str]:
    return sorted(name for name in entries if name.startswith(prefix))


def _record(entries: dict[str, bytes], seq: int) -> tuple[str, dict[str, object]]:
    slot = json.loads(entries[f"log/{seq:012d}"])
    name = f"records/{slot['id']}.json"
    return name, json.loads(entries[name])


def test_regression_archive_with_a_bad_blob_is_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    name = _names(entries, "objects/")[0]
    entries[name] = EVIL
    error = _refused(tmp_path, rebuilt(entries))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == name
    assert "digest" in error.message


@pytest.mark.parametrize(
    "name",
    [
        "../evil.txt",
        "/etc/passwd",
        "log/../../evil",
        "objects\\..\\evil",
        "log/000000000001/",
        "Archive.json",
        "log/00000000001",
        f"records/{'A' * 64}.json",
        "assets/fonts/a.json",
    ],
)
def test_regression_archive_entry_outside_the_show_is_refused(
    tmp_path: Path, entries: dict[str, bytes], name: str
) -> None:
    entries[name] = EVIL
    error = _refused(tmp_path, rebuilt(entries))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == name


def test_regression_a_zip_bomb_is_refused(tmp_path: Path, entries: dict[str, bytes]) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as written:
        for name in sorted(entries):
            written.writestr(name, entries[name])
    assert "compressed" in _refused(tmp_path / "deflated", buffer.getvalue()).message
    data = rebuilt(entries)
    end = len(data) - 22
    central = struct.unpack_from("<I", data, end + 16)[0]
    huge = data[: central + 20] + struct.pack("<II", 2**31 - 2, 2**31 - 2) + data[central + 28 :]
    assert _refused(tmp_path / "huge", huge).code == ErrorCode.TOO_LARGE
    flooded = data[: end + 8] + struct.pack("<HH", 0xFFFF, 0xFFFF) + data[end + 12 :]
    assert _refused(tmp_path / "flooded", flooded).code == ErrorCode.TOO_LARGE


def test_regression_duplicate_entries_are_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    first, second = _names(entries, "objects/")[:2]
    entries[second] = entries[first]
    data = rebuilt(entries).replace(second.encode(), first.encode())
    error = _refused(tmp_path, data)
    assert error.code == ErrorCode.CORRUPT
    assert "twice" in error.message


def test_regression_truncated_or_garbage_bytes_are_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    data = rebuilt(entries)
    for damaged in (data[:-1], data[: len(data) // 2], b"", b"garbage" * 100, b"PK\x03\x04"):
        assert _refused(tmp_path / str(len(damaged)), damaged).code == ErrorCode.CORRUPT


@pytest.mark.parametrize(
    ("manifest", "code"),
    [
        (None, ErrorCode.CORRUPT),
        (b"not json", ErrorCode.CORRUPT),
        (b"[]\n", ErrorCode.CORRUPT),
        ({"format": "e8.repository", "show": SHOW_ID, "version": 1}, ErrorCode.CORRUPT),
        ({"format": "e8.show-archive", "show": SHOW_ID, "version": 2}, ErrorCode.VERSION),
        ({"format": "e8.show-archive", "show": SHOW_ID, "version": 0}, ErrorCode.CORRUPT),
        ({"format": "e8.show-archive", "show": SHOW_ID, "version": True}, ErrorCode.CORRUPT),
        ({"format": "e8.show-archive", "show": SHOW_ID, "version": 1, "x": 1}, ErrorCode.CORRUPT),
        ({"format": "e8.show-archive", "show": "show-1", "version": 1}, ErrorCode.BAD_PATH),
        ({"format": "e8.show-archive", "show": "con", "version": 1}, ErrorCode.BAD_PATH),
        (jcs({"format": "e8.show-archive", "show": SHOW_ID, "version": 1}), ErrorCode.CORRUPT),
    ],
)
def test_regression_a_valid_zip_with_the_wrong_manifest_is_refused(
    tmp_path: Path,
    entries: dict[str, bytes],
    manifest: bytes | dict[str, object] | None,
    code: ErrorCode,
) -> None:
    if manifest is None:
        del entries["archive.json"]
    elif isinstance(manifest, dict):
        entries["archive.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    else:
        entries["archive.json"] = manifest
    assert _refused(tmp_path, rebuilt(entries)).code == code


def test_regression_a_tampered_record_is_refused(tmp_path: Path, entries: dict[str, bytes]) -> None:
    name, record = _record(entries, 2)
    shifted = {**record, "seq": 3}
    error = _refused(tmp_path, rebuilt({**entries, name: _dumped(shifted)}))
    assert error.code == ErrorCode.CORRUPT
    assert "holds seq 3, not 2" in error.message
    revision = dict(json.loads(entries[SLOT_2]), at="2030-01-01T00:00:00Z")
    retimed = {
        **entries,
        SLOT_2: jcs(revision) + b"\n",
        name: _dumped({**record, "revision": revision}),
    }
    error = _refused(tmp_path / "retimed", rebuilt(retimed))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["revision"] == revision["id"]
    assert error.context["seq"] == 2
    assert "layout 1" in error.hint


def _dumped(stored: dict[str, object]) -> bytes:
    return (json.dumps(stored, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def test_regression_an_injected_unchanged_tree_entry_is_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    name, record = _record(entries, 3)
    tree = {str(path): str(blob) for path, blob in json.loads(entries[name])["tree"].items()}
    tree["/renders/injected.txt"] = next(iter(tree.values()))
    error = _refused(tmp_path, rebuilt({**entries, name: _dumped({**record, "tree": tree})}))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == name
    assert "tree before it" in error.message


@pytest.mark.parametrize(
    ("tamper", "named", "reason"),
    [
        (lambda e: e.pop(_names(e, "objects/")[0]), "objects/", "does not hold"),
        (lambda e: e.pop(_names(e, "records/")[0]), "records/", "has no record"),
        (lambda e: e.pop(SLOT_2), SLOT_3, "slot 2 is missing"),
        (lambda e: e.pop(_names(e, "log/")[-1]), "records/", "no slot names"),
        (lambda e: e.update({f"objects/{digest(EVIL)}": EVIL}), "objects/", "no tree names"),
        (
            lambda e: e.update({f"records/{'0' * 64}.json": e[_record(e, 1)[0]]}),
            f"records/{'0' * 64}.json",
            "no slot names",
        ),
    ],
)
def test_regression_missing_or_unreferenced_entries_are_refused(
    tmp_path: Path, entries: dict[str, bytes], tamper: Tamper, named: str, reason: str
) -> None:
    tamper(entries)
    error = _refused(tmp_path, rebuilt(entries))
    assert error.code == ErrorCode.CORRUPT
    assert str(error.context["entry"]).startswith(named)
    assert reason in error.message


def test_regression_entries_not_in_repository_form_are_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    name, record = _record(entries, 1)
    error = _refused(tmp_path, rebuilt({**entries, name: jcs(record)}))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == name
    spelled = entries[SLOT_3].replace(b'Z"', b'+00:00"')
    assert spelled != entries[SLOT_3]
    error = _refused(tmp_path / "spelled", rebuilt({**entries, SLOT_3: spelled}))
    assert error.context["entry"] == SLOT_3
    assert "bytes a repository writes" in error.message
    unfinished = {**entries, SLOT_3: entries[SLOT_3].rstrip(b"\n")}
    error = _refused(tmp_path / "slot", rebuilt(unfinished))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == SLOT_3


def test_regression_a_history_over_the_revision_cap_is_refused(
    tmp_path: Path, entries: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(archive_checks, "MAX_REVISIONS", 3)
    assert _refused(tmp_path, rebuilt(entries)).code == ErrorCode.TOO_LARGE


def _reforged(entries: dict[str, bytes], parent: str, extra: str) -> str:
    """Rewrites the last of four slots with another parent or one more changed path, consistently.

    The revision ID is recomputed, and the slot, the record and its name are rewritten, so only
    the parent or the extra path differs from what an export writes.
    """
    name, _ = _record(entries, 4)
    stored = json.loads(entries[name])
    tree = {str(path): str(blob) for path, blob in stored["tree"].items()}
    old = Revision.from_json(stored["revision"])
    changed = old.changed
    if extra:
        tree[extra] = next(iter(tree.values()))
        changed = tuple(sorted((*changed, extra)))
    changes = {path: tree.get(path) for path in changed}
    new_id = revision_id(parent, old.at, changes)
    revision = Revision(id=new_id, parent=parent, at=old.at, changed=changed)
    del entries[name]
    entries[SLOT_4] = slot_bytes(revision)
    entries[f"records/{new_id}.json"] = record_bytes(revision, 4, tree)
    return new_id


def test_regression_a_forked_last_slot_is_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    _reforged(entries, "f" * 64, "")
    error = _refused(tmp_path, rebuilt(entries))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == SLOT_4
    assert "parent" in error.message


def test_regression_a_changed_path_outside_the_document_paths_is_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    parent = json.loads(entries[SLOT_4])["parent"]
    _reforged(entries, parent, "/renders/../evil.txt")
    error = _refused(tmp_path, rebuilt(entries))
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == SLOT_4
    assert "not a document path" in error.message


def test_regression_an_archive_without_slots_is_refused(
    tmp_path: Path, entries: dict[str, bytes]
) -> None:
    error = _refused(tmp_path, rebuilt({"archive.json": entries["archive.json"]}))
    assert error.code == ErrorCode.CORRUPT
    assert "no slot" in error.message
