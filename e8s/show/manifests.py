"""
manifests.py --- the show manifest of a layout-2 folder: its show ID and its legacy prefix

A native show's manifest holds only its ID. An upgraded show whose history starts with revisions
whose IDs do not recompute under the current scheme also holds the legacy prefix: the slot after
it (`legacy_before`), its length (`legacy_count`) and the SHA-256 of the canonical JSON list of
its revision IDs in order (`legacy_digest`). The three keys come together or not at all. A head
lookup checks only that they agree with each other and with the last slot; a read of the legacy
slots checks the digest, so a manifest edited to stretch the prefix over checked slots, or a
legacy record swapped for another, is refused.

Contains:
    LEGACY_KEYS: names the three manifest keys that describe the legacy prefix
    Manifest: holds a parsed manifest: the show ID and its legacy prefix
    legacy_digest(): returns the digest of a legacy prefix's revision IDs
    manifest_json(): builds a manifest for a show and the IDs of its legacy prefix
    parsed_manifest(): checks a parsed manifest and returns it
    require_legacy_bound(): raises CORRUPT when the legacy prefix passes the last slot
    require_legacy_digest(): raises CORRUPT unless the legacy slots hold the IDs the digest names
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from e8s.core.canonical import jcs
from e8s.core.json_types import JsonValue
from e8s.show.backend_checks import FIRST_SEQ, corrupt
from e8s.show.layout1 import REVISION_ID_PATTERN
from e8s.show.show_folders import show_folder

LEGACY_KEYS = frozenset({"legacy_before", "legacy_count", "legacy_digest"})


class Manifest(NamedTuple):
    """Holds a parsed manifest.

    Attributes:
        show_id: The show the folder holds.
        legacy_before: First slot whose revision ID must recompute; 1 for a native show.
        legacy_digest: Digest of the legacy prefix's revision IDs; empty for a native show.
    """

    show_id: str
    legacy_before: int = FIRST_SEQ
    legacy_digest: str = ""


def legacy_digest(revision_ids: Sequence[str]) -> str:
    """Returns the SHA-256 of the canonical JSON list of a legacy prefix's revision IDs.

    >>> len(legacy_digest(["a" * 64]))
    64
    """
    return hashlib.sha256(jcs(list(revision_ids))).hexdigest()


def manifest_json(show_id: str, legacy_ids: Sequence[str] = ()) -> dict[str, JsonValue]:
    """Builds the manifest of a show whose history starts with `legacy_ids`.

    Args:
        show_id: The show.
        legacy_ids: IDs of the first slots, in order, whose IDs do not recompute; none for a
            native show, whose manifest then holds only its ID.

    >>> manifest_json("show_1")
    {'id': 'show_1'}
    """
    if not legacy_ids:
        return {"id": show_id}
    return {
        "id": show_id,
        "legacy_before": len(legacy_ids) + FIRST_SEQ,
        "legacy_count": len(legacy_ids),
        "legacy_digest": legacy_digest(legacy_ids),
    }


def _count(show_id: str, path: Path, value: JsonValue, key: str) -> int:
    """Returns a positive integer key of the manifest, raising CORRUPT for anything else."""
    if not isinstance(value, int) or isinstance(value, bool) or value < FIRST_SEQ:
        raise corrupt(show_id, path, f"manifest {key} is not a positive integer")
    return value


def _legacy_prefix(show_id: str, path: Path, stored: dict[str, JsonValue]) -> tuple[int, str]:
    """Checks the three legacy keys against each other and returns legacy_before and the digest."""
    before = _count(show_id, path, stored["legacy_before"], "legacy_before")
    count = _count(show_id, path, stored["legacy_count"], "legacy_count")
    digest = stored["legacy_digest"]
    if not isinstance(digest, str) or not REVISION_ID_PATTERN.fullmatch(digest):
        raise corrupt(show_id, path, "manifest legacy_digest is not a 64-digit hex digest")
    if count != before - FIRST_SEQ:
        raise corrupt(show_id, path, "manifest legacy_count is not legacy_before - 1")
    return before, digest


def parsed_manifest(show_id: str, path: Path, stored: JsonValue) -> Manifest:
    """Checks a parsed manifest and returns it.

    Args:
        show_id: Show being read, named in errors; empty when it is not known yet.
        path: The manifest file, whose folder must be the one its show ID encodes.
        stored: Parsed JSON of the manifest.

    Returns:
        manifest: The listed show ID and its legacy prefix.
    """
    if not isinstance(stored, dict) or stored.keys() not in ({"id"}, {"id"} | LEGACY_KEYS):
        raise corrupt(
            show_id,
            path,
            "manifest must hold id, and legacy_before, legacy_count and "
            "legacy_digest together or none of them",
        )
    listed = stored["id"]
    if not isinstance(listed, str) or show_folder(listed) != path.parent.name:
        raise corrupt(show_id, path, "manifest names a show stored in another folder")
    if stored.keys() == {"id"}:
        return Manifest(listed)
    return Manifest(listed, *_legacy_prefix(show_id, path, stored))


def require_legacy_bound(show_id: str, path: Path, legacy_before: int, last: int) -> None:
    """Raises CORRUPT when a manifest's `legacy_before` passes the slot after the last.

    An upgrade ends the legacy prefix at a slot it copies and later slots only follow it, so a
    larger value names slots that do not exist yet, whose records would then be read without
    their IDs checked.

    Args:
        show_id: Show the manifest belongs to, named in the error.
        path: The manifest file.
        legacy_before: First slot whose revision ID must be recomputable from its record.
        last: Number of the show's last slot; 0 before the first.
    """
    if legacy_before > last + 1:
        reason = f"legacy_before {legacy_before} is past slot {last + 1}, the slot after the last"
        raise corrupt(show_id, path, reason)


def require_legacy_digest(
    show_id: str, path: Path, manifest: Manifest, revision_ids: Sequence[str]
) -> None:
    """Raises CORRUPT unless the legacy slots hold the revision IDs the manifest's digest names.

    Args:
        show_id: Show the manifest belongs to, named in the error.
        path: The manifest file.
        manifest: The parsed manifest.
        revision_ids: IDs the slots of the legacy prefix hold, in order.
    """
    if legacy_digest(revision_ids) != manifest.legacy_digest:
        reason = "legacy_digest does not match the revision IDs of the legacy slots"
        raise corrupt(show_id, path, reason)
