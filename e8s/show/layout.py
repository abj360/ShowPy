"""
layout.py --- reads which on-disk layout a repository root holds

Contains:
    LAYOUT_FILE: names the file that records a layout-2 root's version
    LAYOUT_FORMAT: names the format string that file holds
    CURRENT_LAYOUT: numbers the newest layout this release reads
    LAYOUT_MAX_BYTES: caps the size of the layout file read
    read_layout(): reports the layout version of a repository root
"""

from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, VersionError
from e8s.core.strict_json import parse_strict_json
from e8s.show.layout1 import LINK_DIR, OBJECT_DIR, TREE_DIR, _FileBackend

LAYOUT_FILE = "layout.json"
LAYOUT_FORMAT = "e8.repository"
CURRENT_LAYOUT = 2
LEGACY_LAYOUT = 1
# The file holds two keys, about 40 bytes; the cap only refuses a file that is not one.
LAYOUT_MAX_BYTES = 1024
LAYOUT_KEYS = frozenset({"format", "layout"})
LEGACY_SHOW_DIRS = (LINK_DIR, TREE_DIR, OBJECT_DIR)


def _corrupt(root: Path, reason: str) -> StorageError:
    return StorageError(
        f"repository at {root} is corrupt: {reason}",
        hint=f"restore {LAYOUT_FILE} from a backup, or move the folder away to start a new one",
        context={"root": str(root), "file": LAYOUT_FILE},
        code=ErrorCode.CORRUPT,
    )


def _is_legacy_show_dir(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    return any((path / name).is_dir() for name in LEGACY_SHOW_DIRS)


def _unversioned_layout(root: Path) -> int:
    """Classifies a root without a layout file as layout 1, or as empty, or as corrupt."""
    entries = list(root.iterdir())
    if not entries:
        return CURRENT_LAYOUT
    if any(_is_legacy_show_dir(entry) for entry in entries):
        return LEGACY_LAYOUT
    raise _corrupt(root, f"no {LAYOUT_FILE} and no layout-1 show folders")


def _declared_layout(root: Path, path: Path) -> int:
    raw = _FileBackend(root)._read(str(root), path, "layout file", LAYOUT_MAX_BYTES)
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

    A root with `layout.json` has the layout that file names. A root with show folders that hold
    `next/` links and no `layout.json` is layout 1. An empty or missing root reports the current
    layout, because storing the first show creates the file.

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
    path = root / LAYOUT_FILE
    try:
        path.lstat()
    except FileNotFoundError:
        return _unversioned_layout(root) if root.is_dir() else CURRENT_LAYOUT
    except NotADirectoryError as error:
        raise _corrupt(root, "the root is not a folder") from error
    return _declared_layout(root, path)
