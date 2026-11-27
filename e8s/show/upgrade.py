"""
upgrade.py --- copies a layout-1 repository into a new layout-2 root, never touching the source

Contains:
    upgrade_repository(): copies every show of a layout-1 root, with its whole history, into a
        new layout-2 root
"""

import hashlib
import stat
from collections.abc import Callable, Iterator
from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import durable_io
from e8s.show.assets import ASSET_DIR
from e8s.show.backend_checks import FIRST_SEQ, ReadBudget, corrupt, recomputes
from e8s.show.commit_writes import LAYOUT_BYTES, FilePlacer, record_bytes, slot_bytes
from e8s.show.document_dump import dump_json
from e8s.show.durable_io import mapped_io
from e8s.show.durable_reads import (
    existing_ancestor,
    file_pieces,
    is_own_file,
    lies_within,
    listed_entries,
    lstat_info,
    opened_capped,
    stat_info,
)
from e8s.show.file_backend import (
    LOG_DIR,
    LOG_READ_MAX_BYTES,
    MANIFEST_FILE,
    OBJECT_DIR,
    RECORD_DIR,
    SHOWS_DIR,
    SLOT_DIGITS,
)
from e8s.show.history_models import Revision
from e8s.show.layout import HOUSEKEEPING_NAMES, LAYOUT_FILE, LEGACY_LAYOUT, TEMP_DIR, read_layout
from e8s.show.layout1 import BLOB_MAX_BYTES, LINK_DIR, ROOT_LINK, TREE_DIR, Layout1Reader
from e8s.show.manifests import manifest_json
from e8s.show.show_folders import show_folder
from e8s.show.storage_models import ShowUpgrade, UpgradeReport
from e8s.show.upgrade_source import SourceListing, SourceShow, left_out, list_source

SOURCE_HINT = (
    "pass the root folder of a repository written before v0.5.0; a root that holds "
    f"{LAYOUT_FILE} needs no upgrade"
)
WRITER_HINT = (
    "stop every writer on the source, delete the target and run upgrade_repository again; no "
    "writer may run on the source while it is upgraded"
)
TARGET_HINT = (
    "pass a folder that does not exist yet, or an empty one, outside the source; a folder an "
    f"interrupted upgrade left has no {LAYOUT_FILE}: delete it and run upgrade_repository again"
)

DOTS_HINT = (
    "spell the target without `..` or `.` below folders that do not exist yet, or create those "
    "folders first"
)


def _bad_folder(message: str, hint: str, folder: Path) -> StorageError:
    return StorageError(message, hint=hint, context={"path": str(folder)}, code=ErrorCode.BAD_PATH)


def _not_a_folder(target: Path, blocker: Path) -> StorageError:
    if blocker == target:
        return _bad_folder(f"upgrade target {target} is not a folder", TARGET_HINT, target)
    message = f"upgrade target {target} runs through {blocker}, which is not a folder"
    return _bad_folder(message, TARGET_HINT, target)


def _require_layout_1(source: Path) -> None:
    """Refuses a source that holds no layout-1 repository."""
    if read_layout(source) != LEGACY_LAYOUT:
        raise _bad_folder(f"no layout-1 repository at {source} to upgrade", SOURCE_HINT, source)


def _require_plain_tail(target: Path, ancestor: Path, tail: tuple[str, ...]) -> None:
    """Refuses `..` or `.` below the deepest existing folder, and a dangling symlink there.

    Creating the missing folders would let the file system follow such a name anywhere, the
    source included, so the target is refused before anything is written.
    """
    if any(name in {".", ".."} for name in tail):
        message = f"upgrade target {target} names `..` or `.` below a folder that does not exist"
        raise _bad_folder(message, DOTS_HINT, target)
    if tail and lstat_info("", ancestor / tail[0]) is not None:
        raise _not_a_folder(target, ancestor / tail[0])


def _require_empty(target: Path, names: list[str]) -> None:
    """Refuses a target folder that holds a repository or any other entry."""
    stored = [name for name in names if name not in HOUSEKEEPING_NAMES]
    if LAYOUT_FILE in stored:
        message = f"upgrade target {target} already holds a repository"
        raise _bad_folder(message, TARGET_HINT, target)
    if stored:
        message = f"upgrade target {target} is not empty: it holds {', '.join(stored[:3])}"
        raise _bad_folder(message, TARGET_HINT, target)


def _free_target(source: Path, target: Path) -> Path:
    """Returns the folder to write an upgrade to, refusing one that could reach the source.

    The deepest existing ancestor of the target is resolved by the file system, and only plain
    names may follow it. That folder and the source are compared as files, not as names, so a
    target that reaches the source through another case, another Unicode normalization, a
    symlink or `..` through existing folders is refused; so is a target that is no folder or
    holds anything.

    Args:
        source: Root of the layout-1 repository.
        target: Folder the caller named for the layout-2 root.

    Returns:
        folder: The resolved existing ancestor of `target` joined with the names below it.
    """
    with mapped_io("", target):
        ancestor, tail = existing_ancestor(target)
        _require_plain_tail(target, ancestor, tail)
        if lies_within(ancestor, source):
            raise _bad_folder(f"upgrade target {target} is inside the source", TARGET_HINT, target)
        info = stat_info(ancestor)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise _not_a_folder(target, ancestor if tail else target)
        if tail:
            return ancestor.joinpath(*tail)
        names = [name for name, _ in listed_entries(ancestor, follows_links=True)]
    _require_empty(target, names)
    return ancestor


class _Target:
    """Writes the new layout-2 root: every file placed under a name nothing else holds.

    `shows/` is created first, so a target an upgrade leaves unfinished holds a folder and no
    layout file, which every reader refuses; `layout.json` is placed last.

    Attributes:
        root: The new root.
        links_blobs: Whether blobs are still hard-linked; a refused link switches to copies.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.links_blobs = True
        self.heads: dict[str, str] = {}
        with mapped_io("", root):
            durable_io.make_folder(root / SHOWS_DIR)
            durable_io.make_folder(root / TEMP_DIR)
        self._placer = FilePlacer(root / TEMP_DIR)

    def show_folder(self, show_id: str) -> Path:
        folder = self.root / SHOWS_DIR / show_folder(show_id)
        with mapped_io(show_id, folder):
            for name in (LOG_DIR, RECORD_DIR, OBJECT_DIR):
                durable_io.make_folder(folder / name)
        return folder

    def place(self, show_id: str, path: Path, data: bytes) -> None:
        """Places a new file; a name another writer took during the upgrade fails it."""
        if not self._placer.place(show_id, path, data):
            raise _taken(show_id, path)

    def transfer(self, show_id: str, source: Path, target: Path) -> bool:
        """Links a blob into place, or copies it where links are refused; True when linked.

        Both ways the bytes under the new name are hashed before the upgrade goes on, so a blob
        whose bytes do not match its name stops the upgrade before the layout file exists. A
        blob another user owns is copied: a linked file shares its inode with the source, and a
        later commit that reuses the blob sets its modification time, which only the owner may.
        """
        info = lstat_info(show_id, source)
        if self.links_blobs and info is not None and is_own_file(info):
            with mapped_io(show_id, target, is_link=True):
                is_linked = durable_io.link_file(source, target)
            if is_linked:
                _require_digest(show_id, source, _digest_of(show_id, target, source))
                return True
            if is_linked is False:
                raise _taken(show_id, target)
            self.links_blobs = False
        self._copy(show_id, source, target)
        return False

    def _copy(self, show_id: str, source: Path, target: Path) -> None:
        """Copies a blob a piece at a time through a temp file, checking its digest first."""
        hasher = hashlib.sha256()

        def hashed(pieces: Iterator[bytes]) -> Iterator[bytes]:
            for piece in pieces:
                hasher.update(piece)
                yield piece

        fail = _naming(show_id, source)
        with (
            mapped_io(show_id, source),
            opened_capped(source, "blob", BLOB_MAX_BYTES, fail) as handle,
        ):
            pieces = hashed(file_pieces(handle, BLOB_MAX_BYTES))
            temp = durable_io.write_temp_pieces(self.root / TEMP_DIR, pieces)
        with mapped_io(show_id, target):
            try:
                _require_digest(show_id, source, hasher.hexdigest())
                if not durable_io.move_new(temp, target):
                    raise _taken(show_id, target)
            finally:
                durable_io.remove_file(temp)

    def carry_assets(self, source: Path, names: tuple[str, ...]) -> int:
        """Links or copies each asset into `assets/`, verified; returns how many were linked."""
        folder = self.root / ASSET_DIR
        if not names:
            return 0
        with mapped_io("", folder):
            durable_io.make_folder(folder)
        linked = sum(self.transfer("", source / name, folder / name) for name in names)
        with mapped_io("", folder):
            durable_io.flush_directory(folder)
        return linked

    def flush_show(self, folder: Path) -> None:
        with mapped_io("", folder):
            for name in (OBJECT_DIR, RECORD_DIR, LOG_DIR):
                durable_io.flush_directory(folder / name)
            durable_io.flush_directory(folder)

    def finish(self) -> None:
        """Flushes the folders, then places `layout.json` and flushes the root fully."""
        with mapped_io("", self.root):
            durable_io.flush_directory(self.root / SHOWS_DIR)
            durable_io.flush_directory(self.root / TEMP_DIR)
            durable_io.flush_directory(self.root, follows_links=True)
            self.place("", self.root / LAYOUT_FILE, LAYOUT_BYTES)
        try:
            with mapped_io("", self.root):
                durable_io.flush_directory(self.root, is_commit_point=True, follows_links=True)
        except StorageError as error:
            raise StorageError(
                f"upgrade target {self.root} holds {LAYOUT_FILE} but its last flush failed, so it "
                f"may look complete but must not be used: {error.message}",
                hint="delete the target and run upgrade_repository again",
                context=error.context,
                code=error.code,
            ) from error


def _taken(show_id: str, path: Path) -> StorageError:
    return corrupt(show_id, path, "another writer placed this file during the upgrade")


def _naming(show_id: str, named: Path) -> Callable[[Path, str], StorageError]:
    """Builds the failure hook that reports a refused blob under the source path it came from."""

    def fail(_path: Path, reason: str) -> StorageError:
        return corrupt(show_id, named, reason)

    return fail


def _digest_of(show_id: str, path: Path, named: Path) -> str:
    """Hashes a stored blob a piece at a time; a refused file is reported under `named`."""
    hasher = hashlib.sha256()
    fail = _naming(show_id, named)
    with mapped_io(show_id, path), opened_capped(path, "blob", BLOB_MAX_BYTES, fail) as handle:
        for piece in file_pieces(handle, BLOB_MAX_BYTES):
            hasher.update(piece)
    return hasher.hexdigest()


def _require_digest(show_id: str, source: Path, found: str) -> None:
    if found != source.name:
        raise corrupt(show_id, source, "blob bytes do not match their digest")


def _upgrade_show(reader: Layout1Reader, target: _Target, listed: SourceShow) -> ShowUpgrade:
    """Copies one show's history slot by slot, its blobs first, and reports what it moved."""
    show_id = listed.show_id
    revisions = reader.log(show_id)
    target.heads[show_id] = revisions[-1].id
    folder = target.show_folder(show_id)
    budget = ReadBudget(show_id, LOG_READ_MAX_BYTES)
    blobs: set[str] = set()
    dropped: list[tuple[str, tuple[str, ...]]] = []
    linked = 0
    legacy = 0
    ids: list[str] = []
    for seq, revision in enumerate(revisions, start=FIRST_SEQ):
        tree = reader.tree(show_id, revision.id)
        ids.append(revision.id)
        if fields := reader.dropped_fields(show_id, revision.id):
            dropped.append((revision.id, fields))
        if not recomputes(revision, tree):
            legacy = seq
        for digest in sorted(set(tree.values()) - blobs):
            source = reader.root / show_id / OBJECT_DIR / digest
            if lstat_info(show_id, source) is None:
                reason = f"revision {revision.id} names this blob, which is missing"
                raise corrupt(show_id, source, reason)
            linked += target.transfer(show_id, source, folder / OBJECT_DIR / digest)
            blobs.add(digest)
        record, slot = record_bytes(revision, seq, tree), slot_bytes(revision)
        budget.spend(len(record) + len(slot))
        target.place(show_id, folder / RECORD_DIR / f"{revision.id}.json", record)
        target.place(show_id, folder / LOG_DIR / f"{seq:0{SLOT_DIGITS}d}", slot)
    manifest = manifest_json(show_id, ids[:legacy])
    target.place(show_id, folder / MANIFEST_FILE, dump_json(manifest))
    target.flush_show(folder)
    return ShowUpgrade(
        show=show_id,
        revisions=len(revisions),
        blobs_linked=linked,
        blobs_copied=len(blobs) - linked,
        skipped=left_out(listed, _used_files(revisions, blobs)),
        dropped=tuple(dropped),
    )


def _used_files(revisions: list[Revision], blobs: set[str]) -> dict[str, set[str]]:
    """Names the files of each store folder a show's history uses."""
    return {
        LINK_DIR: {ROOT_LINK, *(revision.id for revision in revisions[:-1])},
        TREE_DIR: {f"{revision.id}.json" for revision in revisions},
        OBJECT_DIR: blobs,
    }


def _require_unchanged(source: Path, listing: SourceListing, heads: dict[str, str]) -> None:
    """Refuses a source a writer changed while the upgrade read it: a show, a head or an asset.

    Run just before the layout file is placed, so a commit that landed on the source during the
    upgrade is never left behind in a target that looks complete.
    """
    again = list_source(source)
    reader = Layout1Reader(source)
    shows = [show.show_id for show in again.shows]
    if (
        shows != [show.show_id for show in listing.shows]
        or again.assets != listing.assets
        or any(reader.head(show_id) != heads[show_id] for show_id in shows)
    ):
        raise StorageError(
            f"layout-1 repository at {source} changed while it was upgraded",
            hint=WRITER_HINT,
            context={"root": str(source)},
            code=ErrorCode.CONFLICT,
        )


def upgrade_repository(source: Path, target: Path) -> UpgradeReport:
    """Copies every show of a layout-1 root, with its whole history, into a new layout-2 root.

    Every revision keeps its ID, parent, time and tree. Blobs and assets are hard-linked where
    the two roots share a file system and the current user owns them, and copied a piece at a
    time otherwise; each is hashed on the way. A linked file is one inode under two names:
    linking changes the change time and link count of the source file, and a later commit in
    the new root that reuses the blob sets its modification time, which the source then shows
    too; its bytes never change. The upgrade itself writes, renames or removes nothing under the
    source, and a symlink or special file in it refuses the upgrade before anything is written.
    The caps every read keeps apply. `layout.json` is written last, after everything else is
    flushed, so an interrupted upgrade leaves a target that readers refuse; delete it and run the
    upgrade again. The host swaps the folders afterwards, so rolling back is keeping the source.

    No writer may run on the source while it is upgraded. Just before `layout.json` is placed,
    the source is listed again and every show's head read again; a show, head or asset that
    changed raises StorageError CONFLICT and leaves no layout file.

    Args:
        source: Root of a repository written before v0.5.0.
        target: New folder for the layout-2 root; it must not exist yet, or be empty, and must
            not be inside the source under any spelling. `..` and `.` may follow only folders
            that exist, since the folders the upgrade creates must not lead anywhere else.

    Returns:
        report: For each show, the revisions copied, the blobs linked and copied, the files
            left out and the revision fields dropped; the assets linked and copied; and the root
            entries left out, each with a reason.

    >>> import json, tempfile
    >>> from datetime import UTC, datetime
    >>> from e8s.show.document_repository import revision_id
    >>> from e8s.show.file_repository import FileRepository
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> rev = revision_id("", at, {})
    >>> record = {"revision": {"id": rev, "parent": "", "at": at.isoformat()}, "tree": {}}
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     old, new = Path(folder) / "old", Path(folder) / "new"
    ...     for name in ("next", "trees"):
    ...         (old / "show_1" / name).mkdir(parents=True)
    ...     _ = (old / "show_1" / "next" / "root").write_text(rev)
    ...     _ = (old / "show_1" / "trees" / f"{rev}.json").write_text(json.dumps(record))
    ...     report = upgrade_repository(old, new)
    ...     report.shows[0].revisions, FileRepository(new).head("show_1") == rev
    (1, True)
    """
    _require_layout_1(source)
    folder = _free_target(source, target)
    listing = list_source(source)
    reader = Layout1Reader(source)
    writer = _Target(folder)
    upgraded = tuple(_upgrade_show(reader, writer, show) for show in listing.shows)
    linked = writer.carry_assets(source / ASSET_DIR, listing.assets)
    _require_unchanged(source, listing, writer.heads)
    writer.finish()
    return UpgradeReport(
        shows=upgraded,
        skipped=tuple(listing.left_out),
        assets_linked=linked,
        assets_copied=len(listing.assets) - linked,
    )
