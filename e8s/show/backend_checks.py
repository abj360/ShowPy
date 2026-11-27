"""
backend_checks.py --- the corruption error and the read checks the layout-2 file backend shares

Contains:
    CORRUPT_HINT: tells the caller how to repair a damaged show folder
    corrupt(): builds the StorageError for a stored file that fails a check
    require_caller_ids(): refuses a commit whose revision, parent, blob or tree IDs are malformed
"""

from collections.abc import Mapping
from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.core.json_types import JsonValue
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
