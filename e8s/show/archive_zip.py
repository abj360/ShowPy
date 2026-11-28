"""
archive_zip.py --- writes and reads the canonical stored ZIP a show archive is, refusing any other

An archive is a ZIP whose entries are stored, never compressed, in strictly ascending name order,
each with the same fixed header fields: the ZIP epoch (1980-01-01 00:00:00) as its time, no flag,
no extra field and no comment. Equal entries give equal bytes on every platform and Python
release. The reader accepts that form only: it checks the end record, then each central header
against the fixed fields, the entry's size cap and its local header, before it reads the entry's
bytes for the CRC. Entries must follow each other with no gap and no overlap, so no byte is read
twice and the entries together are never larger than the archive. The archive is never larger
than a ZIP without its ZIP64 extension can address, so no ZIP64 record is ever needed.

Contains:
    MAX_ARCHIVE_BYTES: caps the size of an archive below where ZIP offsets need ZIP64
    MAX_ARCHIVE_ENTRIES: caps the entries of an archive below the ZIP64 entry-count sentinel
    MAX_NAME_BYTES: caps the length of an entry name
    ARCHIVE_HINT: tells the caller how to get an archive this reader accepts
    LOCAL_SIGNATURE: starts every local header
    CENTRAL_SIGNATURE: starts every central directory header
    END_SIGNATURE: starts the end record
    LOCAL_HEADER: lays out a local header's fields
    CENTRAL_HEADER: lays out a central directory header's fields
    END_RECORD: lays out the end record's fields
    STORED: names the compression method of an entry stored as it is
    VERSION_NEEDED: names the ZIP version a reader needs for a stored entry
    UNIX_HOST: names the host system the entries' attributes follow
    VERSION_MADE_BY: names the host and ZIP version that wrote the entries
    EPOCH_TIME: holds the ZIP time of every entry, midnight
    EPOCH_DATE: holds the ZIP date of every entry, 1980-01-01
    EXTERNAL_ATTRIBUTES: gives every entry the attributes of a regular file, mode 0644
    ENCRYPTED_FLAG: marks an encrypted entry, which is refused
    CENTRAL_FIXED: holds the central header fields every entry has, in header order
    archive_error(): builds the CORRUPT StorageError for an archive that fails a check
    ZipEntry: holds one entry's name and a view of its bytes
    ArchiveWriter: writes entries, in ascending name order, into a canonical stored ZIP
    read_entries(): checks a canonical stored ZIP and returns its entries
"""

import stat
import struct
import zlib
from collections.abc import Callable
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, bounded_repr
from e8s.core.json_types import JsonValue

# Offsets and sizes are 32-bit, and readers that treat them as signed need ZIP64 from 2**31 on.
MAX_ARCHIVE_BYTES = 2**31 - 1
# The end record counts entries in 16 bits; 0xFFFF there means "see the ZIP64 record".
MAX_ARCHIVE_ENTRIES = 0xFFFF - 1
MAX_NAME_BYTES = 255
ARCHIVE_HINT = "export the show again with export_archive; only the exact form it writes is read"
LOCAL_SIGNATURE = 0x04034B50
CENTRAL_SIGNATURE = 0x02014B50
END_SIGNATURE = 0x06054B50
LOCAL_HEADER = struct.Struct("<IHHHHHIIIHH")
CENTRAL_HEADER = struct.Struct("<IHHHHHHIIIHHHHHII")
END_RECORD = struct.Struct("<IHHHHIIH")
STORED = 0
VERSION_NEEDED = 20
UNIX_HOST = 3
VERSION_MADE_BY = UNIX_HOST << 8 | VERSION_NEEDED
EPOCH_TIME = 0
EPOCH_DATE = 1 << 5 | 1
EXTERNAL_ATTRIBUTES = (stat.S_IFREG | 0o644) << 16
ENCRYPTED_FLAG = 0x1
CENTRAL_FIXED = (
    *(VERSION_MADE_BY, VERSION_NEEDED, 0, STORED, EPOCH_TIME, EPOCH_DATE),
    *(0, 0, 0, 0, EXTERNAL_ATTRIBUTES),
)


def archive_error(reason: str, *, entry: str = "", **context: JsonValue) -> StorageError:
    """Builds the CORRUPT StorageError for an archive that fails a check.

    Args:
        reason: What check the archive failed.
        entry: Name of the entry that failed it, if any; shown bounded, never its bytes.
        context: Further values for the error's context.
    """
    if entry:
        context["entry"] = entry
    named = f"archive entry {bounded_repr(entry)}" if entry else "archive"
    return StorageError(
        f"{named} is refused: {reason}", hint=ARCHIVE_HINT, context=context, code=ErrorCode.CORRUPT
    )


def _too_large(reason: str, cap: int, entry: str = "") -> StorageError:
    error = archive_error(reason, entry=entry, cap=cap)
    return StorageError(
        error.message,
        hint="an archive holds at most "
        f"{MAX_ARCHIVE_ENTRIES} entries and {MAX_ARCHIVE_BYTES} bytes, and each entry at most "
        "the size its kind of file may have in a repository",
        context=error.context,
        code=ErrorCode.TOO_LARGE,
    )


class ZipEntry(NamedTuple):
    """Holds one archive entry: its name and a view of its bytes inside the archive."""

    name: str
    data: memoryview


class _Header(NamedTuple):
    """Holds the fields a central header gives for one entry."""

    name: str
    flags: int
    method: int
    fixed: tuple[int, ...]
    crc: int
    size: int
    stored_size: int
    offset: int


class ArchiveWriter:
    """Writes entries, in ascending name order, into a canonical stored ZIP.

    Each entry is written as soon as it is added, so the writer holds the archive so far and a
    few bytes per entry for the central directory, never a second copy of an entry.

    >>> writer = ArchiveWriter(lambda name: 16)
    >>> writer.add("a.txt", b"hi")
    >>> [entry.name for entry in read_entries(writer.finish(), lambda name: 16)]
    ['a.txt']
    """

    def __init__(self, cap_of: Callable[[str], int]) -> None:
        self._cap_of = cap_of
        self._archive = bytearray()
        self._central = bytearray()
        self._count = 0
        self._last = ""

    def add(self, name: str, data: bytes) -> None:
        """Appends one stored entry; its name must sort after the previous entry's name.

        Args:
            name: Entry name: ASCII, sorting after every name added before.
            data: Entry bytes, within the cap `cap_of` gives for the name.
        """
        if self._count and name <= self._last:
            raise archive_error("entry names must be added in strictly ascending order", entry=name)
        cap = self._cap_of(name)
        if len(data) > cap:
            raise _too_large(f"entry holds {len(data)} bytes, over its cap", cap, name)
        if self._count == MAX_ARCHIVE_ENTRIES:
            raise _too_large("archive would hold too many entries", MAX_ARCHIVE_ENTRIES, name)
        encoded = name.encode("ascii")
        size = len(self._archive) + len(self._central) + END_RECORD.size
        added = LOCAL_HEADER.size + CENTRAL_HEADER.size + 2 * len(encoded) + len(data)
        if size + added > MAX_ARCHIVE_BYTES:
            raise _too_large("archive would pass its byte cap", MAX_ARCHIVE_BYTES, name)
        crc = zlib.crc32(data)
        offset = len(self._archive)
        self._archive += _local_header(crc, len(data), len(encoded)) + encoded
        self._archive += data
        self._central += _central_header(crc, len(data), len(encoded), offset) + encoded
        self._count += 1
        self._last = name

    def finish(self) -> bytes:
        """Returns the archive: the entries, the central directory and the end record.

        Call it once, after the last entry.
        """
        end = END_RECORD.pack(
            END_SIGNATURE,
            0,
            0,
            self._count,
            self._count,
            len(self._central),
            len(self._archive),
            0,
        )
        self._archive += self._central
        self._archive += end
        return bytes(self._archive)


def _local_header(crc: int, size: int, name_size: int) -> bytes:
    fields = (VERSION_NEEDED, 0, STORED, EPOCH_TIME, EPOCH_DATE, crc, size, size, name_size, 0)
    return LOCAL_HEADER.pack(LOCAL_SIGNATURE, *fields)


def _central_header(crc: int, size: int, name_size: int, offset: int) -> bytes:
    versions, sizes = CENTRAL_FIXED[:6], (crc, size, size, name_size)
    trailer = (*CENTRAL_FIXED[6:], offset)
    return CENTRAL_HEADER.pack(CENTRAL_SIGNATURE, *versions, *sizes, *trailer)


def _central_bounds(data: bytes) -> tuple[int, int]:
    """Checks the end record and returns the entry count and where the central directory starts.

    The record must be the last 22 bytes, with no comment, on one disk, and the central
    directory must end where it starts, so nothing sits between them or after them.
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise _too_large(f"archive holds {len(data)} bytes", MAX_ARCHIVE_BYTES)
    end = len(data) - END_RECORD.size
    if end < 0:
        raise archive_error("it is shorter than a ZIP end record: not a ZIP archive, or cut short")
    signature, disk, central_disk, on_disk, count, size, start, comment = END_RECORD.unpack_from(
        data, end
    )
    if signature != END_SIGNATURE:
        raise archive_error("it does not end with a ZIP end record: cut short, or framed by bytes")
    if (disk, central_disk, comment) != (0, 0, 0) or on_disk != count:
        raise archive_error("it spans disks or carries a comment")
    if count > MAX_ARCHIVE_ENTRIES:
        raise _too_large(f"archive counts {count} entries", MAX_ARCHIVE_ENTRIES)
    if start + size != end:
        raise archive_error("bytes sit between its central directory and its end record")
    return count, start


def _central(data: bytes, position: int, end: int) -> _Header:
    """Reads the central header at `position`, which must fit before `end`, with its name."""
    if position + CENTRAL_HEADER.size > end:
        raise archive_error("its central directory is shorter than its entry count says")
    fields = CENTRAL_HEADER.unpack_from(data, position)
    if fields[0] != CENTRAL_SIGNATURE:
        raise archive_error("a central directory header has no signature")
    name_size = fields[10]
    name_end = position + CENTRAL_HEADER.size + name_size
    if name_size > MAX_NAME_BYTES or name_end > end:
        raise archive_error("an entry name is longer than the archive allows")
    try:
        name = data[position + CENTRAL_HEADER.size : name_end].decode("ascii")
    except UnicodeDecodeError:
        raise archive_error("an entry name is not ASCII") from None
    fixed = (*fields[1:7], *fields[11:16])
    return _Header(name, fields[3], fields[4], fixed, fields[7], fields[9], fields[8], fields[16])


def _require_fixed(header: _Header, previous: str | None) -> None:
    """Refuses an entry whose header fields differ from the fixed ones, or that is out of order."""
    if header.flags & ENCRYPTED_FLAG:
        raise archive_error("it is encrypted", entry=header.name)
    if header.method != STORED:
        raise archive_error("it is compressed; archives hold stored entries", entry=header.name)
    if header.fixed != CENTRAL_FIXED or header.size != header.stored_size:
        raise archive_error(
            "its header fields differ from the ones archives use", entry=header.name
        )
    if previous is not None and header.name == previous:
        raise archive_error("the name is written twice", entry=header.name)
    if previous is not None and header.name < previous:
        raise archive_error("names are not in ascending order", entry=header.name)


def _entry_bytes(data: bytes, header: _Header, start: int, end: int) -> memoryview:
    """Checks the local header of an entry starting at `start` and returns its bytes.

    Args:
        data: The archive.
        header: The entry's central header.
        start: Where the previous entry ended; the entry must start there.
        end: Where the central directory starts; the entry must end before it.
    """
    if header.offset != start:
        raise archive_error("it does not start where the entry before it ends", entry=header.name)
    name = header.name.encode("ascii")
    body = start + LOCAL_HEADER.size + len(name)
    if body + header.size > end:
        raise archive_error("it runs past the central directory: cut short", entry=header.name)
    local = data[start : start + LOCAL_HEADER.size]
    expected = _local_header(header.crc, header.size, len(name))
    if local != expected or data[start + LOCAL_HEADER.size : body] != name:
        raise archive_error("its local header differs from its central one", entry=header.name)
    view = memoryview(data)[body : body + header.size]
    if zlib.crc32(view) != header.crc:
        raise archive_error("its bytes fail their CRC", entry=header.name)
    return view


def read_entries(data: bytes, cap_of: Callable[[str], int]) -> list[ZipEntry]:
    """Checks that `data` is a canonical stored ZIP and returns its entries in order.

    Each entry's size is checked against `cap_of(name)` before its bytes are read, and the
    returned views share the archive's bytes, so reading costs no copy.

    Args:
        data: The archive, untrusted.
        cap_of: Returns the largest size an entry under that name may have; raises for a name
            that is not allowed.

    Returns:
        entries: Each entry's name and bytes, in ascending name order.

    >>> writer = ArchiveWriter(lambda name: 16)
    >>> writer.add("a.txt", b"hi")
    >>> [bytes(entry.data) for entry in read_entries(writer.finish(), lambda name: 16)]
    [b'hi']
    """
    count, central_start = _central_bounds(data)
    central_end = len(data) - END_RECORD.size
    entries: list[ZipEntry] = []
    position, ends_at, previous = central_start, 0, None
    for _ in range(count):
        header = _central(data, position, central_end)
        _require_fixed(header, previous)
        cap = cap_of(header.name)
        if header.size > cap:
            raise _too_large(f"entry declares {header.size} bytes, over its cap", cap, header.name)
        view = _entry_bytes(data, header, ends_at, central_start)
        entries.append(ZipEntry(header.name, view))
        position += CENTRAL_HEADER.size + len(header.name)
        ends_at, previous = (
            ends_at + LOCAL_HEADER.size + len(header.name) + header.size,
            header.name,
        )
    if position != central_end or ends_at != central_start:
        raise archive_error("its central directory or entries hold bytes no entry accounts for")
    return entries
