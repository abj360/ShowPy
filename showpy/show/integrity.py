"""
integrity.py --- checks that every commit lands a known path layout and a decodable show

Contains:
    KNOWN_ROOTS: the path prefixes a repository accepts documents under
    require_known_path(): refuses a path outside KNOWN_ROOTS, including look-alike paths
    require_valid_tree(): checks the changed paths and decodes the show when a show document moved
"""

from collections.abc import Mapping

from showpy.core.errors import StorageError
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
    changed = [p for p in set(before) | set(after) if before.get(p) != after.get(p)]
    for path in sorted(changed):
        require_known_path(path)
    if not any(is_show_doc(p) for p in changed):
        return None
    return decode_show(after)
