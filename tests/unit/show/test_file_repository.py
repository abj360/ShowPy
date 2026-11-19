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
    test_regression_backend_refuses_a_traversal_revision_id: refuses a revision ID that would put
        its tree file outside the repository with BAD_ID, writing nothing
    test_regression_oversized_blob_is_refused_before_it_is_written: refuses a blob over the read
        cap at commit, so the show stays readable
    test_regression_object_swapped_after_lstat_is_refused: refuses a symlink or FIFO swapped in
        between the lstat check and the open, without following or blocking on it
    test_regression_backend_refuses_a_malformed_parent_id: blames the caller for a malformed parent
        ID passed to commit
    test_regression_backend_refuses_a_malformed_blob_digest: blames the caller for a malformed blob
        digest passed to commit
    test_regression_stored_malformed_id_stays_corrupt: keeps CORRUPT for a malformed stored ID
    test_regression_windows_overlong_component_is_name_too_long: maps the Windows invalid-name
        error for a component over 255 characters to NAME_TOO_LONG
    test_regression_windows_invalid_short_name_stays_io: keeps the Windows invalid-name error IO
        when no component is too long
    test_regression_file_for_a_directory_is_io_where_the_os_reports_it_missing: reports a file
        standing in for trees/ as ENOTDIR even where the OS says the path is missing
    test_regression_blob_landed_by_a_racing_writer_is_kept: commits when a racing writer lands the
        same blob first and the OS refuses to replace it
    test_regression_tree_record_landed_by_an_identical_commit_is_kept: commits when an identical
        commit wrote the same tree record first and the OS refuses to replace it
    test_regression_planted_tree_record_blocks_the_commit: refuses a commit whose tree record name
        holds another tree, a truncated record or a symlink, before its next/ link
    test_regression_failed_temp_write_leaves_no_temp_file: removes the temp file when writing or
        syncing it fails
    test_regression_tree_at_walks_the_log_once: parses at most one record per revision plus the
        tree when a workspace reads a whole tree
    test_regression_commit_refuses_to_land_over_a_damaged_existing_blob: refuses a commit whose
        blob is already stored with other bytes, before its tree record or link is written
    test_regression_commit_refuses_a_symlink_at_a_blob_target: refuses a commit whose blob name
        holds a symbolic link, even to the right bytes, before its tree record or link is written
"""

import contextlib
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

import showpy.show.file_repository as file_repository
from showpy.core.errors import ConflictError, StorageError, bounded_repr
from showpy.show.file_repository import (
    BLOB_MAX_BYTES,
    LINK_MAX_BYTES,
    TREE_MAX_BYTES,
    FileRepository,
)
from showpy.show.models import Revision
from showpy.show.repositories import digest
from showpy.show.storage import open_workspace
from tests.fixtures.shows import two_set_show
from tests.helpers import HOSTILE_JSON, capped_address_space, fails_after

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show-1"
STALE_AGE_S = 7 * 24 * 3600
DECOY_NAME = "decoy"
SHOW_DOC = "/show/show.json"
RACERS = 8
LOSER_ID = "0" * 64
TRAVERSAL_ID = "../../../escaped"
RENDER_COMMITS = 5


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
        id=LOSER_ID,
        parent=rev,
        at=AT,
        changed=("/renders/b.txt",),
    )
    tree = {"/renders/b.txt": digest(loser_blob)}
    with pytest.raises(ConflictError):
        repo.backend.commit(SHOW_ID, loser, tree, {digest(loser_blob): loser_blob})
    assert not (base / "trees" / f"{loser.id}.json").exists()


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
        assert set(caught.context) - {"winerror"} == {"show", "path", "errno"}
        assert caught.context.get("winerror") in (None, ERROR_INVALID_NAME)


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

    def deny(*_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path))

    monkeypatch.setattr(os, "open", deny)
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
    assert all(isinstance(o, ConflictError) for o in losers), [
        (o.code, o.message, o.context) for o in losers if not isinstance(o, ConflictError)
    ]


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
    assert caught.value.context == {
        "show": SHOW_ID,
        "path": bounded_repr(str(path)),
        "file": path.name,
    }


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
    assert caught.value.context["path"] == bounded_repr(str(blob_path))
    assert caught.value.context["file"] == blob_path.name
    assert "do not match their digest" in caught.value.message


def _all_files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file()}


def test_regression_backend_refuses_a_traversal_revision_id(tmp_path: Path) -> None:
    """Refuses a revision ID that would put its tree file outside the root, writing nothing."""
    repo, rev, base = _saved(tmp_path)
    before = _all_files(tmp_path)
    raw = b"escaped"
    escaped = Revision(id=TRAVERSAL_ID, parent=rev, at=AT, changed=("/renders/b.txt",))
    tree = {**repo.backend.tree(SHOW_ID, rev), "/renders/b.txt": digest(raw)}
    with pytest.raises(StorageError) as caught:
        repo.backend.commit(SHOW_ID, escaped, tree, {digest(raw): raw})
    assert caught.value.code == "BAD_ID"
    assert "64 lowercase hex digits" in caught.value.hint
    assert _all_files(tmp_path) == before
    assert not (base / "trees" / f"{TRAVERSAL_ID}.json").resolve().exists()
    assert repo.head(SHOW_ID) == rev


def test_regression_backend_refuses_a_malformed_parent_id(tmp_path: Path) -> None:
    """Blames the caller, not the repository, for a malformed parent ID passed to commit."""
    repo, rev, _ = _saved(tmp_path)
    child = Revision(id=HEX_ID, parent=TRAVERSAL_ID, at=AT, changed=())
    with pytest.raises(StorageError) as caught:
        repo.backend.commit(SHOW_ID, child, repo.backend.tree(SHOW_ID, rev), {})
    assert caught.value.code == "BAD_ID"


def test_regression_backend_refuses_a_malformed_blob_digest(tmp_path: Path) -> None:
    """Blames the caller for a malformed blob digest passed to commit, writing nothing."""
    repo, rev, _ = _saved(tmp_path)
    before = _all_files(tmp_path)
    child = Revision(id=HEX_ID, parent=rev, at=AT, changed=("/renders/b.txt",))
    tree = {**repo.backend.tree(SHOW_ID, rev), "/renders/b.txt": TRAVERSAL_ID}
    with pytest.raises(StorageError) as caught:
        repo.backend.commit(SHOW_ID, child, tree, {TRAVERSAL_ID: b"x"})
    assert caught.value.code == "BAD_ID"
    assert "blob digest" in caught.value.message
    assert _all_files(tmp_path) == before


def test_regression_stored_malformed_id_stays_corrupt(tmp_path: Path) -> None:
    """Keeps CORRUPT for a malformed ID read from the repository folder."""
    repo, _, base = _saved(tmp_path)
    (base / "next" / "root").write_text(TRAVERSAL_ID)
    _assert_corrupt(lambda: repo.backend.log(SHOW_ID))


SMALL_BLOB_CAP = 100_000
OVERSIZED_PATH = "/renders/video.mp4"


def test_regression_oversized_blob_is_refused_before_it_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuses a blob over the read cap at commit; a blob at the cap lands and reads back."""
    monkeypatch.setattr(file_repository, "BLOB_MAX_BYTES", SMALL_BLOB_CAP)
    repo, rev, _ = _saved(tmp_path)
    before = _all_files(tmp_path)
    with pytest.raises(StorageError) as caught:
        repo.transaction(
            SHOW_ID, {OVERSIZED_PATH: b"\0" * (SMALL_BLOB_CAP + 1)}, expected_revision=rev, at=AT
        )
    assert caught.value.code == "TOO_LARGE"
    assert caught.value.context["paths"] == [OVERSIZED_PATH]
    assert _all_files(tmp_path) == before
    assert repo.head(SHOW_ID) == rev
    repo.get_show(SHOW_ID)
    at_cap = b"\0" * SMALL_BLOB_CAP
    repo.transaction(SHOW_ID, {OVERSIZED_PATH: at_cap}, expected_revision=rev, at=AT)
    assert repo.read(SHOW_ID, OVERSIZED_PATH) == at_cap


def _swapped_after_lstat(
    monkeypatch: pytest.MonkeyPatch, target: Path, swap: Callable[[Path], None]
) -> None:
    """Swaps `target` for something else while lstat keeps reporting the regular file it was."""
    stale = target.lstat()
    target.unlink()
    swap(target)
    real_lstat = Path.lstat

    def stale_lstat(path: Path) -> os.stat_result:
        return stale if path == target else real_lstat(path)

    monkeypatch.setattr(Path, "lstat", stale_lstat)


SWAPS: dict[str, Callable[[Path, bytes], None]] = {
    "symlink": lambda target, data: _symlink_to_copy(target, data),
    "fifo": lambda target, _data: os.mkfifo(target),
}


def _symlink_to_copy(target: Path, data: bytes) -> None:
    """Links `target` to an outside copy of its bytes, so following it would pass the digest."""
    outside = target.parent.parent.parent / "outside-copy"
    outside.write_bytes(data)
    target.symlink_to(outside)


SWAP_MESSAGES = {"symlink": "symbolic link", "fifo": "not a regular file"}


@pytest.mark.skipif(sys.platform == "win32", reason="needs O_NOFOLLOW, FIFOs and SIGALRM")
@pytest.mark.parametrize("kind", sorted(SWAPS))
@fails_after(5)
def test_regression_object_swapped_after_lstat_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Refuses a symlink or FIFO swapped in after lstat, without following or blocking on it."""
    repo, rev, base = _saved(tmp_path)
    target = base / "objects" / repo.backend.tree(SHOW_ID, rev)[SHOW_DOC]
    data = target.read_bytes()
    _swapped_after_lstat(monkeypatch, target, lambda path: SWAPS[kind](path, data))
    with pytest.raises(StorageError) as caught:
        repo.read(SHOW_ID, SHOW_DOC)
    assert caught.value.code == CORRUPT
    assert SWAP_MESSAGES[kind] in caught.value.message


STORED_FILES: dict[str, Callable[[Path, str], Path]] = {
    "link": lambda base, _rev: base / "next" / "root",
    "record": lambda base, rev: base / "trees" / f"{rev}.json",
}


NOT_REGULAR = "not a regular file"


def _fifo_filled_with(content: bytes) -> Callable[[Path], None]:
    """Makes a FIFO that a writer thread fills with `content` once a reader opens it."""

    def swap(target: Path) -> None:
        os.mkfifo(target)

        def fill() -> None:
            with contextlib.suppress(BrokenPipeError), target.open("wb") as writer:
                writer.write(content)

        threading.Thread(target=fill, daemon=True).start()

    return swap


@pytest.mark.skipif(sys.platform == "win32", reason="needs O_NOFOLLOW, FIFOs and SIGALRM")
@pytest.mark.parametrize("kind", sorted(STORED_FILES))
@fails_after(5)
def test_regression_fifo_swapped_in_after_lstat_is_refused_by_fstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Refuses a FIFO that holds valid content, naming a non-regular file, via the fstat check."""
    repo, rev, base = _saved(tmp_path)
    target = STORED_FILES[kind](base, rev)
    _swapped_after_lstat(monkeypatch, target, _fifo_filled_with(target.read_bytes()))
    with pytest.raises(StorageError) as caught:
        repo.backend.log(SHOW_ID)
    assert caught.value.code == CORRUPT
    assert NOT_REGULAR in caught.value.message


@pytest.mark.skipif(sys.platform == "win32", reason="needs O_NOFOLLOW, FIFOs and SIGALRM")
@pytest.mark.parametrize("kind", sorted(STORED_FILES))
@fails_after(5)
def test_regression_plain_fifo_as_link_or_record_is_corrupt_without_blocking(
    tmp_path: Path, kind: str
) -> None:
    """Refuses a FIFO standing in for a next/ link or a tree record, never blocking on it."""
    repo, rev, base = _saved(tmp_path)
    target = STORED_FILES[kind](base, rev)
    target.unlink()
    os.mkfifo(target)
    with pytest.raises(StorageError) as caught:
        repo.backend.log(SHOW_ID)
    assert caught.value.code == CORRUPT
    assert NOT_REGULAR in caught.value.message


ERROR_INVALID_NAME = 123
OVERLONG_ID = "a" * 300


class _WindowsInvalidNameError(OSError):
    """Stands in for the error Windows raises for an unusable path component."""

    winerror = ERROR_INVALID_NAME


def _windows_invalid_name_for(monkeypatch: pytest.MonkeyPatch, component: str) -> None:
    """Makes os.lstat fail as Windows does for any path containing `component`."""
    real_lstat = os.lstat

    def lstat(path: str | Path, **kwargs: Any) -> os.stat_result:
        if component in Path(path).parts:
            raise _WindowsInvalidNameError(errno.EINVAL, "Invalid argument", str(path))
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)


def test_regression_windows_overlong_component_is_name_too_long(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maps the Windows invalid-name error for a 300-character component to NAME_TOO_LONG."""
    repo, _, _ = _saved(tmp_path)
    _windows_invalid_name_for(monkeypatch, OVERLONG_ID)
    for call in (
        lambda: repo.revisions(OVERLONG_ID),
        lambda: repo.put_show(OVERLONG_ID, two_set_show(), expected_revision="", at=AT),
    ):
        with pytest.raises(StorageError) as caught:
            call()
        assert caught.value.code == "NAME_TOO_LONG"
        assert caught.value.context["errno"] == errno.ENAMETOOLONG
        assert caught.value.context["winerror"] == ERROR_INVALID_NAME


def test_regression_windows_invalid_short_name_stays_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeps the Windows invalid-name error IO when no path component is too long."""
    repo, _, _ = _saved(tmp_path)
    _windows_invalid_name_for(monkeypatch, SHOW_ID)
    with pytest.raises(StorageError) as caught:
        repo.revisions(SHOW_ID)
    assert caught.value.code == "IO"


def test_regression_file_for_a_directory_is_io_where_the_os_reports_it_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reports a file standing in for trees/ as ENOTDIR where the OS calls the path missing."""
    repo, _, base = _saved(tmp_path)
    shutil.rmtree(base / "trees")
    (base / "trees").write_bytes(b"not a directory")
    real_lstat = Path.lstat

    def windows_lstat(path: Path) -> os.stat_result:
        if path.parent == base / "trees":
            raise FileNotFoundError(errno.ENOENT, "The system cannot find the path", str(path))
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", windows_lstat)
    with pytest.raises(StorageError) as caught:
        repo.revisions(SHOW_ID)
    assert caught.value.code == "IO"
    assert caught.value.context["errno"] == errno.ENOTDIR


def _refuse_replacing_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes Path.replace refuse an existing target, as Windows does while a racer holds it."""
    real_replace = Path.replace

    def windows_replace(source: Path, destination: str | Path) -> Path:
        if Path(destination).exists():
            raise PermissionError(errno.EACCES, "Access is denied", str(destination))
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", windows_replace)


def test_regression_blob_landed_by_a_racing_writer_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commits when a racing writer lands the same blob first and the OS refuses to replace it."""
    repo, rev, base = _saved(tmp_path)
    content = b"x"
    target = base / "objects" / digest(content)
    real_lstat_mode = file_repository._lstat_mode

    def racing_lstat_mode(show_id: str, path: Path) -> int | None:
        mode = real_lstat_mode(show_id, path)
        if path == target and mode is None:
            target.write_bytes(content)
        return mode

    monkeypatch.setattr(file_repository, "_lstat_mode", racing_lstat_mode)
    _refuse_replacing_existing(monkeypatch)
    landed = repo.transaction(SHOW_ID, {"/renders/a.txt": content}, expected_revision=rev, at=AT)
    assert repo.head(SHOW_ID) == landed.id
    assert repo.read(SHOW_ID, "/renders/a.txt") == content


def test_regression_tree_record_landed_by_an_identical_commit_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commits when an identical commit wrote the same tree record first; replacing it fails."""
    twin_repo, twin_rev, twin_base = _saved(tmp_path / "twin")
    twin = _second_revision(twin_repo, twin_rev)
    repo, rev, base = _saved(tmp_path)
    assert rev == twin_rev
    shutil.copy(twin_base / "trees" / f"{twin}.json", base / "trees" / f"{twin}.json")
    _refuse_replacing_existing(monkeypatch)
    assert _second_revision(repo, rev) == twin
    assert repo.head(SHOW_ID) == twin


def _plant_other_tree(record: Path, data: bytes) -> None:
    tampered = json.loads(data)
    tampered["tree"]["/renders/a.txt"] = tampered["tree"][SHOW_DOC]
    record.write_text(json.dumps(tampered))


def _plant_truncated(record: Path, _data: bytes) -> None:
    record.write_bytes(b"{")


def _plant_symlink(record: Path, data: bytes) -> None:
    outside = record.parent.parent.parent / "outside-record.json"
    outside.write_bytes(data)
    record.symlink_to(outside)


PLANTED_RECORDS: dict[str, Callable[[Path, bytes], None]] = {
    "other tree": _plant_other_tree,
    "truncated": _plant_truncated,
    "symlink": _plant_symlink,
}


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(kind, marks=NO_SYMLINKS) if kind == "symlink" else kind
        for kind in sorted(PLANTED_RECORDS)
    ],
)
def test_regression_planted_tree_record_blocks_the_commit(tmp_path: Path, kind: str) -> None:
    """Refuses a commit whose record name holds something else, before linking next/."""
    twin_repo, twin_rev, twin_base = _saved(tmp_path / "twin")
    twin = _second_revision(twin_repo, twin_rev)
    repo, rev, base = _saved(tmp_path)
    record = base / "trees" / f"{twin}.json"
    PLANTED_RECORDS[kind](record, (twin_base / "trees" / f"{twin}.json").read_bytes())
    _assert_corrupt(lambda: _second_revision(repo, rev))
    assert not (base / "next" / rev).exists()


def test_regression_failed_temp_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removes the temp file when syncing it to disk fails."""
    repo, rev, base = _saved(tmp_path)

    def fail_sync(_fd: int) -> NoReturn:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(StorageError) as caught:
        _second_revision(repo, rev)
    assert caught.value.code == "IO"
    assert not list(base.rglob(".tmp-*"))


def test_regression_tree_at_walks_the_log_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, head, _ = _saved(tmp_path)
    for n in range(RENDER_COMMITS):
        head = repo.transaction(
            SHOW_ID, {f"/renders/{n}.txt": str(n).encode()}, expected_revision=head, at=AT
        ).id
    backend: Any = repo.backend
    parse: Callable[[str, str, Path], dict[str, object]] = backend._record
    parsed: list[str] = []

    def counted(show_id: str, revision_id: str, source: Path) -> dict[str, object]:
        parsed.append(revision_id)
        return parse(show_id, revision_id, source)

    ws = open_workspace(repo, SHOW_ID)
    monkeypatch.setattr(backend, "_record", counted)
    tree = ws.tree_at(head)
    parses = len(parsed)
    assert len(tree) == len(repo.paths(SHOW_ID, head))
    assert parses <= len(repo.revisions(SHOW_ID)) + 1


def test_regression_commit_refuses_to_land_over_a_damaged_existing_blob(tmp_path: Path) -> None:
    repo, head, base = _saved(tmp_path)
    (base / "objects" / digest(b"x")).write_bytes(b"garbage")
    records = sorted((base / "trees").iterdir())
    with pytest.raises(StorageError) as caught:
        repo.transaction(SHOW_ID, {"/renders/a.txt": b"x"}, expected_revision=head, at=AT)
    assert caught.value.code == "CORRUPT"
    assert repo.head(SHOW_ID) == head
    assert sorted((base / "trees").iterdir()) == records


@NO_SYMLINKS
def test_regression_commit_refuses_a_symlink_at_a_blob_target(tmp_path: Path) -> None:
    repo, head, base = _saved(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    (base / "objects" / digest(b"x")).symlink_to(outside)
    records = sorted((base / "trees").iterdir())
    _assert_corrupt(
        lambda: repo.transaction(SHOW_ID, {"/renders/a.txt": b"x"}, expected_revision=head, at=AT)
    )
    assert repo.head(SHOW_ID) == head
    assert sorted((base / "trees").iterdir()) == records
    assert outside.read_bytes() == b"x"
