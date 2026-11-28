"""
blob_checks.py --- the layout-2 file backend's checks on stored blobs: verified reads, unsent blobs

Contains:
    verified_blob(): reads a stored blob, raising CORRUPT unless its bytes hash to its file name
    stored_blob(): returns the verified bytes of a blob a read names by its digest
    require_unsent_blobs(): requires every tree blob a commit does not send to be stored for the
        show, before anything is written
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from e8s.show import durable_io
from e8s.show.backend import REVISION_ID_PATTERN
from e8s.show.backend_checks import (
    Record,
    corrupt,
    missing_file,
    require_blob_digest,
    unstored_blobs,
)
from e8s.show.durable_reads import lstat_mode, read_capped
from e8s.show.layout1 import BLOB_MAX_BYTES
from e8s.show.storage import ROOT_REVISION


def verified_blob(show_id: str, path: Path) -> bytes:
    """Reads a stored blob, raising CORRUPT unless its bytes hash to its file name.

    Args:
        show_id: Show the blob belongs to, named in the error.
        path: The stored blob file, named by its digest.

    Returns:
        data: The blob's bytes.
    """
    data = read_capped(
        path, "blob", BLOB_MAX_BYTES, lambda source, why: corrupt(show_id, source, why)
    )
    require_blob_digest(show_id, path, data)
    return data


def stored_blob(show_id: str, objects: Path, digest: str) -> bytes:
    """Returns the verified bytes of a blob a read names by its digest.

    Args:
        show_id: Show the blob belongs to, named in the error.
        objects: The show's blob folder.
        digest: The blob's 64-digit hex digest.

    Returns:
        data: The blob's bytes, checked against the digest.
    """
    if not REVISION_ID_PATTERN.fullmatch(digest):
        raise corrupt(show_id, objects, f"{digest!r} is not a 64-digit hex id")
    if lstat_mode(show_id, objects / digest) is None:
        raise missing_file(show_id, objects / digest, "a tree names this blob")
    return verified_blob(show_id, objects / digest)


def require_unsent_blobs(
    show_id: str,
    objects: Path,
    parent: str,
    tree: Mapping[str, str],
    blobs: Mapping[str, bytes],
    read_record: Callable[[str], Record],
) -> None:
    """Requires every tree blob not sent to be stored for this show, before anything is written.

    A blob the parent's tree names is reached by a landed slot, so gc keeps it. Any other one
    is freshened, then read and checked against its digest: gc removes only a file that is old
    and unchanged since it listed it, so a freshened blob stays until this commit's slot lands,
    and one gc already moved aside fails the freshen and refuses the commit.

    Args:
        show_id: Show the commit lands in.
        objects: The show's blob folder.
        parent: The commit's parent revision; its record is read only when a blob is not sent.
        tree: Path to blob digest of the commit's tree.
        blobs: Digest to bytes of the blobs the commit sends.
        read_record: Reads the show's record of a landed revision.
    """
    unsent = {digest for digest in tree.values() if digest not in blobs}
    if not unsent:
        return
    if parent != ROOT_REVISION:
        unsent -= set(read_record(parent).tree.values())
    missing = sorted(digest for digest in unsent if not durable_io.freshen(objects / digest))
    if missing:
        raise unstored_blobs(show_id, missing)
    for digest in sorted(unsent):
        verified_blob(show_id, objects / digest)
