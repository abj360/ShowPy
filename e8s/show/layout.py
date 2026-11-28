"""
layout.py --- reads which on-disk layout a repository root holds

Contains:
    LAYOUT_FILE: names the file that records a layout-2 root's version
    LAYOUT_FORMAT: names the format string that file holds
    CURRENT_LAYOUT: numbers the newest layout this release reads
    LAYOUT_MAX_BYTES: caps the size of the layout file read
    LAYOUT_BYTES: holds the content of `layout.json` for the layout this release writes
    RESTORE_LAYOUT_HINT: tells the caller how to replace a damaged layout file
    TORN_LAYOUT_HINT: tells the caller how to clear a layout file a stopped first commit left torn
    HOUSEKEEPING_NAMES: lists the entries operating systems add to a folder, which a root skips
    TEMP_DIR: names the folder under a layout-2 root that holds every writer's temp files
    ASSET_DIR: names the folder under a root that the file asset store keeps assets in
    require_usable_path(): refuses a path holding a NUL or a character the file system encoding
        cannot store
    read_layout(): reports the layout version of a repository root
    read_layout_file(): reads and checks the layout file itself, refusing a missing one
    holds_layout_1(): tells whether a root holds layout-1 show folders and no layout file
"""

import os
import stat
from functools import partial
from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, VersionError
from e8s.core.strict_json import parse_strict_json
from e8s.show.document_dump import dump_json
from e8s.show.durable_reads import read_capped
from e8s.show.layout1 import LINK_DIR, ROOT_LINK
from e8s.show.torn_reads import FSCK_REPAIR_CALL, is_short_of, is_unfinished_json, read_settled

LAYOUT_FILE = "layout.json"
LAYOUT_FORMAT = "e8.repository"
CURRENT_LAYOUT = 2
LEGACY_LAYOUT = 1
# The file holds two keys, about 40 bytes; the cap only refuses a file that is not one.
LAYOUT_MAX_BYTES = 1024
LAYOUT_KEYS = frozenset({"format", "layout"})
LAYOUT_BYTES = dump_json({"format": LAYOUT_FORMAT, "layout": CURRENT_LAYOUT})
RESTORE_LAYOUT_HINT = (
    f"restore {LAYOUT_FILE} from a backup, or move the folder away to start a new one"
)
TORN_LAYOUT_HINT = (
    "a first commit that stopped while writing it can leave it so; while the root holds no show "
    f"folder, {FSCK_REPAIR_CALL} removes it once it is a day old; otherwise "
    f"{RESTORE_LAYOUT_HINT}"
)
TEMP_DIR = "tmp"
ASSET_DIR = "assets"
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


def require_usable_path(path: Path) -> None:
    """Refuses, with BAD_PATH, a path no file system takes: a NUL, or an unencodable character.

    The operating system would refuse a NUL with a bare ValueError, and an unpaired surrogate
    the file system encoding cannot store with a bare UnicodeEncodeError, on the first lookup.
    A POSIX name byte that is not UTF-8, which Python spells as an escaped surrogate, encodes
    back to that byte and is kept.

    >>> require_usable_path(Path("repo"))
    >>> require_usable_path(Path("re" + chr(0) + "po"))
    Traceback (most recent call last):
    e8s.core.errors.StorageError: BAD_PATH: path ... holds a NUL character (hint: ...)
    """
    spelled = str(path)
    if "\0" in spelled:
        raise _unusable_path(spelled, "a NUL character", "remove the NUL character")
    try:
        os.fsencode(spelled)
    except UnicodeEncodeError as error:
        reason = "a character the file system encoding cannot store"
        fix = f"remove the unpaired surrogate {spelled[error.start]!r}"
        raise _unusable_path(spelled, reason, fix) from error


def _unusable_path(spelled: str, reason: str, fix: str) -> StorageError:
    return StorageError(
        f"path {spelled!r} holds {reason}",
        hint=f"{fix} from the path; no file system accepts one",
        context={"path": repr(spelled)},
        code=ErrorCode.BAD_PATH,
    )


def _corrupt(root: Path, reason: str, hint: str = RESTORE_LAYOUT_HINT) -> StorageError:
    return StorageError(
        f"repository at {root} is corrupt: {reason}",
        hint=hint,
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


def _is_store_dir(entry: os.DirEntry[str]) -> bool:
    """Tells the folders a root holds before its first show: temp files and assets.

    A first commit creates `tmp/` just before it links the layout file, and the asset store,
    which shares the root, creates `tmp/` and `assets/` on its first put.
    """
    return entry.name in {TEMP_DIR, ASSET_DIR} and entry.is_dir(follow_symlinks=False)


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
        if all(_is_store_dir(entry) for entry in stored):
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
        if is_short_of(LAYOUT_BYTES)(raw):
            reason = f"{LAYOUT_FILE} is empty or cut short after every reread"
            raise _corrupt(root, reason, TORN_LAYOUT_HINT) from error
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
    and neither do the `tmp` folder a first commit creates just before that file and the `tmp`
    and `assets` folders the file asset store, which shares the root, creates. A symlinked root
    is followed. Any other content raises StorageError CORRUPT, a newer layout
    raises VersionError, a file-system failure raises StorageError IO, and a root path holding a
    NUL or a character the file system encoding cannot store raises StorageError BAD_PATH.

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
    require_usable_path(root)
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


def holds_layout_1(root: Path) -> bool:
    """Tells whether a root holds layout-1 show folders and no layout file.

    Unlike read_layout(), anything else a root holds, such as an asset store's folders before
    the first show, is no error here.

    Args:
        root: Folder that may hold a repository.

    Returns:
        is_layout_1: True for a root that release v0.4.0 or earlier wrote.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     holds_layout_1(Path(folder))
    False
    """
    try:
        if os.path.lexists(root / LAYOUT_FILE):
            return False
        with os.scandir(root) as entries:
            return any(
                entry.name not in HOUSEKEEPING_NAMES and _is_legacy_show_dir(entry)
                for entry in entries
            )
    except FileNotFoundError:
        return False
    except OSError as error:
        raise _io_error(root, error) from error
