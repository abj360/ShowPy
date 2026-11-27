"""
fsck_scan.py --- checks a whole layout-2 repository root for fsck and gc

The root must hold layout 2. A layout-1 root raises VersionError, a folder that is no repository
raises the StorageError CORRUPT that reading its layout raises, and a missing root raises
StorageError IO, so a mistyped path is never reported clean. A symlinked root is followed; no
link under it is ever followed.

Contains:
    RepositoryScan: holds what checking a repository root found
    scan_repository(): checks the layout file, the root's entries, the temp files and every show
"""

import stat
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, UnknownRefError, VersionError
from e8s.show.assets import ASSET_DIR
from e8s.show.backend_checks import corrupt
from e8s.show.commit_writes import LAYOUT_BYTES, STALE_TEMP_AGE
from e8s.show.durable_io import TEMP_PREFIX, is_stale_temp, mapped_io
from e8s.show.durable_reads import listed_entries, lstat_info, read_capped
from e8s.show.file_backend import SHOWS_DIR
from e8s.show.fsck_show import (
    STALE_TEMP,
    TORN_FILE,
    UNKNOWN_FILE,
    Leftover,
    Repairable,
    ShowScan,
    finding,
    is_plain_folder,
    scan_show,
)
from e8s.show.layout import (
    CURRENT_LAYOUT,
    HOUSEKEEPING_NAMES,
    LAYOUT_FILE,
    LAYOUT_MAX_BYTES,
    TEMP_DIR,
    read_layout,
)
from e8s.show.show_folders import SHOW_FOLDER_PATTERN, show_folder
from e8s.show.storage_models import FsckFinding
from e8s.show.torn_reads import is_short_of

ROOT_FOLDERS = (TEMP_DIR, SHOWS_DIR)


class RepositoryScan(NamedTuple):
    """Holds what checking a repository root found.

    Attributes:
        findings: Problems found outside the show folders.
        shows: What checking each show folder found.
        temps: The temp files reported.
        repairable: A torn layout file a repair may remove, while no show folder exists.
    """

    findings: list[FsckFinding]
    shows: list[ShowScan]
    temps: list[Leftover]
    repairable: list[Repairable]


def _missing_root(root: Path) -> StorageError:
    return StorageError(
        f"no repository at {root}: the folder does not exist",
        hint="pass the folder that holds the repository's layout.json",
        context={"root": str(root)},
        code=ErrorCode.IO,
    )


def _layout_1(root: Path) -> VersionError:
    return VersionError(
        f"repository at {root} is layout 1, which fsck and gc do not check",
        hint="open it with FileRepository, which reads layout 1; fsck and gc check layout 2 only",
        context={"layout": 1, "supported": CURRENT_LAYOUT, "root": str(root)},
    )


def _is_empty(folder: Path) -> bool:
    """Tells whether a folder is missing or holds nothing but housekeeping entries."""
    if lstat_info("", folder) is None:
        return True
    return all(name in HOUSEKEEPING_NAMES for name, _ in listed_entries(folder))


def _is_torn_layout(root: Path) -> bool:
    """Tells whether the layout file is a shorter start of the one a first commit writes."""
    path = root / LAYOUT_FILE
    info = lstat_info("", path)
    if info is None or not stat.S_ISREG(info.st_mode):
        return False
    raw = read_capped(path, "layout file", LAYOUT_MAX_BYTES, partial(corrupt, ""))
    return is_short_of(LAYOUT_BYTES)(raw)


def _check_layout(root: Path) -> list[Repairable]:
    """Requires a layout-2 root, returning its layout file when it is torn and repairable."""
    try:
        is_folder = stat.S_ISDIR(root.stat().st_mode)
    except FileNotFoundError as error:
        raise _missing_root(root) from error
    try:
        layout = read_layout(root)
    except StorageError as error:
        if not (is_folder and error.code == ErrorCode.CORRUPT and _is_torn_layout(root)):
            raise
        is_safe = partial(_is_empty, root / SHOWS_DIR)
        path, is_torn = root / LAYOUT_FILE, is_short_of(LAYOUT_BYTES)
        return [Repairable(path, LAYOUT_MAX_BYTES, is_torn, is_safe)]
    if layout != CURRENT_LAYOUT:
        raise _layout_1(root)
    return []


class _RootCheck:
    """Checks the entries of a root, its temp files and its show folders."""

    def __init__(self, root: Path, at: datetime | None) -> None:
        self.root = root
        self.cutoff = None if at is None else at - STALE_TEMP_AGE
        self.findings: list[FsckFinding] = []
        self.shows: list[ShowScan] = []
        self.temps: list[Leftover] = []

    def report(self, code: str, path: Path, message: str) -> None:
        self.findings.append(finding(self.root, code, path, message))

    def check_root(self) -> None:
        """Checks each root entry, then the temp folder and the shows folder."""
        for name, info in listed_entries(self.root, follows_links=True):
            path = self.root / name
            if name in HOUSEKEEPING_NAMES or name in (LAYOUT_FILE, ASSET_DIR):
                continue
            if name not in ROOT_FOLDERS:
                self.report(UNKNOWN_FILE, path, "is not part of a layout-2 repository")
            elif not is_plain_folder(path, info):
                self.report(UNKNOWN_FILE, path, "is not a plain folder; fsck never follows a link")
            elif name == TEMP_DIR:
                self.check_temps(path)
            else:
                self.check_shows(path)

    def check_temps(self, folder: Path) -> None:
        """Reports temp files a crashed writer left and every other entry of the temp folder."""
        for name, info in listed_entries(folder):
            path = folder / name
            if name in HOUSEKEEPING_NAMES:
                continue
            if not (name.startswith(TEMP_PREFIX) and stat.S_ISREG(info.st_mode)):
                self.report(UNKNOWN_FILE, path, "is not a temp file")
            elif self.cutoff is None or is_stale_temp(name, info, self.cutoff):
                self.report(STALE_TEMP, path, "temp file a writer left behind")
                self.temps.append(Leftover(path, info))

    def check_shows(self, folder: Path) -> None:
        """Checks each show folder, reporting entries that are not plain show folders."""
        for name, info in listed_entries(folder):
            path = folder / name
            if name in HOUSEKEEPING_NAMES:
                continue
            if not SHOW_FOLDER_PATTERN.fullmatch(name):
                self.report(UNKNOWN_FILE, path, "is not a show folder name")
            elif not is_plain_folder(path, info):
                self.report(UNKNOWN_FILE, path, "is not a plain folder; fsck never follows a link")
            else:
                self.shows.append(scan_show(self.root, path))

    def check_one_show(self, show_id: str) -> None:
        """Checks the folder of one show, which must exist under a plain shows folder."""
        folder = self.root / SHOWS_DIR / show_folder(show_id)
        for path in (self.root / SHOWS_DIR, folder):
            info = lstat_info(show_id, path)
            if info is None:
                raise UnknownRefError(
                    f"repository at {self.root} holds no show {show_id!r}",
                    hint="check the show ID, or call fsck(root) to check every show",
                    context={"show": show_id, "root": str(self.root)},
                )
            if not is_plain_folder(path, info):
                self.report(UNKNOWN_FILE, path, "is not a plain folder; fsck never follows a link")
                return
        self.shows.append(scan_show(self.root, folder))


def scan_repository(root: Path, show_id: str | None, at: datetime | None) -> RepositoryScan:
    """Checks a layout-2 root: its layout file, its entries, its temp files and its shows.

    Args:
        root: Repository root folder; a symlinked root is followed.
        show_id: The one show to check, or None for the whole root.
        at: The caller's time; temp files modified within STALE_TEMP_AGE before it belong to
            running writers and are left out. None reports every temp file.

    Returns:
        scan: Findings outside the show folders, each show folder's scan, the temp files
            reported and a torn layout file a repair may remove.
    """
    with mapped_io(show_id or "", root):
        repairable = _check_layout(root)
        check = _RootCheck(root, at)
        if repairable:
            check.report(TORN_FILE, root / LAYOUT_FILE, "layout file is empty or cut short")
        if show_id is None:
            check.check_root()
        else:
            check.check_one_show(show_id)
        return RepositoryScan(check.findings, check.shows, check.temps, repairable)
