"""
legacy_checks.py --- the layout-2 file backend's reads of an upgraded show's legacy prefix

Contains:
    require_legacy_bound_at(): refuses a manifest whose legacy prefix passes the slot after the
        last, looking up the last slot only for an upgraded show
    require_legacy_prefix(): raises CORRUPT unless the legacy slots hold the IDs the manifest's
        digest names
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from e8s.show.backend_checks import FIRST_SEQ
from e8s.show.history_models import Revision
from e8s.show.manifests import Manifest, require_legacy_bound, require_legacy_digest


def require_legacy_bound_at(
    show_id: str, path: Path, legacy_before: int, last: int | None, head_seq: Callable[[], int]
) -> None:
    """Refuses a manifest whose `legacy_before` passes the slot after the last slot.

    Only an upgraded show's manifest names legacy slots, so no other show looks anything up.

    Args:
        show_id: Show the manifest belongs to, named in the error.
        path: The manifest file.
        legacy_before: First slot whose revision ID must be recomputable from its record.
        last: Number of the show's last slot when the caller already found it, else None.
        head_seq: Finds the number of the show's last slot when `last` is None.
    """
    if legacy_before == FIRST_SEQ:
        return
    require_legacy_bound(show_id, path, legacy_before, head_seq() if last is None else last)


def require_legacy_prefix(
    show_id: str,
    path: Path,
    manifest: Manifest,
    read_slot: Callable[[int], Revision],
    read: Sequence[Revision] | None,
) -> None:
    """Raises CORRUPT unless the legacy slots hold the revision IDs the manifest's digest names.

    Args:
        show_id: Show the manifest belongs to, named in the error.
        path: The manifest file.
        manifest: The parsed manifest of an upgraded show.
        read_slot: Reads the revision of one slot of the show.
        read: Revisions of the slots from the first on, when a log already read them.
    """
    legacy = range(FIRST_SEQ, manifest.legacy_before)
    ids = [r.id for r in read or [read_slot(seq) for seq in legacy]]
    require_legacy_digest(show_id, path, manifest, ids[: len(legacy)])
