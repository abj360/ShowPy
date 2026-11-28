#!/usr/bin/env python3

"""
test_archive_zip.py --- tests the canonical stored ZIP an archive is written and read as

Contains:
    test_written_archive_opens_in_zipfile: writes stored entries at the ZIP epoch that the
        standard library reads back byte for byte
    test_written_bytes_equal_zipfile_given_the_same_fields: matches the standard library's own
        writer byte for byte when it is given the fixed fields
    test_written_bytes_are_pinned: pins the bytes of a fixed archive, so no Python release or
        platform changes them
    test_read_entries_returns_what_was_written: reads back every name and byte in order
    test_regression_a_compressed_entry_is_refused: refuses a deflated entry, a ZIP bomb vector
    test_regression_an_encrypted_entry_is_refused: refuses an entry whose encryption flag is set
    test_regression_an_entry_over_its_cap_is_refused_before_it_is_read: refuses a declared size
        over the entry's cap without computing a CRC over its bytes
    test_regression_too_many_entries_are_refused_before_the_walk: refuses an end record counting
        more entries than the cap before reading any central header
    test_regression_overlapping_entries_are_refused: refuses two entries sharing local bytes
    test_regression_an_entry_count_that_disagrees_is_refused: refuses an end record counting
        more or fewer entries than the central directory holds
    test_regression_an_entry_running_past_its_end_is_refused: refuses a declared size that runs
        into the central directory
    test_regression_duplicate_entry_names_are_refused: refuses a name written twice
    test_regression_unsorted_entry_names_are_refused: refuses names out of ascending order, on
        writing and on reading
    test_regression_a_truncated_archive_is_refused: refuses every cut-short prefix
    test_regression_every_single_byte_change_is_refused: refuses an archive with any one byte
        changed, so no header field, size or CRC goes unchecked
    test_garbage_and_framed_bytes_are_refused: refuses bytes that are not a ZIP, and an archive
        with bytes before or after it or a comment
    test_regression_a_name_outside_ascii_is_refused: refuses a name byte above 127
    test_regression_a_name_over_the_name_cap_is_refused: refuses a name longer than the cap
        before reading it
    test_the_size_caps_bound_writing_and_reading: refuses to write or read past the entry and
        byte caps
"""

import hashlib
import io
import struct
import zipfile
import zlib
from collections.abc import Callable

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import archive_zip
from e8s.show.archive_zip import ArchiveWriter, read_entries

ENTRIES = (("a.txt", b"alpha\n"), ("b/c.bin", bytes(range(256))), ("d", b""))
PINNED_SHA256 = "83c14778986225bdc08eaba2811a40438804cff1f1f95f1b1ee8809de2d207a3"
CAP = 1024
END_RECORD_SIZE = 22


def _cap(_name: str) -> int:
    return CAP


def _archive(entries: tuple[tuple[str, bytes], ...] = ENTRIES) -> bytes:
    writer = ArchiveWriter(_cap)
    for name, data in entries:
        writer.add(name, data)
    return writer.finish()


def _refused(data: bytes, cap_of: Callable[[str], int] = _cap) -> StorageError:
    with pytest.raises(StorageError) as caught:
        read_entries(data, cap_of)
    return caught.value


def _set_u16(data: bytes, offset: int, value: int) -> bytes:
    return data[:offset] + struct.pack("<H", value) + data[offset + 2 :]


def _central_offset(data: bytes) -> int:
    return int(struct.unpack_from("<I", data, len(data) - END_RECORD_SIZE + 16)[0])


def test_written_archive_opens_in_zipfile() -> None:
    with zipfile.ZipFile(io.BytesIO(_archive())) as opened:
        assert opened.testzip() is None
        infos = opened.infolist()
        assert [(i.filename, opened.read(i)) for i in infos] == list(ENTRIES)
        assert {i.compress_type for i in infos} == {zipfile.ZIP_STORED}
        assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}
        assert {(i.extra, i.comment, i.flag_bits) for i in infos} == {(b"", b"", 0)}


def test_written_bytes_equal_zipfile_given_the_same_fields() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as written:
        for name, data in ENTRIES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = archive_zip.UNIX_HOST
            info.external_attr = archive_zip.EXTERNAL_ATTRIBUTES
            written.writestr(info, data)
    assert buffer.getvalue() == _archive()


def test_written_bytes_are_pinned() -> None:
    assert hashlib.sha256(_archive()).hexdigest() == PINNED_SHA256


def test_read_entries_returns_what_was_written() -> None:
    entries = read_entries(_archive(), _cap)
    assert [(entry.name, bytes(entry.data)) for entry in entries] == list(ENTRIES)
    assert read_entries(_archive(()), _cap) == []


def test_regression_a_compressed_entry_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as written:
        written.writestr("a.txt", b"a" * 4096)
    error = _refused(buffer.getvalue())
    assert error.code == ErrorCode.CORRUPT
    assert "compressed" in error.message
    assert error.context["entry"] == "a.txt"


def test_regression_an_encrypted_entry_is_refused() -> None:
    data = _archive()
    central = _central_offset(data)
    data = _set_u16(_set_u16(data, 6, 1), central + 8, 1)
    error = _refused(data)
    assert error.code == ErrorCode.CORRUPT
    assert "encrypted" in error.message


def test_regression_an_entry_over_its_cap_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _archive()
    sizes: list[int] = []
    real_crc = zlib.crc32

    def counted_crc(view: memoryview) -> int:
        sizes.append(len(view))
        return real_crc(view)

    monkeypatch.setattr(zlib, "crc32", counted_crc)
    error = _refused(data, lambda name: 10 if name == "b/c.bin" else CAP)
    assert error.code == ErrorCode.TOO_LARGE
    assert error.context["entry"] == "b/c.bin"
    assert sizes == [len(ENTRIES[0][1])]
    central = _central_offset(data) + 46 + len("a.txt")
    huge = struct.pack("<II", 2**31, 2**31)
    error = _refused(data[: central + 20] + huge + data[central + 28 :])
    assert error.code == ErrorCode.TOO_LARGE


def test_regression_too_many_entries_are_refused_before_the_walk() -> None:
    data = _archive()
    end = len(data) - END_RECORD_SIZE
    too_many = archive_zip.MAX_ARCHIVE_ENTRIES + 1
    flooded = data[: end + 8] + struct.pack("<HH", too_many, too_many) + data[end + 12 :]
    error = _refused(flooded)
    assert error.code == ErrorCode.TOO_LARGE
    assert error.context["cap"] == archive_zip.MAX_ARCHIVE_ENTRIES


def test_regression_overlapping_entries_are_refused() -> None:
    data = _archive((("a", b"x" * 64), ("b", b"x" * 64)))
    second = _central_offset(data) + 46 + 1
    overlapping = data[: second + 42] + struct.pack("<I", 0) + data[second + 46 :]
    error = _refused(overlapping)
    assert error.code == ErrorCode.CORRUPT
    assert error.context["entry"] == "b"


def test_regression_an_entry_count_that_disagrees_is_refused() -> None:
    data = _archive()
    end = len(data) - END_RECORD_SIZE
    for count in (len(ENTRIES) + 1, len(ENTRIES) - 1):
        recounted = data[: end + 8] + struct.pack("<HH", count, count) + data[end + 12 :]
        assert _refused(recounted).code == ErrorCode.CORRUPT


def test_regression_an_entry_running_past_its_end_is_refused() -> None:
    data = _archive((("a", b"x" * 8),))
    central = _central_offset(data)
    longer = struct.pack("<II", 64, 64)
    for at in (18, central + 20):
        data = data[:at] + longer + data[at + 8 :]
    error = _refused(data)
    assert "runs past" in error.message


def test_regression_duplicate_entry_names_are_refused() -> None:
    data = _archive((("a1", b"x"), ("a2", b"x"))).replace(b"a2", b"a1")
    error = _refused(data)
    assert "twice" in error.message
    assert error.context["entry"] == "a1"


def test_regression_unsorted_entry_names_are_refused() -> None:
    writer = ArchiveWriter(_cap)
    writer.add("b", b"")
    with pytest.raises(StorageError, match="ascending"):
        writer.add("a", b"")
    data = _archive((("a1", b"x"), ("a2", b"y"))).replace(b"a1", b"a3")
    assert "ascending" in _refused(data).message


def test_regression_a_truncated_archive_is_refused() -> None:
    data = _archive()
    for length in range(len(data)):
        assert _refused(data[:length]).code == ErrorCode.CORRUPT


def test_regression_every_single_byte_change_is_refused() -> None:
    data = _archive()
    for offset in range(len(data)):
        changed = data[:offset] + bytes([data[offset] ^ 0x01]) + data[offset + 1 :]
        assert _refused(changed).code in (ErrorCode.CORRUPT, ErrorCode.TOO_LARGE), offset


def test_garbage_and_framed_bytes_are_refused() -> None:
    data = _archive()
    for garbage in (b"", b"PK", b"not a zip archive" * 8, b"\x00" + data, data + b"\x00"):
        assert _refused(garbage).code == ErrorCode.CORRUPT
    commented = _set_u16(data, len(data) - 2, 3) + b"abc"
    assert _refused(commented).code == ErrorCode.CORRUPT


def test_regression_a_name_outside_ascii_is_refused() -> None:
    data = _archive((("a", b"x"),)).replace(b"a" + b"x", b"\xe1" + b"x", 1)
    data = data.replace(b"a" + b"PK", b"\xe1" + b"PK", 1)
    error = _refused(data)
    assert error.code == ErrorCode.CORRUPT
    assert "ASCII" in error.message


def test_regression_a_name_over_the_name_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _archive()
    monkeypatch.setattr(archive_zip, "MAX_NAME_BYTES", len("a.txt") - 1)
    error = _refused(data)
    assert error.code == ErrorCode.CORRUPT
    assert "longer than" in error.message


def test_the_size_caps_bound_writing_and_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = ArchiveWriter(lambda _name: 3)
    with pytest.raises(StorageError) as caught:
        writer.add("a", b"four")
    assert caught.value.code == ErrorCode.TOO_LARGE
    monkeypatch.setattr(archive_zip, "MAX_ARCHIVE_ENTRIES", 2)
    writer = ArchiveWriter(_cap)
    writer.add("a", b"")
    writer.add("b", b"")
    with pytest.raises(StorageError) as caught:
        writer.add("c", b"")
    assert caught.value.code == ErrorCode.TOO_LARGE
    monkeypatch.setattr(archive_zip, "MAX_ARCHIVE_BYTES", 300)
    writer = ArchiveWriter(_cap)
    with pytest.raises(StorageError) as caught:
        writer.add("a", b"x" * 300)
    assert caught.value.code == ErrorCode.TOO_LARGE
    monkeypatch.setattr(archive_zip, "MAX_ARCHIVE_BYTES", 30)
    assert read_entries(_archive(()), _cap) == []
    assert _refused(b"x" * 31).code == ErrorCode.TOO_LARGE
