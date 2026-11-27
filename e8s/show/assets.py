"""
assets.py --- the in-memory and file-backed asset stores

Contains:
    InMemoryAssetStore: keeps assets in process memory under content-hash URIs
    FileAssetStore: keeps assets as files named by their SHA-256 under a root folder
"""

import errno
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    SchemaError,
    StorageError,
    UnknownRefError,
)
from e8s.core.util import ASSET_PREFIX, asset_uri
from e8s.show.durable_io import NO_HARDLINK_ERRNOS
from e8s.show.durable_reads import read_capped
from e8s.show.integrity import blob_digest
from e8s.show.layout import ASSET_DIR, holds_layout_1, require_usable_path
from e8s.show.layout1 import BLOB_MAX_BYTES, TEMP_PREFIX, old_layout_error

TEMP_DIR = "tmp"
ASSET_NAME_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
ASSET_FILE_MODE = 0o644


class InMemoryAssetStore:
    """Keeps assets in process memory under `asset:sha256:<hex>` URIs."""

    def __init__(self) -> None:
        self._assets: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; equal bytes give the same URI.

        >>> InMemoryAssetStore().put(b"x").startswith(ASSET_PREFIX)
        True
        """
        uri = asset_uri(data)
        self._assets[uri] = data
        return uri

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        _require_asset_uri(uri)
        if uri not in self._assets:
            raise _unknown_asset(uri)
        return self._assets[uri]


def _require_asset_uri(uri: str) -> None:
    if not uri.startswith(ASSET_PREFIX):
        raise SchemaError(
            f"{uri!r} is not an asset URI",
            hint=f"pass a URI returned by put(), starting with {ASSET_PREFIX}",
            context={"uri": uri},
        )


def _unknown_asset(uri: str) -> UnknownRefError:
    return UnknownRefError(
        f"no asset {uri}", hint="store it first with put()", context={"uri": uri}
    )


def _io_error(error: OSError, path: Path) -> StorageError:
    shown = str(error.filename or path)
    context: dict[str, object] = {"path": shown, "errno": error.errno}
    if error.errno == errno.ENAMETOOLONG:
        return StorageError(
            f"name too long for asset store: {shown}",
            hint="use an asset root with a shorter path",
            context=context,
            code=ErrorCode.NAME_TOO_LONG,
        )
    return StorageError(
        f"file-system error in asset store: {error.strerror or error}",
        hint="check that the asset root exists, is writable and is a directory",
        context=context,
        code=ErrorCode.IO,
    )


@contextmanager
def _mapped_io(path: Path) -> Iterator[None]:
    try:
        yield
    except OSError as error:
        raise _io_error(error, path) from error


def _flush_directory(directory: Path) -> None:
    if sys.platform == "win32":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_in_place(path: Path, data: bytes) -> None:
    try:
        descriptor = os.open(path, CREATE_FLAGS, ASSET_FILE_MODE)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _lacks_hard_links(error: OSError) -> bool:
    return isinstance(error, PermissionError) or error.errno in NO_HARDLINK_ERRNOS


def _link_from_temp(temp_dir: Path, path: Path, data: bytes) -> None:
    temp_dir.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=temp_dir, prefix=TEMP_PREFIX)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            return
        except OSError as error:
            if not _lacks_hard_links(error):
                raise
            _create_in_place(path, data)
    finally:
        temp.unlink(missing_ok=True)


def _corrupt(uri: str, path: Path, reason: str) -> StorageError:
    return StorageError(
        f"asset {uri} at {path}: {reason}",
        hint="restore the file from a backup, or remove it and store the asset again with put()",
        context={"uri": uri, "path": str(path)},
        code=ErrorCode.CORRUPT,
    )


def _read_failure(uri: str) -> Callable[[Path, str], Exception]:
    def fail(path: Path, reason: str) -> Exception:
        if "larger than" not in reason:
            return _corrupt(uri, path, reason)
        return StorageError(
            f"asset {uri} at {path}: {reason}",
            hint="keep large media outside the asset store",
            context={"uri": uri, "path": str(path), "cap": BLOB_MAX_BYTES},
            code=ErrorCode.TOO_LARGE,
        )

    return fail


class FileAssetStore:
    """Keeps assets as files named by their SHA-256 under `<root>/assets/`.

    Each new asset is written to a temp file under `<root>/tmp/`, flushed and linked into place,
    so an existing asset is never overwritten; where the file system cannot hard-link, the file
    is created in place exclusively. Reads re-hash the bytes and raise `StorageError` (code
    `CORRUPT`) when they do not match the name. A layout-1 root is read-only: `put` raises
    `VersionError` (code `OLD_LAYOUT`) there, whose hint copies the root into layout 2.

    Attributes:
        root: Folder holding the `assets/` and `tmp/` folders.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     store = FileAssetStore(Path(folder))
    ...     store.get(store.put(b"audio"))
    b'audio'
    """

    def __init__(self, root: Path) -> None:
        require_usable_path(root)
        self.root = root
        self._is_writable = False

    def _require_writable(self) -> None:
        """Refuses a layout-1 root, which this release only reads; once checked, never again.

        No release writes a layout-1 root any more, so a root found to be another layout stays
        writable.
        """
        if self._is_writable:
            return
        if holds_layout_1(self.root):
            raise old_layout_error(self.root)
        self._is_writable = True

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; equal bytes give the same URI.

        An existing file of the right name is not re-verified: `get` reports a torn one as
        `CORRUPT`, and its hint says to remove the file and store the asset again.

        Args:
            data: Exact bytes of the asset.

        Returns:
            uri: `asset:sha256:<hex>` naming the stored bytes.

        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as folder:
        ...     FileAssetStore(Path(folder)).put(b"x") == asset_uri(b"x")
        True
        """
        if len(data) > BLOB_MAX_BYTES:
            raise StorageError(
                f"asset of {len(data)} bytes is over the {BLOB_MAX_BYTES}-byte cap",
                hint="keep large media outside the asset store",
                context={"size": len(data), "cap": BLOB_MAX_BYTES},
                code=ErrorCode.TOO_LARGE,
            )
        self._require_writable()
        directory = self.root / ASSET_DIR
        path = directory / blob_digest(data)
        with _mapped_io(path):
            directory.mkdir(parents=True, exist_ok=True)
            if not os.path.lexists(path):
                _link_from_temp(self.root / TEMP_DIR, path, data)
                _flush_directory(directory)
        return asset_uri(data)

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI, re-hashed against its name.

        Args:
            uri: A URI returned by `put()`.

        Returns:
            data: The stored bytes.

        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as folder:
        ...     store = FileAssetStore(Path(folder))
        ...     store.get(store.put(b"x"))
        b'x'
        """
        _require_asset_uri(uri)
        name = uri.removeprefix(ASSET_PREFIX)
        if not ASSET_NAME_PATTERN.fullmatch(name):
            raise _unknown_asset(uri)
        path = self.root / ASSET_DIR / name
        with _mapped_io(path):
            if not os.path.lexists(path):
                raise _unknown_asset(uri)
            data = read_capped(path, "asset", BLOB_MAX_BYTES, _read_failure(uri))
        if blob_digest(data) != name:
            raise _corrupt(uri, path, "bytes do not match their SHA-256 name")
        return data
