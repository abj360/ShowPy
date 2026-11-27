#!/usr/bin/env python3

"""
test_durable_io.py --- tests the flushes a layout-2 commit makes before its slot lands

Contains:
    test_commit_flushes_each_file_and_directory_before_its_slot: flushes blobs, record and their
        directories before the slot, which gets a full flush
    test_directory_flush_is_skipped_on_windows: opens no directory on Windows
    test_macos_flushes_with_a_barrier_and_the_commit_point_with_a_full_flush: picks the fcntl
        command per file on macOS
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

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SHOW_ID = "flushes"
RENDER = "/renders/a.txt"
FILE_ENTRY = "file"
LINK_ENTRY = "link"
DIRECTORY_ENTRY = "dir"
BARRIER_FSYNC = 85


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    tree = {RENDER: digest(data)}
    revision = Revision(id=revision_id(parent, AT, tree), parent=parent, at=AT, changed=(RENDER,))
    return revision, tree, {digest(data): data}


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | bool]]:
    calls: list[tuple[str, str | bool]] = []
    real_flush_file, real_flush_directory = durable_io.flush_file, durable_io.flush_directory
    real_link_new = durable_io.link_new

    def recording_flush_file(descriptor: int, *, is_commit_point: bool) -> None:
        calls.append((FILE_ENTRY, is_commit_point))
        real_flush_file(descriptor, is_commit_point=is_commit_point)

    def recording_link_new(source: Path, target: Path) -> bool:
        calls.append((LINK_ENTRY, target.parent.name))
        return real_link_new(source, target)

    def recording_flush_directory(path: Path) -> None:
        calls.append((DIRECTORY_ENTRY, path.name))
        real_flush_directory(path)

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
        (FILE_ENTRY, False),
        (LINK_ENTRY, "objects"),
        (FILE_ENTRY, False),
        (LINK_ENTRY, "records"),
        (DIRECTORY_ENTRY, "objects"),
        (DIRECTORY_ENTRY, "records"),
        (FILE_ENTRY, True),
        (LINK_ENTRY, "log"),
        (DIRECTORY_ENTRY, "log"),
    ]


def test_directory_flush_is_skipped_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refusing_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("a directory was opened")

    monkeypatch.setattr(durable_io, "IS_WINDOWS", True)
    monkeypatch.setattr(os, "open", refusing_open)
    flush_directory(tmp_path)


def test_directory_flush_syncs_an_existing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[int] = []
    monkeypatch.setattr(durable_io, "IS_WINDOWS", False)
    monkeypatch.setattr(durable_io, "IS_MACOS", False)
    monkeypatch.setattr(os, "fsync", synced.append)
    flush_directory(tmp_path)
    assert len(synced) == 1


def test_macos_flushes_with_a_barrier_and_the_commit_point_with_a_full_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[int] = []
    monkeypatch.setattr(durable_io, "IS_MACOS", True)
    monkeypatch.setattr(durable_io, "fcntl_flush", lambda _fd, command: commands.append(command))
    flush_file(0, is_commit_point=False)
    flush_file(0, is_commit_point=True)
    assert commands == [BARRIER_FSYNC, durable_io.F_FULLFSYNC]


def test_other_systems_flush_every_file_with_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[int] = []
    monkeypatch.setattr(durable_io, "IS_MACOS", False)
    monkeypatch.setattr(os, "fsync", synced.append)
    flush_file(7, is_commit_point=False)
    flush_file(8, is_commit_point=True)
    assert synced == [7, 8]
