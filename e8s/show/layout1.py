"""
layout1.py --- reads layout-1 repositories, which keep revisions as JSON and blob files under a root

Contains:
    LEGACY_REVISION_KEYS: names the authorship keys v0.1 and v0.2 stored in revision records
    LINK_MAX_BYTES: caps the size of a next/ link file read
    TREE_MAX_BYTES: caps the size of a tree record read
    BLOB_MAX_BYTES: caps the size of a blob written or read
    UPGRADED_SUFFIX: ends the folder name the OLD_LAYOUT hint suggests for the upgraded copy
    old_layout_error(): builds the error every write to a layout-1 root raises
    Layout1Reader: reads one folder per show with objects, trees and next/ links, writing nothing
"""

import errno
import functools
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, VersionError
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show.backend import (
    MAX_REVISIONS,
    REVISION_ID_PATTERN,
    history_too_long_error,
    revisions_after,
    unknown_revision_error,
)
from e8s.show.durable_io import mapped_io
from e8s.show.durable_reads import lstat_mode, read_capped
from e8s.show.history_models import STORED_TIME_KEY, Revision
from e8s.show.integrity import blob_digest
from e8s.show.storage import ROOT_REVISION

ROOT_LINK = "root"
TREE_DIR = "trees"
LINK_DIR = "next"
OBJECT_DIR = "objects"
TEMP_PREFIX = ".tmp-"
LEGACY_REVISION_KEYS = frozenset({"actor", "message", "kind", "target"})
RECORD_PARTS = frozenset({"revision", "tree"})
# A link holds one 64-digit hex ID; v0.1 and v0.2 may have added a newline. Rounded up.
LINK_MAX_BYTES = 128
# A 600-spot x 120-set show's tree record is 47 KB for 362 documents (130 bytes each, measured
# 2026-11-16); 8 MiB holds about 60,000 entries, room for renders, exports and drafts.
TREE_MAX_BYTES = 8 * 2**20
# That show's largest document is 333 KB (a transition's paths.json, measured 2026-11-16).
# /exports/ and /renders/ also hold rendered PDFs and videos, which can reach hundreds of MB,
# so 1 GiB leaves headroom over both while refusing a sparse terabyte.
BLOB_MAX_BYTES = 2**30
UPGRADED_SUFFIX = "-layout2"
UPGRADED_FALLBACK = "repository"


def old_layout_error(root: Path, show_id: str = "", **refused: JsonValue) -> VersionError:
    """Builds the VersionError OLD_LAYOUT every write to a layout-1 root raises.

    Args:
        root: Root folder of the layout-1 repository.
        show_id: Show the write was for; empty for a repository-wide write.
        refused: What the refused write held, added to the error's context.

    Returns:
        error: The error, whose hint is a call that copies the root into layout 2.

    >>> error = old_layout_error(Path("repo"))
    >>> error.code, "repo-layout2" in error.hint
    (<ErrorCode.OLD_LAYOUT: 'OLD_LAYOUT'>, True)
    """
    folder = root.absolute()
    upgraded = folder.parent / f"{folder.name or UPGRADED_FALLBACK}{UPGRADED_SUFFIX}"
    context: dict[str, JsonValue] = {"root": str(root), "layout": 1, **refused}
    if show_id:
        context["show"] = show_id
    return VersionError(
        f"repository at {root} is layout 1, which this release reads but never writes",
        hint=(
            f"copy it into layout 2 with e8s.upgrade_repository(Path({str(root)!r}), "
            f"Path({str(upgraded)!r})), then open the new folder; the old one is never changed"
        ),
        context=context,
        code=ErrorCode.OLD_LAYOUT,
    )


def _guarded[F: Callable[..., object]](method: F) -> F:
    """Maps any OSError a backend method leaks to StorageError; `show_id` is its first argument."""

    @functools.wraps(method)
    def wrapper(self: "Layout1Reader", show_id: str, *args: object, **kwargs: object) -> object:
        with mapped_io(show_id, self.root / show_id):
            return method(self, show_id, *args, **kwargs)

    return cast(F, wrapper)


class Layout1Reader:
    """Reads `<root>/<show>/objects/<sha256>`, `trees/<rev>.json` and `next/<parent>`.

    The chain of `next/<parent>` files is the history: each holds its child's ID. Every read
    checks the chain, the records and the blob digests as the layout-1 writer left them; every
    write raises VersionError OLD_LAYOUT, whose hint copies the root into layout 2.

    Attributes:
        root: Root folder of the layout-1 repository.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     Layout1Reader(Path(folder)).shows()
    []
    """

    def __init__(self, root: Path):
        self.root = root

    def _dir(self, show_id: str) -> Path:
        return self.root / show_id

    def shows(self) -> list[str]:
        """Returns the names of the folders that hold a next/root link, one per show."""
        with mapped_io("", self.root):
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

    def _guard_dirs(self, show_id: str) -> None:
        """Refuses symlinked store directories and reports a file standing in for one as ENOTDIR.

        Windows reports a path through a regular file as missing, not ENOTDIR, so the check runs
        here instead of relying on the open of a file below it.
        """
        base = self._dir(show_id)
        for directory in (base, base / OBJECT_DIR, base / TREE_DIR, base / LINK_DIR):
            mode = lstat_mode(show_id, directory)
            if mode is None:
                continue
            if stat.S_ISLNK(mode):
                raise self._corrupt(show_id, directory, "directory is a symbolic link")
            if not stat.S_ISDIR(mode):
                raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(directory))

    def _read(self, show_id: str, path: Path, kind: str, cap: int) -> bytes:
        """Reads a regular file of at most `cap` bytes, checking its size before reading."""
        return read_capped(
            path, kind, cap, lambda source, reason: self._corrupt(show_id, source, reason)
        )

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
        """Returns the last revision of the chain, or the root revision for an unknown show."""
        log = self.log(show_id)
        return log[-1].id if log else ROOT_REVISION

    @_guarded
    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        """Returns the revisions after `since`, oldest first, walking and checking the chain."""
        return revisions_after(show_id, self._walk(show_id), since)

    def _walk(self, show_id: str) -> list[Revision]:
        self._guard_dirs(show_id)
        links = self._dir(show_id) / LINK_DIR
        log: list[Revision] = []
        seen: set[str] = set()
        current = ROOT_LINK
        while lstat_mode(show_id, links / current) is not None:
            link = links / current
            previous = "" if current == ROOT_LINK else current
            current = self._checked_id(show_id, self._read_link(show_id, link), link)
            if current in seen:
                raise self._corrupt(show_id, link, "next/ links form a cycle")
            seen.add(current)
            revision = self._stored_revision(show_id, current, link)
            self._check_revision(show_id, revision, current, previous, link)
            log.append(revision)
            if len(log) > MAX_REVISIONS:
                raise history_too_long_error(show_id, MAX_REVISIONS)
        return log

    def _stored_revision(self, show_id: str, revision_id: str, source: Path) -> Revision:
        """Decodes a stored revision; v0.1 and v0.2 records lose their authorship keys.

        A time stored with no timezone reads as UTC.

        The file is never rewritten: its ID still hashes the dropped keys, and stays as stored.
        """
        stored = self._record(show_id, revision_id, source)["revision"]
        if isinstance(stored, dict):
            stored = {k: v for k, v in stored.items() if k not in LEGACY_REVISION_KEYS}
        try:
            return Revision._validated(stored, context={STORED_TIME_KEY: True})
        except SchemaError as error:
            path = self._tree_path(show_id, revision_id, source)
            raise self._corrupt(show_id, path, "stored revision is malformed") from error

    @_guarded
    def dropped_fields(self, show_id: str, revision_id: str) -> tuple[str, ...]:
        """Names the stored fields of a revision's record that reading it leaves out.

        Args:
            show_id: The show.
            revision_id: A revision the chain holds.

        Returns:
            dropped: `revision.<key>` for each authorship key v0.1 and v0.2 stored and
                `record.<key>` for each key beside the revision and tree, sorted.
        """
        folder = self._dir(show_id) / TREE_DIR
        record = self._record(show_id, revision_id, folder)
        stored = record["revision"]
        names = [f"record.{key}" for key in record if key not in RECORD_PARTS]
        if isinstance(stored, dict):
            names += [f"revision.{key}" for key in stored if key in LEGACY_REVISION_KEYS]
        return tuple(sorted(names))

    def _check_revision(
        self, show_id: str, revision: Revision, expected_id: str, parent: str, source: Path
    ) -> None:
        if revision.id != expected_id:
            raise self._corrupt(show_id, source, "revision id differs from its link")
        if revision.parent != parent:
            raise self._corrupt(show_id, source, "revision parent differs from the chain")

    @_guarded
    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        """Returns path to blob digest at a revision, read from its tree record."""
        self._guard_dirs(show_id)
        folder = self._dir(show_id) / TREE_DIR
        known_shape = REVISION_ID_PATTERN.fullmatch(revision_id)
        if not known_shape or lstat_mode(show_id, folder / f"{revision_id}.json") is None:
            raise unknown_revision_error(show_id, revision_id, [])
        path = self._tree_path(show_id, revision_id, folder)
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
        """Returns the bytes of a blob after checking they hash to its digest."""
        self._guard_dirs(show_id)
        objects = self._dir(show_id) / OBJECT_DIR
        return self._verified_blob(show_id, objects / self._checked_id(show_id, digest, objects))

    def _verified_blob(self, show_id: str, path: Path) -> bytes:
        """Reads a stored object, raising CORRUPT unless its bytes hash to its file name."""
        data = self._read(show_id, path, "blob", BLOB_MAX_BYTES)
        if blob_digest(data) != path.name:
            raise self._corrupt(show_id, path, "blob bytes do not match their digest")
        return data

    def commit(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        """Refuses every write: a layout-1 root is only read, until it is upgraded.

        The error names the refused revision and counts its paths and new blobs.
        """
        raise old_layout_error(
            self.root, show_id, revision=revision.id, paths=len(tree), blobs=len(blobs)
        )
