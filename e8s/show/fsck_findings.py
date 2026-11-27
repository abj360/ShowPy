"""
fsck_findings.py --- the finding codes, records and helpers fsck and gc share

Contains:
    MISSING_BLOB: codes a blob a tree names that is not stored
    BAD_BLOB: codes a stored blob whose bytes do not hash to its name, or that is not a file
    BAD_RECORD: codes a manifest, slot or revision record that fails its checks
    SLOT_GAP: codes a run of missing slots before a later slot
    TORN_SLOT: codes a slot that is empty or cut short
    UNREACHABLE: codes a record or blob that no slot reaches
    STALE_TEMP: codes a temp file a crashed writer left behind
    UNKNOWN_FILE: codes an entry that is not part of a layout-2 repository, or a link
    TORN_FILE: codes a manifest or layout file that a crashed first write left empty or cut short
    FAILING_CODES: lists the codes that fail a check; the others are left for a clean-up
    Leftover: holds a file a clean-up may remove, with its lstat result when it was listed
    Repairable: holds a torn file a repair may remove and the checks it must still pass
    ShowScan: holds what checking one show folder found
    finding(): builds a finding whose path is relative to the repository root
    relative_path(): names a path under the root relative to it, with `/` separators
    is_plain_folder(): tells whether an lstat result is a folder and not a link or junction
    is_torn_manifest(): tells whether manifest bytes are a shorter start of a show's manifest
"""

import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from e8s.show.durable_reads import is_redirect
from e8s.show.show_folders import show_folder
from e8s.show.storage_models import FsckFinding

MISSING_BLOB = "MISSING_BLOB"
BAD_BLOB = "BAD_BLOB"
BAD_RECORD = "BAD_RECORD"
SLOT_GAP = "SLOT_GAP"
TORN_SLOT = "TORN_SLOT"
UNREACHABLE = "UNREACHABLE"
STALE_TEMP = "STALE_TEMP"
UNKNOWN_FILE = "UNKNOWN_FILE"
TORN_FILE = "TORN_FILE"
FAILING_CODES = frozenset(
    {MISSING_BLOB, BAD_BLOB, BAD_RECORD, SLOT_GAP, TORN_SLOT, UNKNOWN_FILE, TORN_FILE}
)
# A manifest is written as dump_json({"id": show_id}); a show ID is written verbatim between its
# quotes only when it holds none of the characters JSON escapes, which stored show IDs never do.
MANIFEST_HEAD = b'{\n  "id": "'
MANIFEST_TAIL = b'"\n}\n'
VERBATIM_ID = re.compile(rb"^[A-Za-z0-9_.\-]*$")


def finding(root: Path, code: str, path: Path, message: str, show: str = "") -> FsckFinding:
    """Builds a finding whose path is relative to the repository root, with `/` separators.

    Args:
        root: Repository root folder.
        code: Finding code.
        path: File or folder the problem is at, under `root`.
        message: What is wrong.
        show: ID of the show the problem is in; empty when no manifest names it.
    """
    return FsckFinding(code=code, path=relative_path(root, path), message=message, show=show)


def relative_path(root: Path, path: Path) -> str:
    """Names a path under the root relative to it, with `/` separators."""
    return path.relative_to(root).as_posix()


def is_plain_folder(path: Path, info: os.stat_result) -> bool:
    """Tells whether an lstat result is a folder, and not a symlink or a junction."""
    return stat.S_ISDIR(info.st_mode) and not is_redirect(path, info.st_mode)


def is_torn_manifest(data: bytes, folder_name: str) -> bool:
    """Tells whether bytes are a shorter start of the manifest a show in this folder would hold.

    Where the show ID is complete it must be the one the folder name encodes; where it is cut
    short, its readable part must agree with the start of the folder name.

    Args:
        data: Bytes read from the manifest.
        folder_name: Name of the show folder that holds it.

    Returns:
        is_torn: True only when some manifest of this folder starts with `data` and is longer.
    """
    if len(data) <= len(MANIFEST_HEAD):
        return MANIFEST_HEAD.startswith(data)
    if not data.startswith(MANIFEST_HEAD):
        return False
    body = data[len(MANIFEST_HEAD) :]
    closing = body.find(b'"')
    if closing < 0:
        slug = (
            show_folder(body.decode("ascii")).rpartition("-")[0]
            if VERBATIM_ID.match(body)
            else None
        )
        return slug is not None and folder_name.startswith(slug)
    show_id, tail = body[:closing], body[closing:]
    return (
        bool(VERBATIM_ID.match(show_id))
        and len(tail) < len(MANIFEST_TAIL)
        and MANIFEST_TAIL.startswith(tail)
        and show_folder(show_id.decode("ascii")) == folder_name
    )


class Leftover(NamedTuple):
    """Holds a file a clean-up may remove: its path and its lstat result when it was listed."""

    path: Path
    info: os.stat_result


class Repairable(NamedTuple):
    """Holds a torn file a repair may remove, and the checks it must still pass when removed.

    Attributes:
        path: The torn file.
        cap: Largest size the file may have, in bytes.
        is_torn: Tells whether bytes read from it are still torn.
        is_safe: Tells whether nothing has come to rely on the file since the check.
    """

    path: Path
    cap: int
    is_torn: Callable[[bytes], bool]
    is_safe: Callable[[], bool]


class ShowScan(NamedTuple):
    """Holds what checking one show folder found.

    Attributes:
        folder: The show folder.
        show_id: ID its manifest names; empty when the manifest is missing or damaged.
        findings: Problems found; records and blobs are reported unreachable only when every
            slot and record was read.
        unreachable: The records and blobs reported unreachable.
        repairable: Torn files a repair may remove: a torn last slot after an intact slot, and a
            torn manifest of a show with no slot.
        last_seq: Number of the last slot, 0 before the first.
    """

    folder: Path
    show_id: str
    findings: list[FsckFinding]
    unreachable: list[Leftover]
    repairable: list[Repairable]
    last_seq: int

    @property
    def is_failing(self) -> bool:
        """Tells whether any finding fails the check, so no file of the show may be removed."""
        return any(item.code in FAILING_CODES for item in self.findings)
