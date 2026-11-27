#!/usr/bin/env python3

"""
archives.py --- builds show histories and archives for the archive export and import tests

Contains:
    SHOW_ID: names the show every archive test stores
    AT: sets the time of the first revision
    with_history(): saves a show and further revisions into a repository
    entry_map(): returns an archive's entries as a name-to-bytes mapping
    rebuilt(): writes a mapping of entries back into an archive, in ascending name order
    listing(): maps every path under a root to its bytes, or empty bytes for a folder
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from e8s.examples import two_set_show
from e8s.show.archive_zip import ArchiveWriter, read_entries
from e8s.show.document_repository import DocumentRepository
from e8s.show.history_models import Revision

SHOW_ID = "show_1"
AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
ANY_SIZE = 2**31


def with_history(
    repo: DocumentRepository, revisions: int, show_id: str = SHOW_ID
) -> list[Revision]:
    """Saves a show, then changes a render, deletes one and renames the show in turn.

    Args:
        repo: Repository to write into.
        revisions: Number of revisions to land, at least one.
        show_id: Show to write.

    Returns:
        landed: The revisions, oldest first.
    """
    show = two_set_show()
    landed = [repo.put_show(show_id, show, expected_revision="", at=AT)]
    for n in range(1, revisions):
        at = AT + timedelta(minutes=n)
        head = landed[-1].id
        if n % 3 == 0:
            show = show.replace(title=f"title {n}")
            landed.append(repo.put_show(show_id, show, expected_revision=head, at=at))
            continue
        changes: dict[str, bytes | None] = {f"/renders/{n}.txt": f"render {n}".encode()}
        if n % 3 == 2:
            changes[f"/renders/{n - 1}.txt"] = None
        landed.append(repo.transaction(show_id, changes, expected_revision=head, at=at))
    return landed


def entry_map(archive: bytes) -> dict[str, bytes]:
    """Returns the entries of a valid archive by name."""
    return {entry.name: bytes(entry.data) for entry in read_entries(archive, lambda _: ANY_SIZE)}


def rebuilt(entries: dict[str, bytes]) -> bytes:
    """Writes entries into an archive in ascending name order, whatever their names."""
    writer = ArchiveWriter(lambda _: ANY_SIZE)
    for name in sorted(entries):
        writer.add(name, entries[name])
    return writer.finish()


def listing(root: Path) -> dict[str, bytes]:
    """Maps every path under a root to its bytes, or to empty bytes for a folder."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else b""
        for path in sorted(root.rglob("*"))
    }
