#!/usr/bin/env python3

"""
test_upgrade_writes.py --- tests commits and reads on a repository an upgrade wrote

Contains:
    test_regression_a_stale_parent_on_an_upgraded_show_is_a_conflict: raises ConflictError, not
        CORRUPT, for a commit whose parent is an older copied revision
    test_regression_copied_revisions_that_recompute_stay_verified: checks the ID of every copied
        record after the last old-scheme one, so a swapped record there reads as CORRUPT
    test_a_history_with_no_old_scheme_id_gets_no_legacy_before: writes a manifest holding only
        the show ID, after which fsck is clean and a commit lands
    test_upgrade_lists_the_revision_fields_it_drops: names the authorship fields v0.1 stored in
        each revision, and nothing for v0.4.0, whose revisions hold none
"""

import json
from pathlib import Path

import pytest

from e8s.core.errors import ConflictError, StorageError
from e8s.show.backend import digest
from e8s.show.document_dump import dump_json
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import AT, SHOW_ID, built, copied

LEGACY_SHOW = "legacy-show"
AFTER = "/renders/after.txt"
STALE_PARENTS = (4, 0)
# The v0.4.0 fixture's second revision has an old-scheme ID; every later one recomputes.
LEGACY_BEFORE = 3
CURRENT_FIELDS = frozenset({"id", "parent", "at", "changed"})
RECORD_KEYS = frozenset({"revision", "tree"})


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


def test_regression_copied_revisions_that_recompute_stay_verified(tmp_path: Path) -> None:
    target = _upgraded(tmp_path)
    history = FileBackend(target).log(LEGACY_SHOW)
    folder = target / "shows" / show_folder(LEGACY_SHOW)
    assert json.loads((folder / "show.json").read_bytes())["legacy_before"] == LEGACY_BEFORE
    record = folder / "records" / f"{history[-1].id}.json"
    stored = json.loads(record.read_bytes())
    stored["tree"][history[-1].changed[0]] = digest(b"planted")
    record.write_bytes(dump_json(stored))
    with pytest.raises(StorageError) as caught:
        FileBackend(target).tree(LEGACY_SHOW, history[-1].id)
    assert caught.value.code == "CORRUPT"
    assert "does not hash to its name" in caught.value.message


def test_a_history_with_no_old_scheme_id_gets_no_legacy_before(tmp_path: Path) -> None:
    built(tmp_path / "old", 3)
    upgrade_repository(tmp_path / "old", tmp_path / "new")
    manifest = tmp_path / "new" / "shows" / show_folder(SHOW_ID) / "show.json"
    assert json.loads(manifest.read_bytes()) == {"id": SHOW_ID}
    assert fsck(tmp_path / "new").findings == ()
    repo = FileRepository(tmp_path / "new")
    head = repo.head(SHOW_ID)
    landed = repo.transaction(SHOW_ID, {AFTER: b"after"}, expected_revision=head, at=AT)
    assert FileRepository(tmp_path / "new").head(SHOW_ID) == landed.id


def _stored_fields(source: Path, revision_id: str) -> tuple[str, ...]:
    """Names the keys of a layout-1 record that a current revision does not hold."""
    record = json.loads((source / LEGACY_SHOW / "trees" / f"{revision_id}.json").read_bytes())
    extra = [f"revision.{key}" for key in record["revision"] if key not in CURRENT_FIELDS]
    return tuple(sorted(extra + [f"record.{key}" for key in record if key not in RECORD_KEYS]))


@pytest.mark.parametrize(("release", "drops"), [("v0.1.1", True), ("v0.4.0", False)])
def test_upgrade_lists_the_revision_fields_it_drops(
    tmp_path: Path, release: str, *, drops: bool
) -> None:
    source = copied(tmp_path, release)
    report = upgrade_repository(source, tmp_path / "new")
    history = FileBackend(tmp_path / "new").log(LEGACY_SHOW)
    expected = tuple(
        (revision.id, fields)
        for revision in history
        if (fields := _stored_fields(source, revision.id))
    )
    assert report.shows[0].dropped == expected
    assert bool(expected) is drops
    if drops:
        assert all("revision.actor" in fields for _, fields in expected)
