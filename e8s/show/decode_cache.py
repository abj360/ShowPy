"""
decode_cache.py --- keeps decoded documents by path and digest so unchanged ones decode once

Contains:
    DECODE_CACHE_SIZE: default bound on cached documents
    DecodeCache: bounded, thread-safe LRU of decoded documents keyed by path and blob digest
    DecodeCache.get_or_decode(): returns a cached document or decodes and stores it
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import cast

# Default for a repository-wide cache shared by every show it stores: with three documents per
# set (set, forms, paths), 4096 holds about 1,300 sets, 20 shows of 60.
DECODE_CACHE_SIZE = 4096


class DecodeCache:
    """Remembers decoded immutable documents; the key is the path plus the blob digest.

    The path is part of the key because a document's checks depend on where it sits: the same
    bytes can be valid at one path and refused at another. The validation context is not part of
    the key, so one cache serves exactly one validation context.

    Attributes:
        max_entries: Most documents kept; the least recently used one leaves first.
    """

    def __init__(self, max_entries: int = DECODE_CACHE_SIZE) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], object] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_decode[T](self, path: str, digest: str, decode: Callable[[], T]) -> T:
        """Returns the document cached for (path, digest), decoding it on a miss.

        A decode that raises stores nothing. Two threads may decode the same miss; the results
        are equal, so the later store wins.

        Args:
            path: Document path.
            digest: Digest of the document bytes.
            decode: Builds the immutable decoded document from its bytes.

        Returns:
            document: The decoded document.

        >>> cache = DecodeCache()
        >>> cache.get_or_decode("/a", "d1", lambda: 1), cache.get_or_decode("/a", "d1", lambda: 2)
        (1, 1)
        """
        key = (path, digest)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return cast(T, self._entries[key])
        document = decode()
        with self._lock:
            self._entries[key] = document
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return document
