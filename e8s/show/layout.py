"""
layout.py --- reads which on-disk layout a repository root holds

Contains:
    LAYOUT_FILE: names the file that records a layout-2 root's version
    LAYOUT_FORMAT: names the format string that file holds
    CURRENT_LAYOUT: numbers the newest layout this release reads
    LAYOUT_MAX_BYTES: caps the size of the layout file read
    HOUSEKEEPING_NAMES: lists the entries operating systems add to a folder, which a root skips
    TEMP_DIR: names the folder under a layout-2 root that holds every writer's temp files
    read_layout(): reports the layout version of a repository root
    read_layout_file(): reads and checks the layout file itself, refusing a missing one
"""

import os
import stat
from functools import partial
from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, VersionError
from e8s.core.strict_json import parse_strict_json
from e8s.show.durable_reads import read_capped
from e8s.show.layout1 import LINK_DIR, ROOT_LINK
from e8s.show.torn_reads import is_unfinished_json, read_settled

LAYOUT_FILE = "layout.json"
LAYOUT_FORMAT = "e8.repository"
CURRENT_LAYOUT = 2
LEGACY_LAYOUT = 1
# The file holds two keys, about 40 bytes; the cap only refuses a file that is not one.
LAYOUT_MAX_BYTES = 1024
LAYOUT_KEYS = frozenset({"format", "layout"})
TEMP_DIR = "tmp"
# Operating systems add these to a folder on their own: macOS Finder, Spotlight, the trash and the
# file-system event log; fsck on ext file systems; Windows Explorer, the recycle bin and system
# restore. They are skipped wherever a repository lists its entries; anything else fails closed.
HOUSEKEEPING_NAMES = frozenset(
    {
        ".DS_Store",
        "lost+found",
        "Thumbs.db",
        "desktop.ini",
        ".Spotlight-V100",
        ".Trashes",
        ".fseventsd",
        "$RECYCLE.BIN",
        "System Volume Information",
    }
)


def _corrupt(root: Path, reason: str) -> StorageError:
    return StorageError(
        f"repository at {root} is corrupt: {reason}",
        hint=f"restore {LAYOUT_FILE} from a backup, or move the folder away to start a new one",
        context={"root": str(root), "file": LAYOUT_FILE},
        code=ErrorCode.CORRUPT,
    )


def _io_error(root: Path, error: OSError) -> StorageError:
    return StorageError(
        f"file-system error reading the repository layout at {root}: {error.strerror or error}",
        hint="check that the repository root exists, is readable and is a directory",
        context={"root": str(root), "errno": error.errno},
        code=ErrorCode.IO,
    )


def _is_legacy_show_dir(entry: os.DirEntry[str]) -> bool:
    """Tells a layout-1 show folder, which holds the next/root link, from any other entry."""
    return (
        entry.is_dir(follow_symlinks=False) and (Path(entry.path) / LINK_DIR / ROOT_LINK).exists()
    )


def _is_temp_dir(entry: os.DirEntry[str]) -> bool:
    """Tells the temp folder a first commit creates just before it links the layout file."""
    return entry.name == TEMP_DIR and entry.is_dir(follow_symlinks=False)


def _unversioned_layout(root: Path) -> int:
    """Classifies a root without a layout file as layout 1, as empty, or as corrupt.

    A first commit links the layout file after it creates `tmp/`, so a layout file that appears
    between the lookup of its name and this listing is read rather than refused.

    Housekeeping entries are skipped before anything is looked up under them, so an unreadable
    `lost+found` never fails the read.
    """
    try:
        is_folder = stat.S_ISDIR(root.stat().st_mode)
    except FileNotFoundError:
        return CURRENT_LAYOUT
    if not is_folder:
        raise _corrupt(root, "the root is not a folder")
    with os.scandir(root) as entries:
        stored = [entry for entry in entries if entry.name not in HOUSEKEEPING_NAMES]
        if not stored:
            return CURRENT_LAYOUT
        if any(entry.name == LAYOUT_FILE for entry in stored):
            return _declared_layout(root, root / LAYOUT_FILE)
        if any(_is_legacy_show_dir(entry) for entry in stored):
            return LEGACY_LAYOUT
        if all(_is_temp_dir(entry) for entry in stored):
            return CURRENT_LAYOUT
    raise _corrupt(root, f"no {LAYOUT_FILE} and no layout-1 show folders")


def _declared_layout(root: Path, path: Path) -> int:
    read = partial(
        read_capped,
        path,
        "layout file",
        LAYOUT_MAX_BYTES,
        lambda _, reason: _corrupt(root, reason),
        allows_growth=True,
    )
    raw = read_settled(read, is_unfinished_json)
    try:
        document = parse_strict_json(raw, document="layout file")
    except SchemaError as error:
        raise _corrupt(root, f"{LAYOUT_FILE} is not valid JSON") from error
    if not isinstance(document, dict) or document.keys() != LAYOUT_KEYS:
        raise _corrupt(root, f"{LAYOUT_FILE} must hold exactly {sorted(LAYOUT_KEYS)}")
    layout = document["layout"]
    if document["format"] != LAYOUT_FORMAT:
        raise _corrupt(root, f"{LAYOUT_FILE} format is not {LAYOUT_FORMAT!r}")
    if not isinstance(layout, int) or isinstance(layout, bool) or layout < CURRENT_LAYOUT:
        raise _corrupt(root, f"{LAYOUT_FILE} layout must be an integer of at least 2")
    if layout > CURRENT_LAYOUT:
        raise VersionError(
            f"repository layout {layout} is newer than layout {CURRENT_LAYOUT}, the newest this"
            " release reads",
            hint="upgrade e8 to a release that reads this layout",
            context={"layout": layout, "supported": CURRENT_LAYOUT, "root": str(root)},
        )
    return layout


def read_layout(root: Path) -> int:
    """Reports the layout version of a repository root.

    A root with `layout.json` has the layout that file names, even when it also holds layout-1
    show folders. A root with show folders that hold a `next/root` link and no `layout.json` is
    layout 1. An empty or missing root reports the current layout, because storing the first
    show creates the file; entries the operating system adds, such as `.DS_Store`, do not count,
    and neither does the `tmp` folder a first commit creates just before that file. A symlinked
    root is followed. Any other content raises StorageError CORRUPT, a newer layout
    raises VersionError, and a file-system failure raises StorageError IO.

    Args:
        root: Folder that holds the repository.

    Returns:
        layout: 1 for a layout-1 root, otherwise the layout number of the root.

    >>> import tempfile
    >>> from pathlib import Path
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     read_layout(Path(folder) / "new-repo")
    2
    """
    try:
        return _stored_layout(root)
    except OSError as error:
        raise _io_error(root, error) from error


def read_layout_file(root: Path) -> int:
    """Reads and checks a root's layout file itself; a missing file is CORRUPT, never layout 2.

    Args:
        root: Folder that holds the repository.

    Returns:
        layout: The layout number the file names.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     _ = (Path(folder) / LAYOUT_FILE).write_text('{"format": "e8.repository", "layout": 2}')
    ...     read_layout_file(Path(folder))
    2
    """
    try:
        return _declared_layout(root, root / LAYOUT_FILE)
    except OSError as error:
        raise _io_error(root, error) from error


def _stored_layout(root: Path) -> int:
    path = root / LAYOUT_FILE
    try:
        path.lstat()
    except FileNotFoundError:
        return _unversioned_layout(root)
    except NotADirectoryError as error:
        raise _corrupt(root, "the root is not a folder") from error
    return _declared_layout(root, path)
