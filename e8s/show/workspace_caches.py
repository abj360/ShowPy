"""
workspace_caches.py --- bounded caches a workspace keeps per revision and per blob

Contains:
    RevisionCache: bounded LRU of values keyed by revision ID
    BlobCache: byte-bounded LRU of blob contents keyed by digest
    BlobCache.read(): returns a cached blob or loads and stores it
"""

from collections import OrderedDict
from collections.abc import Callable


class RevisionCache[T]:
    """Keeps the values computed for the most recently used revisions.

    Revisions never change, so a value stays right for as long as it is kept.

    Attributes:
        max_entries: Most revisions kept; the least recently used one leaves first.

    >>> cache = RevisionCache[int](1)
    >>> cache["a"], cache["b"] = 1, 2
    >>> "a" in cache, cache["b"]
    (False, 2)
    """

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[str, T] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, revision: object) -> bool:
        return revision in self._entries

    def __getitem__(self, revision: str) -> T:
        self._entries.move_to_end(revision)
        return self._entries[revision]

    def __setitem__(self, revision: str, value: T) -> None:
        self._entries[revision] = value
        self._entries.move_to_end(revision)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)


class BlobCache:
    """Keeps recently read blobs, holding at most `max_bytes` of content in total.

    A blob larger than the bound is returned but never stored. Blobs are keyed by content
    digest, so one entry serves every revision and path that stores the same bytes.

    Attributes:
        max_bytes: Most blob bytes kept; the least recently used blobs leave first.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, bytes] = OrderedDict()
        self._size = 0

    def read(self, digest: str, load: Callable[[str], bytes]) -> bytes:
        """Returns the blob cached under `digest`, loading it on a miss.

        A load that raises stores nothing.

        Args:
            digest: Digest of the blob bytes.
            load: Reads the blob bytes for a digest.

        Returns:
            blob: The blob bytes.

        >>> cache = BlobCache(10)
        >>> cache.read("d", lambda digest: b"hi"), cache.read("d", lambda digest: b"no")
        (b'hi', b'hi')
        """
        if digest in self._entries:
            self._entries.move_to_end(digest)
            return self._entries[digest]
        blob = load(digest)
        if len(blob) <= self.max_bytes:
            self._store(digest, blob)
        return blob

    def _store(self, digest: str, blob: bytes) -> None:
        if digest in self._entries:
            self._size -= len(self._entries.pop(digest))
        self._entries[digest] = blob
        self._size += len(blob)
        while self._size > self.max_bytes:
            self._size -= len(self._entries.popitem(last=False)[1])
