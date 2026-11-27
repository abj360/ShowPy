#!/usr/bin/env python3

"""
test_durable_io_create.py --- tests the exclusive create that places files without hard links

Contains:
    test_exclusive_create_never_replaces_a_file: keeps an existing file and the target of an
        existing symlink untouched
    test_exclusive_create_writes_once_and_flushes: writes the bytes in one call and flushes them
    test_failed_or_short_exclusive_write_removes_the_file: raises and leaves no file for a write
        that fails or writes less than asked
    test_failed_slot_write_without_hard_links_leaves_no_torn_slot: fails the commit with IO and
        removes the slot it created
    test_commit_stopped_at_each_create_seam_leaves_what_readers_expect: leaves no slot when stopped
        at the create and an empty slot readers report as torn when stopped at the write, with
        no descriptor left open
"""

import errno
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.errors import StorageError
from e8s.show import durable_io
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
SHOW_ID = "created"
RENDER = "/renders/a.txt"
REAL_WRITE = os.write


def _change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    data = f"render {n}".encode()
    revision = Revision(
        id=revision_id(parent, AT, {RENDER: digest(data)}), parent=parent, at=AT, changed=(RENDER,)
    )
    return revision, {RENDER: digest(data)}, {digest(data): data}


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs privilege")
def test_exclusive_create_never_replaces_a_file(tmp_path: Path) -> None:
    existing, aimed, linked = tmp_path / "existing", tmp_path / "aimed", tmp_path / "linked"
    existing.write_bytes(b"old")
    aimed.write_bytes(b"target")
    linked.symlink_to(aimed)
    assert durable_io.create_new(existing, b"new") is False
    assert durable_io.create_new(linked, b"new") is False
    assert existing.read_bytes() == b"old"
    assert aimed.read_bytes() == b"target"
    assert linked.is_symlink()


def _count_writes(
    monkeypatch: pytest.MonkeyPatch, result: Callable[[int, bytes], int]
) -> list[int]:
    """Replaces os.write, recording the size of each write and returning `result` for it."""
    sizes: list[int] = []

    def write(descriptor: int, data: bytes) -> int:
        sizes.append(len(data))
        return result(descriptor, data)

    monkeypatch.setattr(os, "write", write)
    return sizes


def test_exclusive_create_writes_once_and_flushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed: list[int] = []
    monkeypatch.setattr(durable_io, "flush_file", flushed.append)
    sizes = _count_writes(monkeypatch, os.write)
    target = tmp_path / "slot"
    assert durable_io.create_new(target, b"x" * 5000) is True
    assert sizes == [5000]
    assert len(flushed) == 1
    assert target.read_bytes() == b"x" * 5000
    assert target.stat().st_mode & 0o777 == durable_io.CREATED_FILE_MODE or sys.platform == "win32"


def _fail_write(_descriptor: int, _data: bytes) -> int:
    raise OSError(errno.ENOSPC, "No space left on device")


def _short_write(descriptor: int, data: bytes) -> int:
    return REAL_WRITE(descriptor, data[:-1])


@pytest.mark.parametrize(
    ("write", "reason"),
    [(_fail_write, "No space left"), (_short_write, "wrote 6 of 7 bytes")],
    ids=["fails", "short"],
)
def test_failed_or_short_exclusive_write_removes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write: Callable[[int, bytes], int], reason: str
) -> None:
    _count_writes(monkeypatch, write)
    target = tmp_path / "slot"
    with pytest.raises(OSError, match=reason):
        durable_io.create_new(target, b"payload")
    assert not target.exists()


def test_failed_slot_write_without_hard_links_leaves_no_torn_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    backend = FileBackend(tmp_path)
    first, first_tree, first_blobs = _change("", 1)
    backend.commit(SHOW_ID, first, first_tree, first_blobs)
    slot = tmp_path / "shows" / show_folder(SHOW_ID) / "log" / "000000000002"
    real_create_new = durable_io.create_new

    def create_new(target: Path, data: bytes) -> bool:
        if target == slot:
            _count_writes(monkeypatch, _fail_write)
        return real_create_new(target, data)

    monkeypatch.setattr(durable_io, "create_new", create_new)
    second, second_tree, second_blobs = _change(first.id, 2)
    with pytest.raises(StorageError) as caught:
        backend.commit(SHOW_ID, second, second_tree, second_blobs)
    assert caught.value.code == "IO"
    assert caught.value.context["errno"] == errno.ENOSPC
    assert not slot.exists()


class _Stopped(BaseException):
    """Stands in for a process that dies between two file-system calls."""


def _stop_at(monkeypatch: pytest.MonkeyPatch, seam: str, path: Path) -> list[int]:
    """Makes the durable_io function `seam` stop the process when it reaches `path`.

    Returns:
        descriptors: The descriptors the stopped call was given, which must not stay open.
    """
    real = getattr(durable_io, seam)
    descriptors: list[int] = []

    def stopping(*args: object) -> object:
        if path in args:
            descriptors.extend(arg for arg in args if isinstance(arg, int))
            raise _Stopped
        return real(*args)

    monkeypatch.setattr(durable_io, seam, stopping)
    return descriptors


def _is_open(descriptor: int) -> bool:
    try:
        os.fstat(descriptor)
    except OSError:
        return False
    return True


@pytest.mark.parametrize(("seam", "code"), [("open_new", None), ("write_new", "TORN_SLOT")])
def test_commit_stopped_at_each_create_seam_leaves_what_readers_expect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str, code: str | None
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    backend = FileBackend(tmp_path)
    first, first_tree, first_blobs = _change("", 1)
    backend.commit(SHOW_ID, first, first_tree, first_blobs)
    slot = tmp_path / "shows" / show_folder(SHOW_ID) / "log" / "000000000002"
    descriptors = _stop_at(monkeypatch, seam, slot)
    second, second_tree, second_blobs = _change(first.id, 2)
    with pytest.raises(_Stopped):
        backend.commit(SHOW_ID, second, second_tree, second_blobs)
    assert not any(_is_open(descriptor) for descriptor in descriptors)
    if code is None:
        assert not slot.exists()
        assert FileBackend(tmp_path).log(SHOW_ID) == [first]
        return
    assert len(descriptors) == 1
    assert slot.read_bytes() == b""
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).log(SHOW_ID)
    assert caught.value.code == code
