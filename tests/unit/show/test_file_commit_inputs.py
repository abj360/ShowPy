#!/usr/bin/env python3

"""
test_file_commit_inputs.py --- tests that a file commit checks the blobs and tree it is handed

Contains:
    test_regression_file_commit_refuses_blob_bytes_that_are_not_their_digest: refuses a forged
        blob before writing anything, on a first commit and on a later one, with and without
        hard links
    test_regression_file_commit_refuses_a_tree_naming_a_blob_its_show_does_not_store: refuses a
        tree naming a blob never stored, stored only by another show, or set aside by a stopped
        gc, with nothing changed, with and without hard links
    test_file_commit_reuses_a_blob_its_show_stores_outside_the_parent_tree: lands without the
        bytes and freshens the blob, so gc keeps it
    test_file_commit_refuses_to_reuse_a_stored_blob_whose_bytes_changed: raises CORRUPT
"""

import errno
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.fsck_findings import SET_ASIDE_PREFIX
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "show_1"
RENDER = "/renders/a.txt"
OTHER_RENDER = "/renders/b.txt"
OTHER_SHOW_ID = "show_2"
LINK_MODES = ["hard links", "no hard links"]
OLD = timedelta(days=2)


def _links(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Makes os.link fail with EPERM, as on exFAT, when `mode` says the file system has none."""
    if mode == "hard links":
        return

    def refuse(_source: str | Path, _target: str | Path, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)


def _revision(parent: str, tree: dict[str, str], changed: tuple[str, ...]) -> Revision:
    ids = {path: tree.get(path) for path in changed}
    return Revision(id=revision_id(parent, AT, ids), parent=parent, at=AT, changed=changed)


def _base(backend: FileBackend) -> tuple[Revision, dict[str, str]]:
    data = b"base"
    tree = {RENDER: digest(data)}
    revision = _revision("", tree, (RENDER,))
    backend.commit(SHOW_ID, revision, tree, {digest(data): data})
    return revision, tree


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    """Maps every path under `folder` to its bytes, or None for a folder."""
    return {
        str(path): path.read_bytes() if path.is_file() else None
        for path in sorted(folder.rglob("*"))
    }


@pytest.mark.parametrize("mode", LINK_MODES)
@pytest.mark.parametrize("has_base", [False, True])
def test_regression_file_commit_refuses_blob_bytes_that_are_not_their_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, has_base: bool
) -> None:
    _links(monkeypatch, mode)
    backend = FileBackend(tmp_path)
    parent, tree = _base(backend) if has_base else (None, {})
    claimed = digest(b"claimed")
    forged_tree = {**tree, OTHER_RENDER: claimed}
    forged = _revision(parent.id if parent else "", forged_tree, (OTHER_RENDER,))
    before = _snapshot(tmp_path)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, forged, forged_tree, {claimed: b"forged"})
    assert caught.value.code == ErrorCode.CORRUPT
    assert caught.value.context["show"] == SHOW_ID
    assert _snapshot(tmp_path) == before
    assert FileBackend(tmp_path).head(SHOW_ID) == (parent.id if parent else "")


def _object(root: Path, blob: str, show_id: str = SHOW_ID) -> Path:
    return root / "shows" / show_folder(show_id) / "objects" / blob


def _store_elsewhere(root: Path, data: bytes) -> None:
    other = FileBackend(root)
    tree = {RENDER: digest(data)}
    other.commit(OTHER_SHOW_ID, _revision("", tree, (RENDER,)), tree, {digest(data): data})


def _set_aside(root: Path, data: bytes) -> None:
    """Leaves the blob as a stopped gc leaves it: moved aside under its `.gc-` name."""
    path = _object(root, digest(data))
    path.write_bytes(data)
    path.rename(path.with_name(SET_ASIDE_PREFIX + path.name))


PLACEMENTS = {
    "never stored": lambda _root, _data: None,
    "stored only by another show": _store_elsewhere,
    "set aside by a stopped gc": _set_aside,
}


def _borrowing(
    parent: Revision, tree: dict[str, str], data: bytes
) -> tuple[Revision, dict[str, str]]:
    borrowed = {**tree, OTHER_RENDER: digest(data)}
    return _revision(parent.id, borrowed, (OTHER_RENDER,)), borrowed


@pytest.mark.parametrize("mode", LINK_MODES)
@pytest.mark.parametrize("placement", sorted(PLACEMENTS))
def test_regression_file_commit_refuses_a_tree_naming_a_blob_its_show_does_not_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, placement: str
) -> None:
    _links(monkeypatch, mode)
    backend = FileBackend(tmp_path)
    parent, tree = _base(backend)
    data = b"not sent"
    PLACEMENTS[placement](tmp_path, data)
    revision, borrowed = _borrowing(parent, tree, data)
    before = _snapshot(tmp_path)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, revision, borrowed, {})
    assert caught.value.code == ErrorCode.CORRUPT
    assert caught.value.context["show"] == SHOW_ID
    assert _snapshot(tmp_path) == before
    assert FileBackend(tmp_path).head(SHOW_ID) == parent.id


@pytest.mark.parametrize("mode", LINK_MODES)
def test_file_commit_reuses_a_blob_its_show_stores_outside_the_parent_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _links(monkeypatch, mode)
    backend = FileBackend(tmp_path)
    parent, tree = _base(backend)
    data = b"left by a lost commit"
    stored = _object(tmp_path, digest(data))
    stored.write_bytes(data)
    old = (AT - OLD).timestamp()
    os.utime(stored, (old, old))
    revision, borrowed = _borrowing(parent, tree, data)
    backend.commit(SHOW_ID, revision, borrowed, {})
    assert stored.stat().st_mtime != old
    assert FileBackend(tmp_path).head(SHOW_ID) == revision.id
    assert gc(tmp_path, at=AT, confirm=True).removed == ()
    assert FileBackend(tmp_path).blob(SHOW_ID, digest(data)) == data


def test_file_commit_refuses_to_reuse_a_stored_blob_whose_bytes_changed(tmp_path: Path) -> None:
    backend = FileBackend(tmp_path)
    parent, tree = _base(backend)
    data = b"left by a lost commit"
    _object(tmp_path, digest(data)).write_bytes(b"changed since")
    revision, borrowed = _borrowing(parent, tree, data)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, revision, borrowed, {})
    assert caught.value.code == ErrorCode.CORRUPT
    assert FileBackend(tmp_path).head(SHOW_ID) == parent.id
