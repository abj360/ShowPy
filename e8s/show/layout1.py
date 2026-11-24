"""
layout1.py --- the layout-1 file backend: revisions as JSON and blob files under a root

Contains:
    REVISION_ID_PATTERN: matches the 64-digit hex IDs allowed in next/ links and tree digests
    LEGACY_REVISION_KEYS: names the authorship keys v0.1 and v0.2 stored in revision records
    NO_HARDLINK_ERRNOS: lists the errno values meaning the file system cannot hard-link
    WINDOWS_INVALID_NAME: names the Windows error code for an unusable path component
    COMPONENT_MAX_CHARS: caps the length of one path component on NTFS
    READ_FLAGS: opens stored files without following symlinks or blocking on FIFOs
    SYMLINK_OPEN_ERRNOS: lists the errno values an O_NOFOLLOW open of a symlink raises
    LINK_MAX_BYTES: caps the size of a next/ link file read
    TREE_MAX_BYTES: caps the size of a tree record read
    BLOB_MAX_BYTES: caps the size of a blob written or read
    _FileBackend: lays out one folder per show with objects, trees and next/ links
"""

import errno
import functools
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, cast

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show.backend import conflict_error, revisions_after
from e8s.show.documents import dump_json
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.storage import ROOT_REVISION

ROOT_LINK = "root"
TREE_DIR = "trees"
LINK_DIR = "next"
OBJECT_DIR = "objects"
TEMP_PREFIX = ".tmp-"
REVISION_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LEGACY_REVISION_KEYS = frozenset({"actor", "message", "kind", "target"})
NO_HARDLINK_ERRNOS = frozenset({errno.EPERM, errno.EXDEV, errno.ENOTSUP})
# Windows has no ENAMETOOLONG for a component over 255 characters: with long paths enabled it
# raises ERROR_INVALID_NAME (errno EINVAL), which also covers bad characters, so the length
# decides. NTFS caps one component at 255 UTF-16 units.
WINDOWS_INVALID_NAME = 123
COMPONENT_MAX_CHARS = 255
# A file swapped in between the lstat check and the open must not be followed (symlink) or
# block the open (FIFO), so the open itself refuses both and fstat checks the descriptor.
# Windows has neither flag; there only the lstat check guards, and creating symlinks needs
# privilege. O_BINARY (Windows only) keeps the C runtime from translating line endings.
READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)
# open(O_NOFOLLOW) on a symlink fails with ELOOP on Linux and macOS, EMLINK on FreeBSD.
SYMLINK_OPEN_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK})
# A link holds one 64-digit hex ID; v0.1 and v0.2 may have added a newline. Rounded up.
LINK_MAX_BYTES = 128
# A 600-spot x 120-set show's tree record is 47 KB for 362 documents (130 bytes each, measured
# 2026-11-16); 8 MiB holds about 60,000 entries, room for renders, exports and drafts.
TREE_MAX_BYTES = 8 * 2**20
# That show's largest document is 333 KB (a transition's paths.json, measured 2026-11-16).
# /exports/ and /renders/ also hold rendered PDFs and videos, which can reach hundreds of MB,
# so 1 GiB leaves headroom over both while refusing a sparse terabyte.
BLOB_MAX_BYTES = 2**30


def _temp_copy(directory: Path, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False, prefix=TEMP_PREFIX) as handle:
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise
    return Path(handle.name)


def _link_exclusive(show_id: str, path: Path, data: bytes) -> bool:
    """Creates `path` with `data` only if it does not exist; os.link refuses to overwrite."""
    temp = _temp_copy(path.parent, data)
    try:
        with _mapped_io(show_id, path, is_link=True):
            try:
                os.link(temp, path)
            except FileExistsError:
                return False
    finally:
        temp.unlink(missing_ok=True)
    return True


def _is_name_too_long(error: OSError, path: str) -> bool:
    if error.errno == errno.ENAMETOOLONG:
        return True
    return getattr(error, "winerror", None) == WINDOWS_INVALID_NAME and any(
        len(part) > COMPONENT_MAX_CHARS for part in Path(path).parts
    )


def _storage_error(show_id: str, error: OSError, fallback: Path, *, is_link: bool) -> StorageError:
    path = str(error.filename or fallback)
    context: dict[str, object] = {"show": show_id, "path": path, "errno": error.errno}
    if _is_name_too_long(error, path):
        winerror = getattr(error, "winerror", None)
        return StorageError(
            f"name too long for {show_id}: {path}",
            hint="use a shorter show ID or a repository root with a shorter path",
            context={**context, "errno": errno.ENAMETOOLONG}
            | ({"winerror": winerror} if winerror is not None else {}),
            code=ErrorCode.NAME_TOO_LONG,
        )
    if is_link and (isinstance(error, PermissionError) or error.errno in NO_HARDLINK_ERRNOS):
        return StorageError(
            f"file system under {path} cannot create hard links",
            hint="put the repository root on a file system that supports hard links",
            context=context,
            code=ErrorCode.NO_HARDLINKS,
        )
    return StorageError(
        f"file-system error in repository of {show_id}: {error.strerror or error}",
        hint="check that the repository root exists, is writable and is a directory",
        context=context,
        code=ErrorCode.IO,
    )


@contextmanager
def _mapped_io(show_id: str, path: Path, *, is_link: bool = False) -> Iterator[None]:
    """Turns an OSError raised inside the block into a StorageError with a code and hint."""
    try:
        yield
    except OSError as error:
        raise _storage_error(show_id, error, path, is_link=is_link) from error


def _guarded[F: Callable[..., Any]](method: F) -> F:  # Any: decorator keeps each signature
    """Maps any OSError a backend method leaks to StorageError; `show_id` is its first argument."""

    @functools.wraps(method)
    def wrapper(self: "_FileBackend", show_id: str, *args: Any, **kwargs: Any) -> Any:
        with _mapped_io(show_id, self.root / show_id):
            return method(self, show_id, *args, **kwargs)

    return cast(F, wrapper)


def _lstat_mode(show_id: str, path: Path) -> int | None:
    """Returns the lstat mode of `path`, or None if absent; ENAMETOOLONG is never hidden."""
    with _mapped_io(show_id, path):
        try:
            return os.lstat(path).st_mode
        except FileNotFoundError:
            return None


def _require_readable_sizes(
    show_id: str, tree: Mapping[str, str], blobs: Mapping[str, bytes]
) -> None:
    """Refuses blobs over BLOB_MAX_BYTES before any write, since reads would refuse them."""
    oversized = {d for d, data in blobs.items() if len(data) > BLOB_MAX_BYTES}
    if not oversized:
        return
    paths = sorted(p for p, d in tree.items() if d in oversized)
    raise StorageError(
        f"files of {show_id} over the {BLOB_MAX_BYTES}-byte cap: {', '.join(paths)}",
        hint="keep each file under the cap; store larger media outside the repository",
        context={"show": show_id, "paths": paths, "cap": BLOB_MAX_BYTES},
        code=ErrorCode.TOO_LARGE,
    )


class _FileBackend:
    """Lays out `<root>/<show>/objects/<sha256>`, `trees/<rev>.json` and `next/<parent>`.

    The chain of `next/<parent>` files is the history: each holds its child's ID, and creating
    one with os.link is the atomic compare-and-swap that makes a commit land.
    """

    def __init__(self, root: Path):
        self.root = root

    def _dir(self, show_id: str) -> Path:
        return self.root / show_id

    def shows(self) -> list[str]:
        with _mapped_io("", self.root):
            if not self.root.exists():
                return []
            return [p.name for p in self.root.iterdir() if (p / LINK_DIR / ROOT_LINK).exists()]

    def _corrupt(self, show_id: str, path: Path, reason: str) -> StorageError:
        return StorageError(
            f"repository of {show_id} is corrupt: {reason}",
            hint="restore the show folder from a backup or remove the damaged file",
            context={"show": show_id, "path": str(path), "file": path.name},
            code=ErrorCode.CORRUPT,
        )

    def _checked_id(self, show_id: str, value: str, source: Path) -> str:
        if not REVISION_ID_PATTERN.fullmatch(value):
            raise self._corrupt(show_id, source, f"{value!r} is not a 64-digit hex id")
        return value

    def _caller_id(self, show_id: str, value: str, role: str) -> str:
        if not REVISION_ID_PATTERN.fullmatch(value):
            raise StorageError(
                f"{role} {value!r} is not a 64-digit hex id",
                hint="revision IDs and blob digests are 64 lowercase hex digits made by "
                "DocumentRepository.transaction",
                context={"show": show_id, role: value},
                code=ErrorCode.BAD_ID,
            )
        return value

    def _guard_dirs(self, show_id: str) -> None:
        """Refuses symlinked store directories and reports a file standing in for one as ENOTDIR.

        Windows reports a path through a regular file as missing, not ENOTDIR, so the check runs
        here instead of relying on the open of a file below it.
        """
        base = self._dir(show_id)
        for directory in (base, base / OBJECT_DIR, base / TREE_DIR, base / LINK_DIR):
            mode = _lstat_mode(show_id, directory)
            if mode is None:
                continue
            if stat.S_ISLNK(mode):
                raise self._corrupt(show_id, directory, "directory is a symbolic link")
            if not stat.S_ISDIR(mode):
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(directory))

    def _read(self, show_id: str, path: Path, kind: str, cap: int) -> bytes:
        """Reads a regular file of at most `cap` bytes, checking its size before reading."""
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise self._corrupt(show_id, path, "file is a symbolic link")
            if not stat.S_ISREG(mode):
                raise self._corrupt(show_id, path, "file is missing or not a regular file")
            with os.fdopen(self._open_no_follow(show_id, path), "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise self._corrupt(show_id, path, "file is missing or not a regular file")
                return self._read_sized(show_id, path, handle, kind, cap)
        except (FileNotFoundError, IsADirectoryError) as error:
            raise self._corrupt(show_id, path, "file is missing or not a file") from error

    def _open_no_follow(self, show_id: str, path: Path) -> int:
        """Opens `path` for reading without following a symlink or blocking on a FIFO."""
        try:
            return os.open(path, READ_FLAGS)
        except OSError as error:
            if error.errno in SYMLINK_OPEN_ERRNOS:
                raise self._corrupt(show_id, path, "file is a symbolic link") from error
            raise

    def _read_sized(self, show_id: str, path: Path, handle: BinaryIO, kind: str, cap: int) -> bytes:
        """Reads an open file by its fstat size, which `read(n)` allocates up front."""
        size = os.fstat(handle.fileno()).st_size
        if size > cap:
            raise self._corrupt(show_id, path, f"{kind} is larger than {cap} bytes")
        data = handle.read(size + 1)
        if len(data) > size:
            raise self._corrupt(show_id, path, f"{kind} grew while it was read")
        return data

    def _read_link(self, show_id: str, path: Path) -> str:
        try:
            return self._read(show_id, path, "link", LINK_MAX_BYTES).decode().strip()
        except UnicodeDecodeError as error:
            raise self._corrupt(show_id, path, "link is not valid UTF-8") from error

    def _tree_path(self, show_id: str, revision_id: str, source: Path) -> Path:
        name = self._checked_id(show_id, revision_id, source)
        return self._dir(show_id) / TREE_DIR / f"{name}.json"

    def _record(self, show_id: str, revision_id: str, source: Path) -> dict[str, JsonValue]:
        path = self._tree_path(show_id, revision_id, source)
        raw = self._read(show_id, path, "tree record", TREE_MAX_BYTES)
        try:
            record = parse_strict_json(raw, document="tree record")
        except SchemaError as error:
            raise self._corrupt(
                show_id, path, "tree file is not valid JSON or too deeply nested"
            ) from error
        if not isinstance(record, dict) or "revision" not in record or "tree" not in record:
            raise self._corrupt(show_id, path, "tree file lacks revision or tree")
        return record

    @_guarded
    def head(self, show_id: str) -> str:
        log = self.log(show_id)
        return log[-1].id if log else ROOT_REVISION

    @_guarded
    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        return revisions_after(show_id, self._walk(show_id), since)

    def _walk(self, show_id: str) -> list[Revision]:
        self._guard_dirs(show_id)
        links = self._dir(show_id) / LINK_DIR
        log: list[Revision] = []
        seen: set[str] = set()
        current = ROOT_LINK
        while _lstat_mode(show_id, links / current) is not None:
            link = links / current
            previous = "" if current == ROOT_LINK else current
            current = self._checked_id(show_id, self._read_link(show_id, link), link)
            if current in seen:
                raise self._corrupt(show_id, link, "next/ links form a cycle")
            seen.add(current)
            revision = self._stored_revision(show_id, current, link)
            self._check_revision(show_id, revision, current, previous, link)
            log.append(revision)
        return log

    def _stored_revision(self, show_id: str, revision_id: str, source: Path) -> Revision:
        """Decodes a stored revision; v0.1 and v0.2 records lose their authorship keys.

        The file is never rewritten: its ID still hashes the dropped keys, and stays as stored.
        """
        stored = self._record(show_id, revision_id, source)["revision"]
        if isinstance(stored, dict):
            stored = {k: v for k, v in stored.items() if k not in LEGACY_REVISION_KEYS}
        return Revision._validated(stored)

    def _check_revision(
        self, show_id: str, revision: Revision, expected_id: str, parent: str, source: Path
    ) -> None:
        if revision.id != expected_id:
            raise self._corrupt(show_id, source, "revision id differs from its link")
        if revision.parent != parent:
            raise self._corrupt(show_id, source, "revision parent differs from the chain")

    @_guarded
    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        self._guard_dirs(show_id)
        path = self._tree_path(show_id, revision_id, self._dir(show_id) / TREE_DIR)
        stored = self._record(show_id, revision_id, path)["tree"]
        if not isinstance(stored, dict):
            raise self._corrupt(show_id, path, "tree is not a mapping of paths to digests")
        tree: dict[str, str] = {}
        for key, value in stored.items():
            if not isinstance(value, str):
                raise self._corrupt(show_id, path, "tree is not a mapping of paths to digests")
            tree[key] = self._checked_id(show_id, value, path)
        return tree

    @_guarded
    def blob(self, show_id: str, digest: str) -> bytes:
        self._guard_dirs(show_id)
        objects = self._dir(show_id) / OBJECT_DIR
        return self._verified_blob(show_id, objects / self._checked_id(show_id, digest, objects))

    def _verified_blob(self, show_id: str, path: Path) -> bytes:
        """Reads a stored object, raising CORRUPT unless its bytes hash to its file name."""
        data = self._read(show_id, path, "blob", BLOB_MAX_BYTES)
        if blob_digest(data) != path.name:
            raise self._corrupt(show_id, path, "blob bytes do not match their digest")
        return data

    def _store_record(
        self, show_id: str, path: Path, revision: Revision, tree: Mapping[str, str]
    ) -> None:
        """Links the tree record into place, or verifies the record already there byte for byte.

        Files are linked, never replaced, because Windows refuses to replace a file another
        writer holds. The record's name is the revision ID, which hashes the parent, time and
        changes, so a racing identical commit writes the same bytes. Anything else under the
        name (another tree, a truncated file, a symlink) blocks the commit before it lands.
        """
        record = dump_json({"revision": revision.to_json(), "tree": tree})
        if _link_exclusive(show_id, path, record):
            return
        if self._read(show_id, path, "tree record", TREE_MAX_BYTES) != record:
            raise self._corrupt(show_id, path, "tree record differs from this commit")

    @_guarded
    def commit(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        self._guard_dirs(show_id)
        base = self._dir(show_id)
        tree_name = self._caller_id(show_id, revision.id, "revision id")
        if revision.parent:
            self._caller_id(show_id, revision.parent, "parent id")
        _require_readable_sizes(show_id, tree, blobs)
        for digest, data in blobs.items():
            target = base / OBJECT_DIR / self._caller_id(show_id, digest, "blob digest")
            if _lstat_mode(show_id, target) is None and _link_exclusive(show_id, target, data):
                continue
            # A blob already stored, or linked first by a racing writer, is kept only when it
            # holds these bytes; a symlink or damaged file blocks the commit before it lands.
            self._verified_blob(show_id, target)
        self._store_record(show_id, base / TREE_DIR / f"{tree_name}.json", revision, tree)
        link = base / LINK_DIR / (revision.parent or ROOT_LINK)
        if not _link_exclusive(show_id, link, tree_name.encode()):
            raise conflict_error(show_id, revision.parent, self.log(show_id, since=revision.parent))
