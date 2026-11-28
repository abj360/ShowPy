"""
torn_reads.py --- rereads a stored file that another writer may still be filling in place

Where a file system cannot hard-link, a writer creates each file under its final name and then
writes it, so a reader can find the file empty or cut short for a moment. A reader that finds such
a file pauses and reads it again before it judges the file.

Contains:
    TORN_READ_PAUSES_S: sets the pause before each reread of a file that looks torn
    TORN_READ_RETRIES: counts the rereads before a file that still looks torn is reported
    FSCK_REPAIR_CALL: spells the fsck call a hint names to remove a file a crashed writer left torn
    read_settled(): reads a file again after each pause while it looks torn
    is_unfinished_line(): tells whether bytes lack the newline that ends a finished slot
    is_unfinished_json(): tells whether bytes do not yet parse as one JSON document
    is_short_of(): builds a check that tells whether bytes are a shorter start of the expected bytes
"""

import time
from collections.abc import Callable

from e8s.core.errors import SchemaError
from e8s.core.strict_json import parse_strict_json

# A writer creates the file and fills it with its next call, so the first pause covers a writer
# that is scheduled at once and the last one a writer paused by a busy machine.
TORN_READ_PAUSES_S = (0.001, 0.01, 0.1)
TORN_READ_RETRIES = len(TORN_READ_PAUSES_S)
FSCK_REPAIR_CALL = "fsck(root, repair=True, confirm=True, at=datetime.now(UTC))"


def read_settled(read: Callable[[], bytes], is_torn: Callable[[bytes], bool]) -> bytes:
    r"""Reads a file, then reads it again after each pause for as long as it looks torn.

    Args:
        read: Reads the file's bytes.
        is_torn: Tells whether bytes read look like a file whose writer has not finished it.

    Returns:
        data: The last bytes read, which still look torn only when every reread found them so.

    >>> reads = iter([b"", b"{}", b"{}\n"])
    >>> read_settled(lambda: next(reads), is_unfinished_line)
    b'{}\n'
    """
    data = read()
    for pause in TORN_READ_PAUSES_S:
        if not is_torn(data):
            break
        time.sleep(pause)
        data = read()
    return data


def is_unfinished_line(data: bytes) -> bool:
    r"""Tells whether bytes lack the closing newline, the last byte a slot's writer writes.

    A slot is one line of canonical JSON, which holds no other newline, so any shorter part of it
    lacks the newline too.

    >>> is_unfinished_line(b'{"id":'), is_unfinished_line(b"{}\n")
    (True, False)
    """
    return not data.endswith(b"\n")


def is_unfinished_json(data: bytes) -> bool:
    """Tells whether bytes do not parse as one JSON document, as every shorter part of one fails.

    A stored JSON file holds one object, and no shorter part of an object parses, so a file its
    writer has not finished never passes; a finished but damaged file fails too, and is reported
    once the rereads run out.

    >>> is_unfinished_json(b'{"id": "a"'), is_unfinished_json(b'{"id": "a"}')
    (True, False)
    """
    try:
        parse_strict_json(data, document="stored file")
    except SchemaError:
        return True
    return False


def is_short_of(expected: bytes) -> Callable[[bytes], bool]:
    """Builds a check that tells whether bytes are a shorter start of `expected`.

    A writer that places a file this commit also places writes the same bytes, so a stored copy
    that is a shorter start of them is one that writer has not finished.

    Args:
        expected: Bytes the finished file holds.

    Returns:
        is_short: Tells whether bytes read are a proper prefix of `expected`.

    >>> is_short = is_short_of(b"abc")
    >>> is_short(b"ab"), is_short(b"abc"), is_short(b"ax")
    (True, False, False)
    """
    return lambda data: len(data) < len(expected) and expected.startswith(data)
