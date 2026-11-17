"""
integrity.py --- checks that every commit lands a known path layout and a decodable show

Contains:
    KNOWN_ROOTS: the path prefixes a repository accepts documents under
    require_known_path(): refuses a path outside KNOWN_ROOTS, including look-alike paths
    blob_digest(): returns the SHA-256 hex digest blobs are addressed by
    require_valid_digests(): checks digest trees, loading and decoding only what must be decoded
    require_valid_tree(): checks byte trees the same way, without a cache
"""

import hashlib
from collections.abc import Callable, Iterator, Mapping

from showpy.core.errors import StorageError
from showpy.show.decode_cache import DecodeCache
from showpy.show.documents import decode_show, is_show_doc
from showpy.show.models import Show

KNOWN_ROOTS = (
    "/show/",
    "/time/",
    "/sets/",
    "/transitions/",
    "/canvas/",
    "/config/",
    "/publish/",
    "/renders/",
    "/exports/",
    "/lib/drafts/",
)
FORBIDDEN_CHARACTERS = ("\\", "\x00")
DOT_SEGMENTS = ("", ".", "..")


def _bad_path(path: str) -> StorageError:
    return StorageError(
        f"path {path!r} is not a storable document path",
        hint=f"write files under {', '.join(KNOWN_ROOTS)} with no '.', '..' or empty parts",
        context={"path": path, "roots": list(KNOWN_ROOTS)},
        code="BAD_PATH",
    )


def require_known_path(path: str) -> None:
    """Refuses a path outside KNOWN_ROOTS, or one that only looks inside a root.

    Args:
        path: Absolute document path.

    >>> require_known_path("/sets/1/set.py")
    >>> try:
    ...     require_known_path("/sets/../etc/passwd")
    ... except StorageError as exc:
    ...     print(exc.code)
    BAD_PATH
    """
    root = next((r for r in KNOWN_ROOTS if path.startswith(r)), None)
    if root is None or any(c in path for c in FORBIDDEN_CHARACTERS):
        raise _bad_path(path)
    if any(segment in DOT_SEGMENTS for segment in path[len(root) :].split("/")):
        raise _bad_path(path)


def blob_digest(data: bytes) -> str:
    """Returns the SHA-256 hex digest blobs and assets are addressed by."""
    return hashlib.sha256(data).hexdigest()


class _LazyBlobs(Mapping[str, bytes]):
    def __init__(self, digests: Mapping[str, str], blob: Callable[[str], bytes]) -> None:
        self._digests = digests
        self._blob = blob

    def __getitem__(self, path: str) -> bytes:
        return self._blob(self._digests[path])

    def __iter__(self) -> Iterator[str]:
        return iter(self._digests)

    def __len__(self) -> int:
        return len(self._digests)


def require_valid_digests(
    before: Mapping[str, str],
    after: Mapping[str, str],
    blob: Callable[[str], bytes],
    cache: DecodeCache | None = None,
) -> Show | None:
    """Checks every changed path and decodes the show when any show document changed.

    Documents whose digest sits in `cache` at the same path are reused; every changed
    document is decoded fresh, and the show is validated whole either way.

    Args:
        before: Path to blob digest before the commit.
        after: Path to blob digest the commit would leave.
        blob: Returns the bytes of a digest; called only for documents that must be decoded.
        cache: Decoded documents of earlier commits, or None to decode everything.

    Returns:
        show: The decoded `after` tree, or None when no show document changed.

    >>> require_valid_digests({}, {"/renders/a.txt": blob_digest(b"hi")}, lambda d: b"hi") is None
    True
    """
    changed = [p for p in set(before) | set(after) if before.get(p) != after.get(p)]
    for path in sorted(changed):
        require_known_path(path)
    if not any(is_show_doc(p) for p in changed):
        return None
    return decode_show(_LazyBlobs(after, blob), cache=cache, digests=after)


def require_valid_tree(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> Show | None:
    """Checks every changed path and decodes the show when any show document changed.

    Args:
        before: Document bytes before the commit.
        after: Document bytes the commit would leave.

    Returns:
        show: The decoded `after` tree, or None when no show document changed.

    >>> require_valid_tree({}, {"/renders/a.txt": b"hi"}) is None
    True
    """
    by_digest = {blob_digest(raw): raw for raw in after.values()}
    return require_valid_digests(
        {p: blob_digest(raw) for p, raw in before.items()},
        {p: blob_digest(raw) for p, raw in after.items()},
        by_digest.__getitem__,
    )
