#!/usr/bin/env python3

"""
test_file_commit_inputs.py --- tests that a file commit checks the blobs and tree it is handed

Contains:
    test_regression_file_commit_refuses_blob_bytes_that_are_not_their_digest: refuses a forged
        blob before writing anything, on a first commit and on a later one, with and without
        hard links
"""

import errno
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.history_models import Revision

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "show_1"
RENDER = "/renders/a.txt"
OTHER_RENDER = "/renders/b.txt"
LINK_MODES = ["hard links", "no hard links"]


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
