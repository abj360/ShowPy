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
    test_regression_record_id_must_match_its_link: refuses a tree record whose revision id differs
        from the link that named it
    test_regression_record_parent_must_follow_the_chain: refuses a tree record whose parent is not
        the previous revision
    test_regression_non_utf8_link_is_corrupt: maps an undecodable next/ link to CORRUPT
    test_regression_symlinked_object_is_refused: refuses an object that is a symbolic link
    test_regression_symlinked_directory_blocks_commit: refuses to write through a symlinked
        objects/ directory
    test_regression_dangling_root_link_is_corrupt: treats a dangling next/root as corruption, not
        as an empty history
    test_regression_malformed_records_are_corrupt: maps malformed, directory-shaped and deeply
        nested tree files to CORRUPT
    test_regression_trailing_newline_in_id_is_refused: refuses an ID followed by a newline
    test_regression_link_failures_carry_no_hardlinks_code: maps os.link refusals to NO_HARDLINKS
        with show, path and errno context
    test_regression_temp_file_failure_is_io_not_no_hardlinks: keeps a failing temp write out of
        NO_HARDLINKS
    test_regression_long_names_are_name_too_long: maps ENAMETOOLONG on writes and reads
    test_regression_losing_commit_race_is_a_conflict: keeps a lost link race a ConflictError
    test_regression_overlong_show_id_reads_raise_name_too_long: refuses reads of a 300-character ID
    test_regression_other_os_errors_are_io: maps stray OSErrors on read paths and show_ids() to IO
    test_regression_deep_or_huge_tree_record_is_corrupt: maps a tree record nested past the stack
        or holding an overlong int to CORRUPT
    test_regression_oversized_files_are_corrupt_before_reading: refuses a sparse terabyte link,
        tree record or blob by its size, naming the kind and cap but not the content
    test_regression_file_growing_after_its_size_check_is_corrupt: refuses a file that grew after
        fstat reported its size
    test_regression_swapped_blob_is_corrupt_naming_its_file: reports a blob whose bytes miss their
        digest as CORRUPT with the object path
"""

import errno
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import pytest

from showpy.core.errors import ConflictError, StorageError
from showpy.show.file_repository import (
    BLOB_MAX_BYTES,
    LINK_MAX_BYTES,
    TREE_MAX_BYTES,
    FileRepository,
)
from showpy.show.models import Revision
from showpy.show.repositories import digest
from tests.fixtures.shows import two_set_show
from tests.helpers import HOSTILE_JSON, capped_address_space, fails_after

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show-1"
STALE_AGE_S = 7 * 24 * 3600
DECOY_NAME = "decoy"
SHOW_DOC = "/show/show.json"
RACERS = 8


def _saved(tmp_path: Path) -> tuple[FileRepository, str, Path]:
    repo = FileRepository(tmp_path / "repo")
    rev = repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    return repo, rev.id, tmp_path / "repo" / SHOW_ID


def _second_revision(repo: FileRepository, parent: str) -> str:
    return repo.transaction(
        SHOW_ID,
        {"/renders/a.txt": b"hi"},
        expected_revision=parent,
        at=AT,
    ).id


def test_regression_next_link_to_outside_path_is_refused(tmp_path: Path) -> None:
    """Refuses a next/ file naming a path outside the tree store (audit 9.7 item 3; P0 #3)."""
    repo, rev, base = _saved(tmp_path)
    record = json.loads((base / "trees" / f"{rev}.json").read_text())
    decoy = tmp_path / f"{DECOY_NAME}.json"
    decoy.write_text(json.dumps(record))
    (base / "next" / rev).write_text(f"../../../{DECOY_NAME}")
    with pytest.raises(StorageError):
        repo.revisions(SHOW_ID)


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_next_link_cycle_is_refused(tmp_path: Path) -> None:
    """Refuses a cycle in next/ instead of looping forever (audit 9.7 item 3; P0 #3, #7)."""
    repo, rev, base = _saved(tmp_path)
    (base / "next" / rev).write_text(rev)
    with pytest.raises(StorageError):
        repo.revisions(SHOW_ID)


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


def test_regression_missing_object_is_a_storage_error(tmp_path: Path) -> None:
    """Maps a missing object file to StorageError, not FileNotFoundError (audit 9.7 item 9)."""
    repo, _, base = _saved(tmp_path)
    next(iter((base / "objects").iterdir())).unlink()
    with pytest.raises(StorageError):
        repo.get_show(SHOW_ID)


def test_regression_truncated_tree_is_a_storage_error(tmp_path: Path) -> None:
    """Maps a truncated tree file to StorageError, not JSONDecodeError (audit 9.7 item 9)."""
    repo, rev, base = _saved(tmp_path)
    tree_file = base / "trees" / f"{rev}.json"
    tree_file.write_bytes(tree_file.read_bytes()[:20])
    with pytest.raises(StorageError):
        repo.revisions(SHOW_ID)


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
        changed=("/renders/b.txt",),
    )
    tree = {"/renders/b.txt": digest(loser_blob)}
    with pytest.raises(ConflictError):
        repo.backend.commit(SHOW_ID, loser, tree, {digest(loser_blob): loser_blob})
    assert not (base / "trees" / "loser.json").exists()


CORRUPT = "CORRUPT"
NO_SYMLINKS = pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")
HEX_ID = "a" * 64
NESTING_DEPTH = 100_000


def _tamper_record(base: Path, rev: str, edit: Callable[[dict[str, Any]], None]) -> None:
    path = base / "trees" / f"{rev}.json"
    record = json.loads(path.read_text())
    edit(record)
    path.write_text(json.dumps(record))


def _assert_corrupt(action: Callable[[], object]) -> None:
    with pytest.raises(StorageError) as caught:
        action()
    assert caught.value.code == CORRUPT


def test_regression_record_id_must_match_its_link(tmp_path: Path) -> None:
    """Refuses a tree record whose revision id differs from the link that named it."""
    repo, rev, base = _saved(tmp_path)
    _tamper_record(base, rev, lambda r: r["revision"].update(id="../../../x"))
    _assert_corrupt(lambda: repo.revisions(SHOW_ID))


def test_regression_record_parent_must_follow_the_chain(tmp_path: Path) -> None:
    """Refuses a tree record whose parent is not the previous revision."""
    repo, rev, base = _saved(tmp_path)
    _tamper_record(base, rev, lambda r: r["revision"].update(parent=HEX_ID))
    _assert_corrupt(lambda: repo.revisions(SHOW_ID))


def test_regression_non_utf8_link_is_corrupt(tmp_path: Path) -> None:
    """Maps an undecodable next/ link to CORRUPT."""
    repo, rev, base = _saved(tmp_path)
    (base / "next" / rev).write_bytes(b"\xff\xfe")
    _assert_corrupt(lambda: repo.revisions(SHOW_ID))


@NO_SYMLINKS
def test_regression_symlinked_object_is_refused(tmp_path: Path) -> None:
    """Refuses an object that is a symbolic link instead of reading outside the root."""
    repo, rev, base = _saved(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"outside")
    object_file = base / "objects" / repo.backend.tree(SHOW_ID, rev)[SHOW_DOC]
    object_file.unlink()
    object_file.symlink_to(secret)
    _assert_corrupt(lambda: repo.read(SHOW_ID, SHOW_DOC))


@NO_SYMLINKS
def test_regression_symlinked_directory_blocks_commit(tmp_path: Path) -> None:
    """Refuses to write through a symlinked objects/ directory."""
    repo, rev, base = _saved(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    shutil.move(base / "objects", elsewhere)
    (base / "objects").symlink_to(elsewhere)
    before = sorted(elsewhere.iterdir())
    _assert_corrupt(lambda: _second_revision(repo, rev))
    assert sorted(elsewhere.iterdir()) == before


@NO_SYMLINKS
def test_regression_dangling_root_link_is_corrupt(tmp_path: Path) -> None:
    """Treats a dangling next/root as corruption, not as an empty history."""
    repo, _, base = _saved(tmp_path)
    (base / "next" / "root").unlink()
    (base / "next" / "root").symlink_to(tmp_path / "nowhere")
    _assert_corrupt(lambda: repo.revisions(SHOW_ID))


def test_regression_malformed_records_are_corrupt(tmp_path: Path) -> None:
    """Maps malformed, directory-shaped and deeply nested tree files to CORRUPT."""
    repo, rev, base = _saved(tmp_path)
    tree_file = base / "trees" / f"{rev}.json"
    good = tree_file.read_text()
    bad_records = [
        json.dumps({"revision": json.loads(good)["revision"]}),
        json.dumps([1, 2]),
        json.dumps({**json.loads(good), "tree": {SHOW_DOC: 5}}),
        json.dumps({**json.loads(good), "tree": [HEX_ID]}),
        "[" * NESTING_DEPTH,
    ]
    for text in bad_records:
        tree_file.write_text(text)
        _assert_corrupt(lambda: repo.backend.tree(SHOW_ID, rev))
    tree_file.write_bytes(b"\xff\xfe")
    _assert_corrupt(lambda: repo.backend.tree(SHOW_ID, rev))
    tree_file.unlink()
    tree_file.mkdir()
    _assert_corrupt(lambda: repo.backend.tree(SHOW_ID, rev))


@pytest.mark.skipif(sys.platform == "win32", reason="newline in file names")
def test_regression_trailing_newline_in_id_is_refused(tmp_path: Path) -> None:
    """Refuses a tree digest followed by a newline, even when that object file exists."""
    repo, rev, base = _saved(tmp_path)
    digest_value = repo.backend.tree(SHOW_ID, rev)[SHOW_DOC]
    shutil.copy(base / "objects" / digest_value, base / "objects" / f"{digest_value}\n")
    _tamper_record(base, rev, lambda r: r["tree"].update({SHOW_DOC: f"{digest_value}\n"}))
    _assert_corrupt(lambda: repo.read(SHOW_ID, SHOW_DOC))


def _commit_error(tmp_path: Path) -> StorageError:
    repo = FileRepository(tmp_path / "repo")
    with pytest.raises(StorageError) as caught:
        repo.put_show(SHOW_ID, two_set_show(), expected_revision="", at=AT)
    return caught.value


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(errno.EPERM, "Operation not permitted"),
        OSError(errno.EPERM, "Operation not permitted"),
        OSError(errno.EXDEV, "Invalid cross-device link"),
        OSError(errno.ENOTSUP, "Operation not supported"),
    ],
)
def test_regression_link_failures_carry_no_hardlinks_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    """Maps os.link refusals to NO_HARDLINKS with show, path and errno context."""

    def refuse(*_args: object) -> None:
        raise error

    monkeypatch.setattr(os, "link", refuse)
    caught = _commit_error(tmp_path)
    assert caught.code == "NO_HARDLINKS"
    assert set(caught.context) == {"show", "path", "errno"}
    assert caught.context["show"] == SHOW_ID
    assert caught.context["errno"] == error.errno
    assert caught.hint


def test_regression_temp_file_failure_is_io_not_no_hardlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeps a failing temp write out of NO_HARDLINKS."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", refuse)
    caught = _commit_error(tmp_path)
    assert caught.code == "IO"
    assert caught.context["errno"] == errno.EACCES


def test_regression_long_names_are_name_too_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maps ENAMETOOLONG on writes and reads."""
    repo, _, _ = _saved(tmp_path)
    with pytest.raises(StorageError) as written:
        repo.put_show("a" * 300, two_set_show(), expected_revision="", at=AT)

    def too_long(_self: Path, **_kwargs: object) -> os.stat_result:
        raise OSError(errno.ENAMETOOLONG, "File name too long")

    monkeypatch.setattr(Path, "lstat", too_long)
    with pytest.raises(StorageError) as read:
        repo.revisions(SHOW_ID)
    for caught in (written.value, read.value):
        assert caught.code == "NAME_TOO_LONG"
        assert caught.context["errno"] == errno.ENAMETOOLONG
        assert set(caught.context) == {"show", "path", "errno"}


def test_regression_other_os_errors_are_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Maps stray OSErrors on read paths and show_ids() to IO."""
    repo, _, base = _saved(tmp_path)
    shutil.rmtree(base / "trees")
    (base / "trees").write_bytes(b"not a directory")
    with pytest.raises(StorageError) as not_dir:
        repo.revisions(SHOW_ID)
    assert not_dir.value.code == "IO"
    assert not_dir.value.context["errno"] == errno.ENOTDIR

    def deny(_self: Path) -> list[Path]:
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path))

    monkeypatch.setattr(Path, "iterdir", deny)
    with pytest.raises(StorageError) as listing:
        repo.shows()
    assert listing.value.code == "IO"
    assert listing.value.context["errno"] == errno.EACCES


def test_regression_permission_denied_read_is_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maps PermissionError from a read to IO."""
    repo, _, _ = _saved(tmp_path)

    def deny(_self: Path, *_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path))

    monkeypatch.setattr(Path, "open", deny)
    with pytest.raises(StorageError) as caught:
        repo.revisions(SHOW_ID)
    assert caught.value.code == "IO"
    assert caught.value.context["errno"] == errno.EACCES


def test_regression_losing_commit_race_is_a_conflict(tmp_path: Path) -> None:
    """Keeps a lost os.link race a ConflictError, never IO or NO_HARDLINKS."""
    repo, rev, _ = _saved(tmp_path)
    repo.transaction(SHOW_ID, {"/renders/a.txt": b"a"}, expected_revision=rev, at=AT)
    with pytest.raises(ConflictError):
        repo.transaction(SHOW_ID, {"/renders/b.txt": b"b"}, expected_revision=rev, at=AT)


def test_regression_threaded_commit_race_has_one_winner(tmp_path: Path) -> None:
    """Lands exactly one of several threads racing on one parent; the rest get ConflictError."""
    repo, rev, _ = _saved(tmp_path)
    outcomes: list[object] = []

    def attempt(index: int) -> None:
        try:
            outcomes.append(
                repo.transaction(
                    SHOW_ID, {f"/renders/{index}.txt": b"x"}, expected_revision=rev, at=AT
                )
            )
        except StorageError as error:
            outcomes.append(error)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(RACERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    losers = [o for o in outcomes if isinstance(o, StorageError)]
    assert len(outcomes) - len(losers) == 1
    assert all(isinstance(o, ConflictError) for o in losers)


def test_regression_overlong_show_id_reads_raise_name_too_long(tmp_path: Path) -> None:
    """Refuses reads of a 300-character ID instead of reporting an empty show."""
    repo, _, _ = _saved(tmp_path)
    long_id = "a" * 300
    for read in (
        lambda: repo.revisions(long_id),
        lambda: repo.head(long_id),
        lambda: repo.read(long_id, SHOW_DOC),
    ):
        with pytest.raises(StorageError) as caught:
            read()
        assert caught.value.code == "NAME_TOO_LONG"


@pytest.mark.parametrize("raw", HOSTILE_JSON.values(), ids=HOSTILE_JSON.keys())
def test_regression_deep_or_huge_tree_record_is_corrupt(tmp_path: Path, raw: bytes) -> None:
    """Maps a tree record nested past the stack or holding an overlong int to CORRUPT."""
    repo, rev, base = _saved(tmp_path)
    (base / "trees" / f"{rev}.json").write_bytes(raw)
    _assert_corrupt(lambda: repo.backend.tree(SHOW_ID, rev))
    _assert_corrupt(lambda: repo.revisions(SHOW_ID))


SPARSE_SIZE = 2**40
BLOCK_BYTES = 512


def _make_sparse(path: Path) -> None:
    """Grows `path` to a terabyte without writing it; skips where sparse files are missing."""
    try:
        os.truncate(path, SPARSE_SIZE)
    except OSError as error:
        pytest.skip(f"no sparse files here: {error}")
    if getattr(path.stat(), "st_blocks", SPARSE_SIZE) * BLOCK_BYTES >= SPARSE_SIZE:
        pytest.skip("file system stores truncated files in full")


def _oversized_reads(repo: FileRepository, rev: str, base: Path) -> dict[str, tuple[Path, int]]:
    digest_value = repo.backend.tree(SHOW_ID, rev)[SHOW_DOC]
    return {
        "link": (base / "next" / "root", LINK_MAX_BYTES),
        "tree record": (base / "trees" / f"{rev}.json", TREE_MAX_BYTES),
        "blob": (base / "objects" / digest_value, BLOB_MAX_BYTES),
    }


@pytest.mark.skipif(sys.platform != "linux", reason="needs RLIMIT_AS to stop a runaway read")
@pytest.mark.parametrize("kind", ["link", "tree record", "blob"])
def test_regression_oversized_files_are_corrupt_before_reading(tmp_path: Path, kind: str) -> None:
    """Refuses a sparse terabyte file by its size instead of allocating it (lane A review).

    The address space is capped so a missing size check fails fast instead of exhausting memory.
    """
    repo, rev, base = _saved(tmp_path)
    path, cap = _oversized_reads(repo, rev, base)[kind]
    _make_sparse(path)
    with capped_address_space(), pytest.raises(StorageError) as caught:
        repo.read(SHOW_ID, SHOW_DOC)
    assert caught.value.code == CORRUPT
    assert f"{kind} is larger than {cap} bytes" in caught.value.message
    assert caught.value.context == {"show": SHOW_ID, "path": str(path)}


def test_regression_file_growing_after_its_size_check_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuses a link that grew after fstat reported its size, past the cap or not."""
    repo, _, base = _saved(tmp_path)
    (base / "next" / "root").write_bytes(b"a" * (LINK_MAX_BYTES * 2))
    reported_size = 1
    real_fstat = os.fstat

    def stale_fstat(fd: int) -> os.stat_result:
        fields = list(real_fstat(fd))
        fields[stat.ST_SIZE] = reported_size
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", stale_fstat)
    with pytest.raises(StorageError) as caught:
        repo.revisions(SHOW_ID)
    assert caught.value.code == CORRUPT
    assert "link grew while it was read" in caught.value.message


def test_regression_swapped_blob_is_corrupt_naming_its_file(tmp_path: Path) -> None:
    """Reports a blob whose bytes miss their digest as CORRUPT with the object path."""
    repo, rev, base = _saved(tmp_path)
    blob_path = base / "objects" / repo.backend.tree(SHOW_ID, rev)[SHOW_DOC]
    blob_path.write_bytes(blob_path.read_bytes() + b" ")
    with pytest.raises(StorageError) as caught:
        repo.get_show(SHOW_ID)
    assert caught.value.code == CORRUPT
    assert caught.value.context["path"] == str(blob_path)
    assert "do not match their digest" in caught.value.message
