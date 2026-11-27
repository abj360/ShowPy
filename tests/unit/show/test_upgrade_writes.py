#!/usr/bin/env python3

"""
test_upgrade_writes.py --- tests commits and reads on a repository an upgrade wrote

Contains:
    test_regression_a_stale_parent_on_an_upgraded_show_is_a_conflict: raises ConflictError, not
        CORRUPT, for a commit whose parent is an older copied revision
"""

from pathlib import Path

import pytest

from e8s.core.errors import ConflictError
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.history_models import Revision
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import AT, copied

LEGACY_SHOW = "legacy-show"
AFTER = "/renders/after.txt"
STALE_PARENTS = (4, 0)


def _upgraded(tmp_path: Path) -> Path:
    target = tmp_path / "new"
    upgrade_repository(copied(tmp_path), target)
    return target


def _child(backend: FileBackend, parent: str) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    """Builds a commit adding one render on top of `parent`, with a current-scheme ID."""
    data = b"after"
    tree = {**backend.tree(LEGACY_SHOW, parent), AFTER: digest(data)}
    child_id = revision_id(parent, AT, {AFTER: digest(data)})
    return Revision(id=child_id, parent=parent, at=AT, changed=(AFTER,)), tree, {digest(data): data}


@pytest.mark.parametrize("index", STALE_PARENTS)
def test_regression_a_stale_parent_on_an_upgraded_show_is_a_conflict(
    tmp_path: Path, index: int
) -> None:
    target = _upgraded(tmp_path)
    history = FileBackend(target).log(LEGACY_SHOW)
    for backend in (FileBackend(target), FileBackend(target)):
        backend.head(LEGACY_SHOW)
        revision, tree, blobs = _child(backend, history[index].id)
        with pytest.raises(ConflictError):
            backend.commit(LEGACY_SHOW, revision, tree, blobs)
    assert FileBackend(target).log(LEGACY_SHOW) == history
