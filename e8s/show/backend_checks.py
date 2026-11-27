"""
backend_checks.py --- the corruption error and the read checks the layout-2 file backend shares

Contains:
    CORRUPT_HINT: tells the caller how to repair a damaged show folder
    corrupt(): builds the StorageError for a stored file that fails a check
    Record: holds a revision record's slot number, revision and tree
    ReadBudget: counts the bytes one log read takes and refuses to pass its limit
    refuse_redirect(): refuses a folder that is a symlink or a junction
    require_plain_folders(): refuses the first listed folder that is a symlink or a junction
    require_caller_ids(): refuses a commit whose revision, parent, blob or tree IDs are malformed
"""

from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.core.json_types import JsonValue
from e8s.show import durable_io
from e8s.show.history_models import Revision
from e8s.show.layout1 import REVISION_ID_PATTERN
from e8s.show.storage import ROOT_REVISION

CORRUPT_HINT = "restore the show folder from a backup or remove the damaged file"


def corrupt(show_id: str, path: Path, reason: str) -> StorageError:
    """Builds the CORRUPT StorageError for a stored file that fails a check.

    Args:
        show_id: Show the file belongs to; empty when it is not known yet, which leaves it out.
        path: The damaged file.
        reason: What check it failed.
    """
    context: dict[str, JsonValue] = {"path": str(path), "file": path.name}
    if show_id:
        context["show"] = show_id
    return StorageError(
        f"repository{' of ' + show_id if show_id else ''} is corrupt: {reason}",
        hint=CORRUPT_HINT,
        context=context,
        code=ErrorCode.CORRUPT,
    )


class Record(NamedTuple):
    """Holds a revision record: its slot number, its revision and its tree."""

    seq: int
    revision: Revision
    tree: dict[str, str]


class ReadBudget:
    """Counts the bytes one log() call has read and refuses to pass its limit."""

    def __init__(self, show_id: str, limit: int) -> None:
        self._show_id = show_id
        self._limit = limit
        self._remaining = limit

    def spend(self, size: int) -> None:
        """Takes `size` bytes from the budget, raising TOO_LARGE when it runs out."""
        self._remaining -= size
        if self._remaining < 0:
            raise StorageError(
                f"log of {self._show_id} holds more than {self._limit} bytes",
                hint="save the current state as a new show and continue there",
                context={"show": self._show_id, "cap": self._limit},
                code=ErrorCode.TOO_LARGE,
            )


def _caller_id(show_id: str, value: str, role: str) -> None:
    if not REVISION_ID_PATTERN.fullmatch(value):
        raise StorageError(
            f"{role} {value!r} is not a 64-digit hex id",
            hint="revision IDs and blob digests are 64 lowercase hex digits made by "
            "DocumentRepository.transaction",
            context={"show": show_id, role: value},
            code=ErrorCode.BAD_ID,
        )


def require_caller_ids(
    show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
) -> None:
    """Refuses a commit whose revision, parent, blob or tree IDs are not 64-digit hex."""
    _caller_id(show_id, revision.id, "revision id")
    if revision.parent != ROOT_REVISION:
        _caller_id(show_id, revision.parent, "parent id")
    for digest in [*blobs, *tree.values()]:
        _caller_id(show_id, digest, "blob digest")


def refuse_redirect(path: Path, mode: int, show_id: str = "") -> None:
    """Raises CORRUPT when `path`, with stat mode `mode`, is a symlink or a junction."""
    if durable_io.is_redirect(path, mode):
        raise corrupt(show_id, path, "folder is a symbolic link or junction")


def require_plain_folders(show_id: str, *folders: Path) -> None:
    """Raises CORRUPT for the first listed folder that is a symlink or a junction.

    Args:
        show_id: Show the folders belong to, named in the error.
        folders: Folders a read or write passes through; a missing one is fine.
    """
    for path in folders:
        mode = durable_io.lstat_mode(show_id, path)
        if mode is not None:
            refuse_redirect(path, mode, show_id)
