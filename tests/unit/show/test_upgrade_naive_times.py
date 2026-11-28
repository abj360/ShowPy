#!/usr/bin/env python3

"""
test_upgrade_naive_times.py --- tests the upgrade of layout-1 revisions stored with a naive time

A layout-1 revision may store its time without a zone. The reader takes such a time as UTC, and
the upgrade writes every copy with that aware time, so a revision whose ID was hashed from the
naive time no longer recomputes and must fall in the legacy prefix.

Contains:
    test_regression_a_revision_hashed_from_a_naive_time_is_legacy_after_upgrade: puts the
        revision in the legacy prefix with consistent manifest keys, reads it with an aware time,
        passes fsck and takes a native commit
    test_a_naive_time_hashed_as_aware_needs_no_legacy_prefix: leaves a revision whose ID was
        hashed from the aware time outside any legacy prefix
"""

import json
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from e8s.examples import two_set_show
from e8s.show.document_dump import dump_json
from e8s.show.document_repository import revision_id
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.manifests import legacy_digest
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.show_folders import show_folder
from e8s.show.upgrade import upgrade_repository
from tests.fixtures.upgrade_repos import AT, SHOW_ID

REVISIONS = 3


def _layout1_chain(root: Path, *, naive_hashed: int | None, naive_stored: int | None) -> list[str]:
    """Writes a layout-1 show of three revisions, one stored with a naive time.

    Args:
        root: Layout-1 root to create.
        naive_hashed: Index of the revision whose ID hashes its naive time, if any.
        naive_stored: Index of the revision stored with a naive time but hashed as aware, if any.

    Returns:
        ids: The revision IDs, oldest first.
    """
    memory = InMemoryRepository()
    head = memory.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT).id
    for n in range(1, REVISIONS):
        docs = {f"/renders/{n}.txt": f"render {n}".encode()}
        at = AT + timedelta(minutes=n)
        head = memory.transaction(SHOW_ID, docs, expected_revision=head, at=at).id
    folder, parent, ids = root / SHOW_ID, "", []
    for name in ("objects", "trees", "next"):
        (folder / name).mkdir(parents=True)
    for index, revision in enumerate(memory.revisions(SHOW_ID)):
        tree = dict(memory.backend.tree(SHOW_ID, revision.id))
        naive = revision.at.replace(tzinfo=None)
        hashed_at = naive if index == naive_hashed else revision.at
        changes = {path: tree.get(path) for path in revision.changed}
        new_id = revision_id(parent, hashed_at, changes)
        stored_at = naive if index in (naive_hashed, naive_stored) else revision.at
        stored = {
            "id": new_id,
            "parent": parent,
            "at": stored_at.isoformat(),
            "changed": list(revision.changed),
        }
        for digest in tree.values():
            (folder / "objects" / digest).write_bytes(memory.backend.blob(SHOW_ID, digest))
        (folder / "trees" / f"{new_id}.json").write_bytes(
            dump_json({"revision": stored, "tree": tree})
        )
        (folder / "next" / (parent or "root")).write_text(new_id)
        ids.append(new_id)
        parent = new_id
    return ids


def _manifest(root: Path) -> dict[str, object]:
    path = root / "shows" / show_folder(SHOW_ID) / "show.json"
    loaded: dict[str, object] = json.loads(path.read_bytes())
    return loaded


@pytest.mark.parametrize("naive_hashed", range(REVISIONS))
def test_regression_a_revision_hashed_from_a_naive_time_is_legacy_after_upgrade(
    tmp_path: Path, naive_hashed: int
) -> None:
    ids = _layout1_chain(tmp_path / "old", naive_hashed=naive_hashed, naive_stored=None)
    upgrade_repository(tmp_path / "old", tmp_path / "new")
    legacy_before = naive_hashed + 2
    assert _manifest(tmp_path / "new") == {
        "id": SHOW_ID,
        "legacy_before": legacy_before,
        "legacy_count": legacy_before - 1,
        "legacy_digest": legacy_digest(ids[: legacy_before - 1]),
    }
    assert fsck(tmp_path / "new").findings == ()
    repo = FileRepository(tmp_path / "new")
    revisions = repo.revisions(SHOW_ID)
    assert [r.id for r in revisions] == ids
    assert all(r.at.utcoffset() == timedelta(0) for r in revisions)
    for revision in revisions:
        assert repo.backend.tree(SHOW_ID, revision.id)
    after = {"/renders/after.txt": b"after"}
    landed = repo.transaction(SHOW_ID, after, expected_revision=ids[-1], at=AT)
    assert FileRepository(tmp_path / "new").head(SHOW_ID) == landed.id
    assert fsck(tmp_path / "new").findings == ()


def test_a_naive_time_hashed_as_aware_needs_no_legacy_prefix(tmp_path: Path) -> None:
    ids = _layout1_chain(tmp_path / "old", naive_hashed=None, naive_stored=1)
    upgrade_repository(tmp_path / "old", tmp_path / "new")
    assert _manifest(tmp_path / "new") == {"id": SHOW_ID}
    assert fsck(tmp_path / "new").findings == ()
    revisions = FileRepository(tmp_path / "new").revisions(SHOW_ID)
    assert [r.id for r in revisions] == ids
    assert revisions[1].at.tzinfo is not None
    assert revisions[1].at.astimezone(UTC) == revisions[1].at
