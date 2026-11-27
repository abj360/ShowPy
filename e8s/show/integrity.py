"""
integrity.py --- checks that every commit lands a known path layout and a decodable show

Contains:
    KNOWN_ROOTS: the path prefixes a repository accepts documents under
    RESERVED_ROOTS: the path prefixes kept for documents a later format defines, refused today
    require_structured_files(): checks changed text files decode as their readers decode them
    require_known_path(): refuses a path outside KNOWN_ROOTS, including look-alike paths, paths
        under RESERVED_ROOTS, Windows device names in writes and any name under `/cast/` but
        cast.json
    blob_digest(): returns the SHA-256 hex digest blobs are addressed by
    LazyBlobs: maps paths to blob bytes, reading each blob only when its path is looked up
    LazyBlobs.cached(): returns the tree reading each blob through a bounded cache
    LazyBlobs.digests: returns the content digest of each path's blob
    changed_paths(): lists the paths whose blob differs between two trees, reading no blob
    require_new_set_keys(): checks every set key and transition folder a commit stores in a
        folder the base lacks
    require_distinct_folders(): refuses a new set folder matching another folder ignoring case
    require_valid_digests(): checks digest trees, loading and decoding only what must be decoded,
        and refuses a decoded show the encoder could not store again
    require_valid_tree(): checks byte trees the same way, without a cache
"""

import hashlib
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import partial
from types import MappingProxyType
from typing import Self

from e8s.core.error_codes import ErrorCode
from e8s.core.error_context import bounded_repr
from e8s.core.errors import SchemaError, StorageError
from e8s.core.ids import WINDOWS_DEVICE_NAMES, check_id, is_device_name
from e8s.show.decode_cache import DecodeCache
from e8s.show.document_paths import CAST_DOC
from e8s.show.documents import (
    SHEET_DOC,
    SHOW_DOC,
    TIMELINE_DOC,
    decode_show,
    expected_show_docs,
    is_show_doc,
    require_storable,
)
from e8s.show.files import require_writable
from e8s.show.font_paths import require_written_fonts
from e8s.show.migrate import require_current_show_doc
from e8s.show.models import Show
from e8s.show.role_documents import CAST_ROOT, require_cast_beside_roles
from e8s.show.set_keys import PAIR_SEPARATOR, SETS_ROOT, TRANSITIONS_ROOT
from e8s.show.structured import TEXT_SUFFIXES, require_structured
from e8s.show.workspace_caches import BlobCache

KNOWN_ROOTS = (
    "/show/",
    "/time/",
    "/sets/",
    "/transitions/",
    CAST_ROOT,
    "/canvas/",
    "/notes/",
    "/proposals/",
    "/assets/",
    "/config/",
    "/publish/",
    "/renders/",
    "/exports/",
    "/lib/drafts/",
)
RESERVED_ROOTS = ("/props/", "/tracks/")
FORBIDDEN_CHARACTER = "\\"
CONTROL_CATEGORY = "Cc"
REQUIRED_DOCS = (SHOW_DOC, SHEET_DOC, TIMELINE_DOC)
DOT_SEGMENTS = ("", ".", "..")
FOLDER_DEPTH = 3
FORMAT_CHECKED = "format-checked"
PAIR_SIZE = 2
FORMAT_CATEGORY = "Cf"


def _bad_path(path: str) -> StorageError:
    return StorageError(
        f"path {path!r} is not a storable document path",
        hint=f"write files under {', '.join(KNOWN_ROOTS)} with no '.', '..' or empty parts",
        context={"path": path, "roots": list(KNOWN_ROOTS)},
        code=ErrorCode.BAD_PATH,
    )


def _reserved_path(path: str, root: str) -> StorageError:
    return StorageError(
        f"{root} is reserved for documents a later format version defines",
        hint=f"{root} is reserved, so nothing may be stored there yet; write files under"
        f" {', '.join(KNOWN_ROOTS)}",
        context={"path": path, "root": root, "roots": list(KNOWN_ROOTS)},
        code=ErrorCode.BAD_PATH,
    )


def _named_segments(root: str, segments: list[str]) -> list[str]:
    """Returns the segments a path names freely; a set or transition folder is its set keys'."""
    if len(segments) > 1 and root in (f"/{SETS_ROOT}/", f"/{TRANSITIONS_ROOT}/"):
        return segments[1:]
    return segments


def _device_path(path: str, device: str) -> StorageError:
    return StorageError(
        f"path {bounded_repr(path)} names {bounded_repr(device)}, a Windows device name",
        hint=f"rename that part: {', '.join(sorted(WINDOWS_DEVICE_NAMES))}, alone or before a"
        " dot, open a device on Windows instead of a file",
        context={"path": path, "segment": device},
        code=ErrorCode.BAD_PATH,
    )


def _is_forbidden(char: str) -> bool:
    return char == FORBIDDEN_CHARACTER or unicodedata.category(char) in (
        CONTROL_CATEGORY,
        FORMAT_CATEGORY,
    )


def require_known_path(path: str, *, is_write: bool = True) -> None:
    """Refuses a path outside KNOWN_ROOTS, one that only looks inside a root, or a device name.

    A segment Windows opens as a device (`con`, `NUL.txt`, `com1.png`) is refused anywhere but
    as a set or transition folder, whose set keys are checked, stored ones kept, on their own.
    A deleted path skips the device check, so a file stored before the rule can be removed.

    Args:
        path: Absolute document path.
        is_write: Whether the path is being written; False for a delete.

    >>> require_known_path("/sets/1/set.py")
    >>> try:
    ...     require_known_path("/sets/../etc/passwd")
    ... except StorageError as exc:
    ...     print(exc.code)
    BAD_PATH
    """
    reserved = next((r for r in RESERVED_ROOTS if path.startswith(r)), None)
    if reserved is not None:
        raise _reserved_path(path, reserved)
    root = next((r for r in KNOWN_ROOTS if path.startswith(r)), None)
    if root is None or any(_is_forbidden(c) for c in path):
        raise _bad_path(path)
    segments = path[len(root) :].split("/")
    if any(segment in DOT_SEGMENTS for segment in segments):
        raise _bad_path(path)
    device = next((s for s in _named_segments(root, segments) if is_device_name(s)), None)
    if is_write and device is not None:
        raise _device_path(path, device)
    if root == CAST_ROOT and path != CAST_DOC:
        raise StorageError(
            f"{bounded_repr(path)} is not a cast document: {CAST_ROOT} holds only {CAST_DOC}",
            hint=f"store the cast in {CAST_DOC}; no format defines another name there",
            context={"path": path, "allowed": [CAST_DOC]},
            code=ErrorCode.BAD_PATH,
        )


def blob_digest(data: bytes) -> str:
    """Returns the SHA-256 hex digest blobs and assets are addressed by."""
    return hashlib.sha256(data).hexdigest()


class LazyBlobs(Mapping[str, bytes]):
    """Maps paths to blob bytes, reading a blob only when its path is looked up.

    Membership and iteration read the digests alone, so listing or probing paths loads nothing.
    A path in the tree whose blob the backend lacks raises StorageError, so `get` never turns
    a missing blob into None.

    >>> blobs = LazyBlobs({"/renders/a.txt": "d"}, lambda digest: b"hi")
    >>> "/renders/a.txt" in blobs, blobs["/renders/a.txt"]
    (True, b'hi')
    """

    def __init__(
        self,
        digests: Mapping[str, str],
        blob: Callable[[str], bytes],
        *,
        uncached: Callable[[str], bytes] | None = None,
    ) -> None:
        self._digests = digests
        self._blob = blob
        self._uncached = blob if uncached is None else uncached

    def __getitem__(self, path: str) -> bytes:
        digest = self._digests[path]
        try:
            return self._blob(digest)
        except KeyError as err:
            raise StorageError(
                f"no blob stored for {path!r}",
                hint="restore the show folder from a backup or remove the damaged file",
                context={"path": path, "digest": digest},
                code=ErrorCode.CORRUPT,
            ) from err

    def __contains__(self, path: object) -> bool:
        return path in self._digests

    def __iter__(self) -> Iterator[str]:
        return iter(self._digests)

    def __len__(self) -> int:
        return len(self._digests)

    def cached(self, cache: BlobCache) -> Self:
        """Returns the same tree reading each blob through `cache`, so repeat lookups read none.

        The blobs bypass any memo the tree's own reader keeps, so `cache` alone bounds the bytes
        held.
        """
        load = self._uncached
        return type(self)(self._digests, lambda digest: cache.read(digest, load))

    @property
    def digests(self) -> Mapping[str, str]:
        """Returns a read-only view of each path's blob digest, so callers can key caches by it."""
        return MappingProxyType(self._digests)


def _digests(tree: Mapping[str, bytes]) -> Mapping[str, str]:
    if isinstance(tree, LazyBlobs):
        return tree.digests
    return {path: blob_digest(raw) for path, raw in tree.items()}


def changed_paths(before: Mapping[str, bytes], after: Mapping[str, bytes]) -> list[str]:
    """Lists the paths written or deleted between two trees, sorted, comparing digests alone.

    Args:
        before: Tree of the older state.
        after: Tree of the newer state.

    Returns:
        paths: Paths present in only one tree or holding different bytes.

    >>> changed_paths({"/a": b"1", "/b": b"2"}, {"/a": b"1", "/b": b"3", "/c": b"4"})
    ['/b', '/c']
    """
    old, new = _digests(before), _digests(after)
    return sorted(p for p in old.keys() | new.keys() if old.get(p) != new.get(p))


def _require_required_docs(after: Mapping[str, str]) -> None:
    missing = [p for p in REQUIRED_DOCS if p not in after]
    if missing:
        raise SchemaError(
            f"commit leaves no {', '.join(missing)}",
            hint="write the whole show with save_show()",
            context={"missing": missing},
        )


def _require_no_orphans(show: Show, written: list[str]) -> None:
    expected = expected_show_docs(show)
    orphans = sorted(p for p in written if is_show_doc(p) and p not in expected)
    if orphans:
        raise SchemaError(
            f"show documents the show does not read: {', '.join(orphans)}",
            hint="write documents only for sets and consecutive transitions in the show",
            context={"orphans": orphans},
        )


def _set_keys_in(path: str) -> list[str]:
    parts = path.split("/")
    if len(parts) <= FOLDER_DEPTH:
        return []
    if parts[1] == SETS_ROOT:
        return [parts[2]]
    if parts[1] == TRANSITIONS_ROOT:
        return parts[2].split(PAIR_SEPARATOR)
    return []


def _transition_folder(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) <= FOLDER_DEPTH or parts[1] != TRANSITIONS_ROOT:
        return None
    return parts[2]


def _require_pair_folders(before: Iterable[str], written: Iterable[str]) -> None:
    """Refuses a transition folder new to the tree that does not name exactly two set keys."""
    stored = {_transition_folder(path) for path in before}
    new = {_transition_folder(path) for path in written} - stored
    for folder in sorted(folder for folder in new if folder is not None):
        keys = folder.split(PAIR_SEPARATOR)
        if len(keys) != PAIR_SIZE or not all(keys):
            raise SchemaError(
                f"transition folder {bounded_repr(folder)} does not name two set keys",
                hint=f"store a transition's files under /{TRANSITIONS_ROOT}/"
                f"<key a>{PAIR_SEPARATOR}<key b>/",
                context={"folder": folder},
            )


def require_new_set_keys(before: Iterable[str], written: Iterable[str]) -> None:
    """Checks every set key and transition folder a commit stores in a folder the base lacks.

    Folders already stored are grandfathered, so v0.1 and v0.2 set IDs keep saving until stored IDs
    are migrated; a new folder, however the show was built, must pass check_id.

    Args:
        before: Paths of the base revision's tree.
        written: Paths the commit writes.

    >>> require_new_set_keys(["/sets/con/set.json"], ["/sets/con/set.py", "/sets/2/set.py"])
    >>> try:
    ...     require_new_set_keys([], ["/sets/con/set.json"])
    ... except SchemaError as exc:
    ...     print(exc.context["kind"])
    set key
    """
    stored = {key for path in before for key in _set_keys_in(path)}
    _require_pair_folders(before, written)
    for key in sorted({key for path in written for key in _set_keys_in(path)} - stored):
        if check_id(key, kind="set key") != key:
            raise SchemaError(
                f"set key {key!r} is not in NFC form",
                hint="write set keys as plain ASCII letters, digits, _ and .",
                context={"kind": "set key", "text": key},
            )


def require_distinct_folders(before: Iterable[str], after: Iterable[str]) -> None:
    """Refuses a set folder new to the tree that matches another folder ignoring case.

    Folders already stored are grandfathered, since v0.2 stored set IDs differing only by case.

    Args:
        before: Paths of the base revision's tree.
        after: Paths of the tree the commit would leave.

    >>> require_distinct_folders(["/sets/1a/set.json"], ["/sets/1a/set.json", "/sets/2/set.py"])
    >>> try:
    ...     require_distinct_folders(["/sets/1a/set.json"], ["/sets/1a/set.json", "/sets/1A/x"])
    ... except SchemaError as exc:
    ...     print(exc.context["folders"])
    ['1A', '1a']
    """
    stored = {key for path in before for key in _set_keys_in(path)}
    keys = {key for path in after for key in _set_keys_in(path)}
    folded: dict[str, set[str]] = {}
    for key in keys:
        folded.setdefault(key.casefold(), set()).add(key)
    groups = [folded[key.casefold()] for key in keys - stored]
    clashes = sorted({key for group in groups if len(group) > 1 for key in group})
    if clashes:
        raise SchemaError(
            f"set folders repeat ignoring case: {', '.join(clashes)}",
            hint="give every set a key unique ignoring case; add_set() picks a free one",
            context={"folders": clashes},
        )


def require_structured_files(
    changed: Iterable[str], after: Mapping[str, str], blob: Callable[[str], bytes]
) -> None:
    """Checks every changed text file outside the show documents, loading only those blobs.

    Show documents are decoded with the show; files read as raw bytes are never loaded.

    Args:
        changed: Paths the commit touches.
        after: Path to blob digest the commit would leave.
        blob: Returns the bytes of a digest.

    >>> require_structured_files(["/canvas/comments.json"], {"/canvas/comments.json": "d"},
    ...                         lambda digest: b"[]")
    """
    for path in sorted(changed):
        if path in after and path.endswith(TEXT_SUFFIXES) and not is_show_doc(path):
            require_structured(path, blob(after[path]))


def _require_current_format(
    digest: str, blob: Callable[[str], bytes], cache: DecodeCache | None
) -> None:
    """Checks show.json is in the current format version, once per digest with a cache."""
    check = partial(_check_current_format, digest, blob)
    if cache is None:
        check()
    else:
        cache.get_or_decode(SHOW_DOC, f"{digest} {FORMAT_CHECKED}", check)


def _check_current_format(digest: str, blob: Callable[[str], bytes]) -> None:
    require_current_show_doc(blob(digest))


def require_valid_digests(
    before: Mapping[str, str],
    after: Mapping[str, str],
    blob: Callable[[str], bytes],
    cache: DecodeCache | None = None,
) -> Show | None:
    """Checks every changed path and decodes the show when any show document changed.

    Writing a derived table raises LockedError code READ_ONLY; deleting a stored copy is allowed.
    A written font document must be named by the SHA-256 of its bytes and load as a font;
    deleting one is allowed.
    When any show document changes, the show.json the commit leaves must be in the current
    format version, else SchemaError code OLD_FORMAT: show.json versions the whole tree.
    Documents whose digest sits in `cache` at the same path are reused, so a commit loads and
    decodes only the documents it changed; the show model is validated whole either way, so
    every rule across documents is checked on every commit that changes a show document.

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
        require_known_path(path, is_write=path in after)
        if path in after:
            require_writable(path)
    written = [p for p in changed if p in after]
    require_new_set_keys(before, written)
    require_distinct_folders(before, after)
    require_written_fonts(written, after, blob, cache)
    require_structured_files(changed, after, blob)
    if not any(is_show_doc(p) for p in changed):
        return None
    if SHOW_DOC in after:
        _require_current_format(after[SHOW_DOC], blob, cache)
    _require_required_docs(after)
    require_cast_beside_roles(after)
    show = decode_show(LazyBlobs(after, blob), cache=cache, digests=after)
    require_storable(show)
    _require_no_orphans(show, written)
    return show


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
