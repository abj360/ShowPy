"""
upgrade_source.py --- lists a layout-1 root for an upgrade: its shows and what an upgrade leaves out

An upgrade never opens a symlink, a junction, a FIFO, a socket or a device. One found where the
listing looks (the root, each show folder and its next/, objects/ and trees/ folders) refuses the
upgrade before anything is written, naming it, since following it could copy files from outside
the repository and skipping it could drop history a host keeps behind a link. Regular files and
folders the history does not use are left out of the upgrade and reported with a reason.

Contains:
    STORE_FOLDERS: names the folders of a layout-1 show folder that hold its history
    SourceShow: holds what one layout-1 show folder lists
    SourceListing: holds what a layout-1 root lists: its shows, its assets and what is left out
    list_source(): lists a layout-1 root, refusing every link and special file
    left_out(): pairs each file of a show folder its history does not use with the reason
"""

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.assets import ASSET_DIR
from e8s.show.durable_io import mapped_io
from e8s.show.durable_reads import is_redirect, listed_entries, lstat_mode
from e8s.show.layout import HOUSEKEEPING_NAMES
from e8s.show.layout1 import (
    LINK_DIR,
    OBJECT_DIR,
    REVISION_ID_PATTERN,
    ROOT_LINK,
    TEMP_PREFIX,
    TREE_DIR,
)

STORE_FOLDERS = (LINK_DIR, OBJECT_DIR, TREE_DIR)
HOUSEKEEPING = "entry the operating system adds to a folder"
NOT_A_SHOW = "not a layout-1 show folder: it holds no next/root link"
NOT_IN_A_SHOW = "not part of a layout-1 show folder"
FOLDER_IN_STORE = "folder inside a store folder, which holds only files"
LEFT_BY_A_WRITER = "temp file a layout-1 writer left behind"
NOT_AN_ASSET = "not an asset: its name is no SHA-256 digest"
UNUSED_FILES = {
    LINK_DIR: "next/ link off the history chain",
    OBJECT_DIR: "blob no revision's tree names",
    TREE_DIR: "record of a revision off the history chain, such as a commit that lost its race",
}
LINK_HINT = (
    "move it out of the repository, or replace it with the file or folder it points to, then run "
    "upgrade_repository again; the upgrade never follows a link or opens a special file"
)

type LeftOut = list[tuple[str, str]]


class SourceShow(NamedTuple):
    """Holds what one layout-1 show folder lists.

    Attributes:
        show_id: The folder's name, which is the show's ID.
        files: Names of the regular files in each of next/, objects/ and trees/ that exist.
        left_out: Pairs of a path relative to the root and why the upgrade leaves it out, for the
            entries that belong to no store folder.
    """

    show_id: str
    files: Mapping[str, tuple[str, ...]]
    left_out: tuple[tuple[str, str], ...]


class SourceListing(NamedTuple):
    """Holds what a layout-1 root lists.

    Attributes:
        shows: Each show folder's listing, sorted by show ID.
        assets: Names of the files under `assets/` that an asset store wrote, sorted.
        left_out: Pairs of a root entry or asset-folder entry and why the upgrade leaves it out.
    """

    shows: list[SourceShow]
    assets: tuple[str, ...]
    left_out: LeftOut


def _refuse_special(root: Path, path: Path, mode: int) -> None:
    """Refuses a symlink, a junction or anything that is neither a folder nor a regular file."""
    if is_redirect(path, mode):
        kind = "a symbolic link or junction"
    elif stat.S_ISDIR(mode) or stat.S_ISREG(mode):
        return
    else:
        kind = "a special file (a FIFO, socket or device)"
    relative = path.relative_to(root).as_posix()
    raise StorageError(
        f"layout-1 repository at {root} holds {kind} at {relative}, which an upgrade never opens",
        hint=LINK_HINT,
        context={"root": str(root), "path": relative, "file": path.name},
        code=ErrorCode.CORRUPT,
    )


def _listed(root: Path, folder: Path) -> list[tuple[str, int]]:
    """Lists a folder's entries with their lstat modes, sorted, never following a link."""
    with mapped_io("", folder):
        entries = listed_entries(folder, follows_links=folder == root)
    return [(name, info.st_mode) for name, info in entries]


def _store_files(root: Path, folder: Path, left: LeftOut) -> tuple[str, ...]:
    """Lists the regular files of a store folder, leaving out what is not a file."""
    files: list[str] = []
    for name, mode in _listed(root, folder):
        relative = (folder / name).relative_to(root).as_posix()
        if name in HOUSEKEEPING_NAMES:
            left.append((relative, HOUSEKEEPING))
            continue
        _refuse_special(root, folder / name, mode)
        if stat.S_ISDIR(mode):
            left.append((relative, FOLDER_IN_STORE))
        else:
            files.append(name)
    return tuple(files)


def _source_show(root: Path, name: str) -> SourceShow:
    """Lists one show folder: the files of its store folders and every other entry left out."""
    folder = root / name
    files: dict[str, tuple[str, ...]] = {}
    left: LeftOut = []
    for entry, mode in _listed(root, folder):
        relative = f"{name}/{entry}"
        if entry in HOUSEKEEPING_NAMES:
            left.append((relative, HOUSEKEEPING))
            continue
        _refuse_special(root, folder / entry, mode)
        if entry in STORE_FOLDERS and stat.S_ISDIR(mode):
            files[entry] = _store_files(root, folder / entry, left)
        else:
            left.append((relative, NOT_IN_A_SHOW))
    return SourceShow(name, files, tuple(left))


def _is_show_folder(root: Path, name: str) -> bool:
    """Tells a layout-1 show folder, whose next/ folder holds a root link, from other folders."""
    links = root / name / LINK_DIR
    mode = lstat_mode("", links)
    if mode is not None:
        _refuse_special(root, links, mode)
    return mode is not None and stat.S_ISDIR(mode) and lstat_mode("", links / ROOT_LINK) is not None


def _source_assets(root: Path, left: LeftOut) -> tuple[str, ...]:
    """Lists the asset files under `assets/`, leaving out every file no asset store wrote."""
    assets: list[str] = []
    for name in _store_files(root, root / ASSET_DIR, left):
        if REVISION_ID_PATTERN.fullmatch(name):
            assets.append(name)
        else:
            reason = LEFT_BY_A_WRITER if name.startswith(TEMP_PREFIX) else NOT_AN_ASSET
            left.append((f"{ASSET_DIR}/{name}", reason))
    return tuple(assets)


def list_source(root: Path) -> SourceListing:
    """Lists a layout-1 root for an upgrade, refusing every link and special file it finds.

    Args:
        root: Folder of the layout-1 repository; a symlinked root is followed.

    Returns:
        listing: The show folders, the asset files of `assets/`, and every other entry with
            why it is left out.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     (Path(folder) / "show_1" / "next").mkdir(parents=True)
    ...     _ = (Path(folder) / "show_1" / "next" / "root").write_text("0" * 64)
    ...     _ = (Path(folder) / "notes.txt").write_text("mine")
    ...     listing = list_source(Path(folder))
    ...     [show.show_id for show in listing.shows], listing.left_out
    (['show_1'], [('notes.txt', 'not a layout-1 show folder: it holds no next/root link')])
    """
    shows: list[SourceShow] = []
    assets: tuple[str, ...] = ()
    left: LeftOut = []
    for name, mode in _listed(root, root):
        if name in HOUSEKEEPING_NAMES:
            left.append((name, HOUSEKEEPING))
            continue
        _refuse_special(root, root / name, mode)
        if stat.S_ISDIR(mode) and _is_show_folder(root, name):
            shows.append(_source_show(root, name))
        elif stat.S_ISDIR(mode) and name == ASSET_DIR:
            assets = _source_assets(root, left)
        else:
            left.append((name, NOT_A_SHOW))
    return SourceListing(shows, assets, sorted(left))


def left_out(show: SourceShow, used: Mapping[str, set[str]]) -> tuple[tuple[str, str], ...]:
    """Pairs each file of a show folder its history does not use with why it is left out.

    Args:
        show: The show folder's listing.
        used: Names of the files the history uses, for each store folder.

    Returns:
        left_out: Paths relative to the root with their reasons, sorted by path, including the
            entries the listing already left out.

    >>> show = SourceShow("s", {"next": ("root", ".tmp-1")}, ())
    >>> left_out(show, {"next": {"root"}})
    (('s/next/.tmp-1', 'temp file a layout-1 writer left behind'),)
    """
    left = list(show.left_out)
    for folder, names in show.files.items():
        for name in names:
            if name in used.get(folder, set()):
                continue
            reason = LEFT_BY_A_WRITER if name.startswith(TEMP_PREFIX) else UNUSED_FILES[folder]
            left.append((f"{show.show_id}/{folder}/{name}", reason))
    return tuple(sorted(left))
