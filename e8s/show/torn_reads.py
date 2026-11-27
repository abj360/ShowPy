"""
torn_reads.py --- rereads a stored file that another writer may still be filling in place

Where a file system cannot hard-link, a writer creates each file under its final name and then
writes it, so a reader can find the file empty or cut short for a moment. A reader that finds such
a file pauses and reads it again before it judges the file.

Contains:
    TORN_READ_PAUSES_S: sets the pause before each reread of a file that looks torn
    TORN_READ_RETRIES: counts the rereads before a file that still looks torn is reported
    read_settled(): reads a file again after each pause while it looks torn
    is_unfinished_line(): tells whether bytes lack the newline that ends a finished slot
"""

import time
from collections.abc import Callable

# A writer creates the file and fills it with its next call, so the first pause covers a writer
# that is scheduled at once and the last one a writer paused by a busy machine.
TORN_READ_PAUSES_S = (0.001, 0.01, 0.1)
TORN_READ_RETRIES = len(TORN_READ_PAUSES_S)


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
