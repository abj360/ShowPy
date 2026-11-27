#!/usr/bin/env python3

"""
test_durable_io.py --- tests the flushes a layout-2 commit makes before its slot lands

Contains:
    test_first_commit_flushes_every_directory_it_creates_before_the_slot: flushes the layout
        entry's folder, shows/ and the show folder after the first files and before the slot
    test_a_second_backend_on_an_existing_root_flushes_the_chain_before_its_commit: flushes the
        same folders in a process that did not create them
    test_commit_flushes_each_file_and_directory_before_its_slot: flushes blobs, record and their
        directories before the slot, which gets a full flush
    test_directory_flush_is_skipped_on_windows: opens no directory on Windows
    test_directory_flush_opens_without_following_links_and_syncs: opens the folder with
        O_NOFOLLOW and syncs its descriptor
    test_macos_flushes_files_and_directories_with_a_barrier_and_the_slot_directory_fully: picks
        the fcntl command per flush on macOS
    test_full_flush_command_is_the_one_the_os_defines: matches F_FULLFSYNC to the fcntl module
    test_other_systems_flush_every_file_with_fsync: calls fsync for blobs, records and slots
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    tree = {RENDER: digest(data)}
    revision = Revision(id=revision_id(parent, AT, tree), parent=parent, at=AT, changed=(RENDER,))
    return revision, tree, {digest(data): data}


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

    def recording_flush_directory(path: Path, *, is_commit_point: bool = False) -> None:
        calls.append((DIRECTORY_ENTRY, path.name, COMMIT_POINT if is_commit_point else ""))
        real_flush_directory(path, is_commit_point=is_commit_point)

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
    opened: list[tuple[Path, int]] = []
    synced: list[int] = []
    monkeypatch.setattr(durable_io, "IS_WINDOWS", False)
    monkeypatch.setattr(durable_io, "IS_MACOS", False)

    def fake_open(path: Path, flags: int) -> int:
        opened.append((path, flags))
        return 99

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", synced.append)
    monkeypatch.setattr(os, "close", lambda _descriptor: None)
    flush_directory(tmp_path)
    assert synced == [99]
    assert opened[0][0] == tmp_path
    assert opened[0][1] & getattr(os, "O_NOFOLLOW", 0) == getattr(os, "O_NOFOLLOW", 0)


def test_macos_flushes_files_and_directories_with_a_barrier_and_the_slot_directory_fully(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[int] = []
    monkeypatch.setattr(durable_io, "IS_MACOS", True)
    monkeypatch.setattr(durable_io, "IS_WINDOWS", False)
    monkeypatch.setattr(durable_io, "fcntl_flush", lambda _fd, command: commands.append(command))
    flush_file(0)
    flush_directory(tmp_path)
    flush_directory(tmp_path, is_commit_point=True)
    assert commands == [BARRIER_FSYNC, BARRIER_FSYNC, durable_io.F_FULLFSYNC]


def test_full_flush_command_is_the_one_the_os_defines() -> None:
    fcntl = pytest.importorskip("fcntl")
    if not hasattr(fcntl, "F_FULLFSYNC"):
        pytest.skip("fcntl has no F_FULLFSYNC on this system")
    assert durable_io.F_FULLFSYNC == fcntl.F_FULLFSYNC


def test_other_systems_flush_every_file_with_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    monkeypatch.setattr(durable_io, "IS_MACOS", False)
    monkeypatch.setattr(os, "fsync", synced.append)
    flush_file(7)
    assert synced == [7]
