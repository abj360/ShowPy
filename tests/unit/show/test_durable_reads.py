#!/usr/bin/env python3

"""
test_durable_reads.py --- tests comparing two stored files in bounded pieces

Contains:
    test_same_file_under_two_names_has_the_same_bytes: answers from the inode, reading nothing
    test_same_bytes_compares_every_piece: tells equal from unequal files of one size, the
        difference in the last piece
    test_same_bytes_reads_each_file_a_piece_at_a_time: never asks either file for more than
        COMPARE_PIECE_BYTES in one read
    test_regression_same_bytes_never_trusts_an_inode_of_zero: compares the bytes of two files
        whose file system reports inode 0 for both
    test_regression_same_bytes_reads_two_files_that_share_a_reused_inode: compares the bytes
        when the second name holds a file given the first one's freed inode
    test_same_bytes_refuses_what_is_not_a_regular_file: answers False for a missing file, a
        folder, a symlink and files of different sizes
    test_same_bytes_refuses_a_file_swapped_after_its_lookup: answers False for a link, a FIFO or
        a vanished file found in place of a regular file once it is opened
    test_same_bytes_refuses_two_fifos_found_in_place_of_both_files: answers False when both
        names hold FIFOs once opened, never blocking on them
    test_regression_same_bytes_refuses_a_folder_found_in_place_of_a_file: answers False and
        closes every descriptor when a folder holds either name once opened
"""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import IO

import pytest

from e8s.show import durable_reads
from e8s.show.durable_reads import has_same_bytes
from tests.fixtures.fsck_repos import with_stat, with_zero_inode

PIECE = 4


def test_same_file_under_two_names_has_the_same_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"same")
    os.link(first, second)

    def no_open(*_args: object) -> int:
        raise AssertionError("opened one file twice to compare it with itself")

    monkeypatch.setattr(durable_reads, "_open_no_follow", no_open)
    assert has_same_bytes(first, second) is True


def test_same_bytes_compares_every_piece(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(durable_reads, "COMPARE_PIECE_BYTES", PIECE)
    base = b"abcd" * 5
    first, same, other = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    first.write_bytes(base)
    same.write_bytes(base)
    other.write_bytes(base[:-1] + b"X")
    assert has_same_bytes(first, same) is True
    assert has_same_bytes(first, other) is False


class _RecordingReader:
    """Wraps an open file, recording the size each read asks for."""

    def __init__(self, handle: IO[bytes], sizes: list[int]) -> None:
        self._handle = handle
        self._sizes = sizes

    def __enter__(self) -> "_RecordingReader":
        return self

    def __exit__(self, *_exc: object) -> None:
        self._handle.close()

    def fileno(self) -> int:
        return self._handle.fileno()

    def read(self, size: int = -1) -> bytes:
        self._sizes.append(size)
        return self._handle.read(size)


def test_same_bytes_reads_each_file_a_piece_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(durable_reads, "COMPARE_PIECE_BYTES", PIECE)
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"abcd" * 5)
    second.write_bytes(b"abcd" * 5)
    sizes: list[int] = []
    real_fdopen = os.fdopen

    def recording_fdopen(descriptor: int, mode: str = "r") -> _RecordingReader:
        return _RecordingReader(real_fdopen(descriptor, mode), sizes)

    monkeypatch.setattr(os, "fdopen", recording_fdopen)
    assert has_same_bytes(first, second) is True
    assert len(sizes) >= 2 * 5
    assert all(0 < size <= PIECE for size in sizes)


def test_regression_same_bytes_never_trusts_an_inode_of_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python documents st_ino as unique only when non-zero; some file systems report 0."""
    first, same, other = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    first.write_bytes(b"good bytes")
    same.write_bytes(b"good bytes")
    other.write_bytes(b"other byte")
    lookup = durable_reads.lstat_info

    def zero_inode(show_id: str, path: Path) -> os.stat_result | None:
        info = lookup(show_id, path)
        return None if info is None else with_zero_inode(info)

    monkeypatch.setattr(durable_reads, "lstat_info", zero_inode)
    assert has_same_bytes(first, other) is False
    assert has_same_bytes(first, same) is True


def test_regression_same_bytes_reads_two_files_that_share_a_reused_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first file is freed between the two lookups and the second gets its inode."""
    first, other = tmp_path / "a", tmp_path / "c"
    first.write_bytes(b"good bytes")
    other.write_bytes(b"other byte")
    one = os.lstat(first)
    lookup = durable_reads.lstat_info

    def reused_inode(show_id: str, path: Path) -> os.stat_result | None:
        info = lookup(show_id, path)
        if info is None or path != other:
            return info
        return with_stat(info, st_dev=one.st_dev, st_ino=one.st_ino, st_mtime_ns=one.st_mtime_ns)

    monkeypatch.setattr(durable_reads, "lstat_info", reused_inode)
    assert has_same_bytes(first, other) is False


def test_same_bytes_refuses_what_is_not_a_regular_file(tmp_path: Path) -> None:
    file, shorter = tmp_path / "a", tmp_path / "b"
    file.write_bytes(b"abc")
    shorter.write_bytes(b"ab")
    assert has_same_bytes(file, shorter) is False
    assert has_same_bytes(file, tmp_path / "missing") is False
    assert has_same_bytes(file, tmp_path) is False
    if sys.platform != "win32":
        link = tmp_path / "link"
        link.symlink_to(file)
        assert has_same_bytes(file, link) is False


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks and FIFOs need POSIX")
@pytest.mark.parametrize("swapped", ["link", "fifo", "vanished"])
def test_same_bytes_refuses_a_file_swapped_after_its_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swapped: str
) -> None:
    """The lookup saw a regular file; something else holds the name when it is opened."""
    file, other = tmp_path / "a", tmp_path / "b"
    file.write_bytes(b"abc")
    other.write_bytes(b"abc")
    regular = os.lstat(other)
    if swapped == "link":
        other.unlink()
        other.symlink_to(file)
    elif swapped == "fifo":
        other.unlink()
        os.mkfifo(other)
    else:
        other.unlink()
    lookup = durable_reads.lstat_info
    monkeypatch.setattr(
        durable_reads,
        "lstat_info",
        lambda show_id, path: regular if path == other else lookup(show_id, path),
    )
    assert has_same_bytes(file, other) is False


@pytest.mark.skipif(sys.platform == "win32", reason="FIFOs need POSIX")
def test_same_bytes_refuses_two_fifos_found_in_place_of_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two FIFOs read as empty, so only the check of each opened descriptor refuses them."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"abc")
    second.write_bytes(b"abc")
    looked_up = {path: os.lstat(path) for path in (first, second)}
    for path in (first, second):
        path.unlink()
        os.mkfifo(path)
    monkeypatch.setattr(durable_reads, "lstat_info", lambda _show_id, path: looked_up[path])
    assert has_same_bytes(first, second) is False


@pytest.mark.skipif(sys.platform == "win32", reason="Windows refuses to open a folder as a file")
@pytest.mark.parametrize("swapped", ["first", "second"])
def test_regression_same_bytes_refuses_a_folder_found_in_place_of_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swapped: str
) -> None:
    """A folder opens read-only on POSIX; it must be refused and its descriptor closed."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"abc")
    second.write_bytes(b"abc")
    looked_up = {path: os.lstat(path) for path in (first, second)}
    folder = first if swapped == "first" else second
    folder.unlink()
    folder.mkdir()
    monkeypatch.setattr(durable_reads, "lstat_info", lambda _show_id, path: looked_up[path])
    opened: list[int] = []
    real_open = durable_reads._open_no_follow

    def recording_open(path: Path, fail: Callable[[Path, str], Exception]) -> int:
        opened.append(real_open(path, fail))
        return opened[-1]

    monkeypatch.setattr(durable_reads, "_open_no_follow", recording_open)
    assert has_same_bytes(first, second) is False
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptor)
