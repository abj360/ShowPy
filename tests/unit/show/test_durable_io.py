#!/usr/bin/env python3

"""
test_durable_io.py --- tests the flushes a layout-2 commit makes before its slot lands

Contains:
    test_commit_flushes_each_file_and_directory_before_its_slot: flushes blobs, record and their
        directories before the slot, which gets a full flush
    test_first_commit_flushes_every_directory_it_creates_before_the_slot: flushes the layout entry's
        folder, shows/ and the show folder after the first files and before the slot
    test_a_second_backend_on_an_existing_root_flushes_the_chain_before_its_commit: flushes the same
        folders in a process that did not create them
    test_failed_macos_flush_fails_the_commit_with_io: maps a failing fcntl flush to IO and lands no
        slot
    test_failed_directory_flush_fails_the_commit_with_io: maps a failing directory fsync to IO and
        lands no slot
    test_a_failed_chain_flush_is_retried_by_the_next_commit: flushes the chain again after a failed
        first flush
    test_regression_commit_on_a_symlinked_root_lands: commits and reads back through a root that is
        a symlink
    test_directory_flush_is_skipped_on_windows: opens no directory on Windows
    test_directory_flush_opens_without_following_links_and_syncs: opens the folder with O_NOFOLLOW
        and syncs its descriptor
    test_macos_flushes_files_and_directories_with_a_barrier_and_the_slot_directory_fully: picks the
        fcntl command per flush on macOS
    test_full_flush_command_is_the_one_the_os_defines: matches F_FULLFSYNC to the fcntl module
    test_a_failing_fcntl_flush_raises_instead_of_being_swallowed: raises OSError for a closed
        descriptor
    test_other_systems_flush_every_file_with_fsync: calls fsync on the descriptor
"""

import errno
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.show import durable_io
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.durable_io import flush_directory, flush_file
from e8s.show.file_backend import FileBackend
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SHOW_ID = "flushes"
RENDER = "/renders/a.txt"
FILE_ENTRY = "file"
LINK_ENTRY = "link"
DIRECTORY_ENTRY = "dir"
BARRIER_FSYNC = 85
COMMIT_POINT = "commit point"
FAKE_DIRECTORY_DESCRIPTOR = -99


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    tree = {RENDER: digest(data)}
    revision = Revision(id=revision_id(parent, AT, tree), parent=parent, at=AT, changed=(RENDER,))
    return revision, tree, {digest(data): data}


def _fake_directory_open(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, int]]:
    """Fakes only the opens of folders, so no real folder is opened and files still are.

    Returns:
        opened: Path and flags of each faked folder open, in call order.
    """
    opened: list[tuple[Path, int]] = []
    real_open, real_close = os.open, os.close

    def fake_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if not Path(path).is_dir():
            return real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append((Path(path), flags))
        return FAKE_DIRECTORY_DESCRIPTOR

    def fake_close(descriptor: int) -> None:
        if descriptor != FAKE_DIRECTORY_DESCRIPTOR:
            real_close(descriptor)

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "close", fake_close)
    return opened


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    real_flush_file, real_flush_directory = durable_io.flush_file, durable_io.flush_directory
    real_link_new = durable_io.link_new

    def recording_flush_file(descriptor: int) -> None:
        calls.append((FILE_ENTRY,))
        real_flush_file(descriptor)

    def recording_link_new(source: Path, target: Path) -> bool:
        calls.append((LINK_ENTRY, target.parent.name))
        return real_link_new(source, target)

    def recording_flush_directory(
        path: Path, *, is_commit_point: bool = False, follows_links: bool = False
    ) -> None:
        calls.append((DIRECTORY_ENTRY, path.name, COMMIT_POINT if is_commit_point else ""))
        real_flush_directory(path, is_commit_point=is_commit_point, follows_links=follows_links)

    monkeypatch.setattr(durable_io, "flush_file", recording_flush_file)
    monkeypatch.setattr(durable_io, "link_new", recording_link_new)
    monkeypatch.setattr(durable_io, "flush_directory", recording_flush_directory)
    return calls


def test_commit_flushes_each_file_and_directory_before_its_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FileBackend(tmp_path)
    first, first_tree, first_blobs = _change("", 1)
    backend.commit(SHOW_ID, first, first_tree, first_blobs)
    calls = _record_calls(monkeypatch)
    second, second_tree, second_blobs = _change(first.id, 2)
    backend.commit(SHOW_ID, second, second_tree, second_blobs)
    assert calls == [
        (FILE_ENTRY,),
        (LINK_ENTRY, "objects"),
        (FILE_ENTRY,),
        (LINK_ENTRY, "records"),
        (DIRECTORY_ENTRY, "objects", ""),
        (DIRECTORY_ENTRY, "records", ""),
        (FILE_ENTRY,),
        (LINK_ENTRY, "log"),
        (DIRECTORY_ENTRY, "log", COMMIT_POINT),
    ]


def test_first_commit_flushes_every_directory_it_creates_before_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_calls(monkeypatch)
    revision, tree, blobs = _change("", 1)
    FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert calls[:8] == [
        (FILE_ENTRY,),
        (LINK_ENTRY, tmp_path.name),
        (FILE_ENTRY,),
        (LINK_ENTRY, show_folder(SHOW_ID)),
        (DIRECTORY_ENTRY, tmp_path.name, ""),
        (DIRECTORY_ENTRY, "shows", ""),
        (DIRECTORY_ENTRY, show_folder(SHOW_ID), ""),
        (FILE_ENTRY,),
    ]
    assert calls[-3:] == [
        (FILE_ENTRY,),
        (LINK_ENTRY, "log"),
        (DIRECTORY_ENTRY, "log", COMMIT_POINT),
    ]


def test_a_second_backend_on_an_existing_root_flushes_the_chain_before_its_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, first_tree, first_blobs = _change("", 1)
    FileBackend(tmp_path).commit(SHOW_ID, first, first_tree, first_blobs)
    calls = _record_calls(monkeypatch)
    second, second_tree, second_blobs = _change(first.id, 2)
    FileBackend(tmp_path).commit(SHOW_ID, second, second_tree, second_blobs)
    chain = [
        (DIRECTORY_ENTRY, tmp_path.name, ""),
        (DIRECTORY_ENTRY, "shows", ""),
        (DIRECTORY_ENTRY, show_folder(SHOW_ID), ""),
    ]
    start = calls.index(chain[0])
    assert calls[start : start + len(chain)] == chain
    assert start < calls.index((LINK_ENTRY, "log"))


def test_failed_macos_flush_fails_the_commit_with_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fcntl(_descriptor: int, _command: int) -> None:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(durable_io, "IS_MACOS", True)
    monkeypatch.setattr(durable_io, "fcntl_flush", failing_fcntl)
    revision, tree, blobs = _change("", 1)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert caught.value.code == "IO"
    assert not list((tmp_path / "shows").glob("*/log/*"))


def test_failed_directory_flush_fails_the_commit_with_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed: list[int] = []
    real_fsync = os.fsync

    def failing_directory_fsync(descriptor: int) -> None:
        if descriptor != FAKE_DIRECTORY_DESCRIPTOR:
            real_fsync(descriptor)
            return
        failed.append(descriptor)
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(durable_io, "IS_MACOS", False)
    monkeypatch.setattr(durable_io, "IS_WINDOWS", False)
    _fake_directory_open(monkeypatch)
    monkeypatch.setattr(os, "fsync", failing_directory_fsync)
    revision, tree, blobs = _change("", 1)
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).commit(SHOW_ID, revision, tree, blobs)
    assert failed == [FAKE_DIRECTORY_DESCRIPTOR]
    assert caught.value.code == "IO"
    assert not list((tmp_path / "shows").glob("*/log/*"))


def test_a_failed_chain_flush_is_retried_by_the_next_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_flush_directory = durable_io.flush_directory

    def failing_flush_directory(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EIO, "Input/output error")

    backend = FileBackend(tmp_path)
    revision, tree, blobs = _change("", 1)
    monkeypatch.setattr(durable_io, "flush_directory", failing_flush_directory)
    with pytest.raises(StorageError):
        backend.commit(SHOW_ID, revision, tree, blobs)
    monkeypatch.setattr(durable_io, "flush_directory", real_flush_directory)
    calls = _record_calls(monkeypatch)
    backend.commit(SHOW_ID, revision, tree, blobs)
    assert (DIRECTORY_ENTRY, show_folder(SHOW_ID), "") in calls


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privilege on Windows")
def test_regression_commit_on_a_symlinked_root_lands(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    revision, tree, blobs = _change("", 1)
    backend = FileBackend(link)
    backend.commit(SHOW_ID, revision, tree, blobs)
    assert backend.head(SHOW_ID) == revision.id


def test_directory_flush_is_skipped_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refusing_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("a directory was opened")

    monkeypatch.setattr(durable_io, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "open", refusing_open)
    flush_directory(tmp_path)


def test_directory_flush_opens_without_following_links_and_syncs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    monkeypatch.setattr(durable_io, "IS_WINDOWS", False)
    monkeypatch.setattr(durable_io, "IS_MACOS", False)
    opened = _fake_directory_open(monkeypatch)
    monkeypatch.setattr(os, "fsync", synced.append)
    flush_directory(tmp_path)
    assert synced == [FAKE_DIRECTORY_DESCRIPTOR]
    assert opened[0][0] == tmp_path
    assert opened[0][1] & getattr(os, "O_NOFOLLOW", 0) == getattr(os, "O_NOFOLLOW", 0)


def test_macos_flushes_files_and_directories_with_a_barrier_and_the_slot_directory_fully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[int] = []
    monkeypatch.setattr(durable_io, "IS_MACOS", True)
    monkeypatch.setattr(durable_io, "IS_WINDOWS", False)
    monkeypatch.setattr(durable_io, "fcntl_flush", lambda _fd, command: commands.append(command))
    _fake_directory_open(monkeypatch)
    flush_file(0)
    flush_directory(tmp_path)
    flush_directory(tmp_path, is_commit_point=True)
    assert commands == [BARRIER_FSYNC, BARRIER_FSYNC, durable_io.F_FULLFSYNC]


def test_full_flush_command_is_the_one_the_os_defines() -> None:
    fcntl = pytest.importorskip("fcntl")
    if not hasattr(fcntl, "F_FULLFSYNC"):
        pytest.skip("fcntl has no F_FULLFSYNC on this system")
    assert durable_io.F_FULLFSYNC == fcntl.F_FULLFSYNC


def test_a_failing_fcntl_flush_raises_instead_of_being_swallowed() -> None:
    pytest.importorskip("fcntl")
    closed = os.open(os.devnull, os.O_RDONLY)
    os.close(closed)
    with pytest.raises(OSError, match="Bad file descriptor") as caught:
        durable_io.fcntl_flush(closed, durable_io.F_FULLFSYNC)
    assert caught.value.errno == errno.EBADF


def test_other_systems_flush_every_file_with_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    monkeypatch.setattr(durable_io, "IS_MACOS", False)
    monkeypatch.setattr(os, "fsync", synced.append)
    flush_file(7)
    assert synced == [7]
