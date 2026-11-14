#!/usr/bin/env python3

"""
test_file_repository.py --- encodes audited file-repository defects as strict xfails

Contains:
    test_regression_next_link_to_outside_path_is_refused: refuses a next/ file naming a path outside
        the tree store
    test_regression_next_link_cycle_is_refused: refuses a cycle in next/ instead of looping forever
    test_regression_tree_digest_path_traversal_is_refused: refuses a tree entry whose digest is a
        path outside objects/
    test_regression_missing_object_is_a_storage_error: maps a missing object file to StorageError,
        not FileNotFoundError
    test_regression_truncated_tree_is_a_storage_error: maps a truncated tree file to StorageError,
        not JSONDecodeError
    test_regression_missing_hard_links_are_a_storage_error: maps a file system without hard links to
        StorageError, or commits anyway
    test_regression_swapped_blob_is_reported_corrupt: reports a swapped blob as corrupt by verifying
        its hash on read
    test_regression_commit_cleans_stale_temp_files: removes `.tmp-*` files left by crashed writers
        on the next commit
    test_regression_losing_commit_leaves_no_orphan_tree: removes the tree file of a commit that lost
        the race
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from showpy.core.errors import ConflictError, StorageError
from showpy.show.file_repository import FileRepository
from showpy.show.models import Revision
from showpy.show.repositories import digest
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show-1"
STALE_AGE_S = 7 * 24 * 3600
DECOY_NAME = "decoy"
SHOW_DOC = "/show/show.json"


def _saved(tmp_path: Path) -> tuple[FileRepository, str, Path]:
    repo = FileRepository(tmp_path / "repo")
    rev = repo.put_show(
        SHOW_ID, two_set_show(), expected_revision="", actor="designer", at=AT, message="create"
    )
    return repo, rev.id, tmp_path / "repo" / SHOW_ID


def _second_revision(repo: FileRepository, parent: str) -> str:
    return repo.transaction(
        SHOW_ID,
        {"/renders/a.txt": b"hi"},
        expected_revision=parent,
        actor="agent",
        at=AT,
        message="render",
    ).id


@pytest.mark.defect("UP-9.7-03")
@pytest.mark.xfail(strict=True, reason="UP-9.7-03")
def test_regression_next_link_to_outside_path_is_refused(tmp_path: Path) -> None:
    """Refuses a next/ file naming a path outside the tree store (audit 9.7 item 3; P0 #3)."""
    repo, rev, base = _saved(tmp_path)
    record = json.loads((base / "trees" / f"{rev}.json").read_text())
    decoy = tmp_path / f"{DECOY_NAME}.json"
    decoy.write_text(json.dumps(record))
    (base / "next" / rev).write_text(f"../../../{DECOY_NAME}")
    with pytest.raises(StorageError):
        repo.revisions(SHOW_ID)


@pytest.mark.defect("UP-9.7-04")
@pytest.mark.xfail(strict=True, reason="UP-9.7-04")
@pytest.mark.timeout(5, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_next_link_cycle_is_refused(tmp_path: Path) -> None:
    """Refuses a cycle in next/ instead of looping forever (audit 9.7 item 3; P0 #3, #7)."""
    repo, rev, base = _saved(tmp_path)
    (base / "next" / rev).write_text(rev)
    with pytest.raises(StorageError):
        repo.revisions(SHOW_ID)


@pytest.mark.defect("UP-9.7-05")
@pytest.mark.xfail(strict=True, reason="UP-9.7-05")
def test_regression_tree_digest_path_traversal_is_refused(tmp_path: Path) -> None:
    """Refuses a tree entry whose digest is a path outside objects/ (audit 9.7 item 3; P0 #3)."""
    repo, rev, base = _saved(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"outside")
    tree_file = base / "trees" / f"{rev}.json"
    record = json.loads(tree_file.read_text())
    record["tree"][SHOW_DOC] = "../../../secret.txt"
    tree_file.write_text(json.dumps(record))
    with pytest.raises(StorageError):
        repo.read(SHOW_ID, SHOW_DOC)


@pytest.mark.defect("UP-9.7-22")
@pytest.mark.xfail(strict=True, reason="UP-9.7-22")
def test_regression_missing_object_is_a_storage_error(tmp_path: Path) -> None:
    """Maps a missing object file to StorageError, not FileNotFoundError (audit 9.7 item 9)."""
    repo, _, base = _saved(tmp_path)
    next(iter((base / "objects").iterdir())).unlink()
    with pytest.raises(StorageError):
        repo.get_show(SHOW_ID)


@pytest.mark.defect("UP-9.7-23")
@pytest.mark.xfail(strict=True, reason="UP-9.7-23")
def test_regression_truncated_tree_is_a_storage_error(tmp_path: Path) -> None:
    """Maps a truncated tree file to StorageError, not JSONDecodeError (audit 9.7 item 9)."""
    repo, rev, base = _saved(tmp_path)
    tree_file = base / "trees" / f"{rev}.json"
    tree_file.write_bytes(tree_file.read_bytes()[:20])
    with pytest.raises(StorageError):
        repo.revisions(SHOW_ID)


@pytest.mark.defect("UP-9.7-24")
@pytest.mark.xfail(strict=True, reason="UP-9.7-24")
def test_regression_missing_hard_links_are_a_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maps a file system without hard links to StorageError, or commits anyway (audit 9.7 #9)."""

    def refuse(*_args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    try:
        repo, rev, _ = _saved(tmp_path)
    except StorageError:
        return
    assert repo.head(SHOW_ID) == rev


@pytest.mark.defect("UP-9.7-26")
@pytest.mark.xfail(strict=True, reason="UP-9.7-26")
def test_regression_swapped_blob_is_reported_corrupt(tmp_path: Path) -> None:
    """Reports a swapped blob as corrupt by verifying its hash on read (audit 9.7 item 10)."""
    repo, _, base = _saved(tmp_path)
    digest = json.loads(next((base / "trees").iterdir()).read_text())["tree"][SHOW_DOC]
    (base / "objects" / digest).write_bytes(b"{}")
    with pytest.raises(StorageError):
        repo.read(SHOW_ID, SHOW_DOC)


@pytest.mark.defect("UP-9.7-27")
@pytest.mark.xfail(strict=True, reason="UP-9.7-27")
def test_regression_commit_cleans_stale_temp_files(tmp_path: Path) -> None:
    """Removes `.tmp-*` files left by crashed writers on the next commit (audit 9.7 item 10)."""
    repo, rev, base = _saved(tmp_path)
    stale = base / "objects" / ".tmp-crashed"
    stale.write_bytes(b"partial")
    os.utime(stale, (AT.timestamp() - STALE_AGE_S, AT.timestamp() - STALE_AGE_S))
    _second_revision(repo, rev)
    assert not list(base.rglob(".tmp-*"))


@pytest.mark.defect("UP-9.7-28")
@pytest.mark.xfail(strict=True, reason="UP-9.7-28")
def test_regression_losing_commit_leaves_no_orphan_tree(tmp_path: Path) -> None:
    """Removes the tree file of a commit that lost the race (audit 9.7 item 10)."""
    repo, rev, base = _saved(tmp_path)
    _second_revision(repo, rev)
    loser_blob = b"loser"
    loser = Revision(
        id="loser",
        parent=rev,
        at=AT,
        actor="agent",
        message="late",
        changed=("/renders/b.txt",),
    )
    tree = {"/renders/b.txt": digest(loser_blob)}
    with pytest.raises(ConflictError):
        repo.backend.commit(SHOW_ID, loser, tree, {digest(loser_blob): loser_blob})
    assert not (base / "trees" / "loser.json").exists()
