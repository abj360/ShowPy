"""
fsck.py --- checks a layout-2 file repository and reports every problem it finds

Contains:
    fsck(): checks a repository root, or one show in it, reading everything and changing nothing
"""

from datetime import datetime
from pathlib import Path

from e8s.core.errors import SchemaError
from e8s.show.fsck_scan import RepositoryScan, scan_repository
from e8s.show.fsck_show import FAILING_CODES
from e8s.show.storage_models import FsckFinding, FsckReport


def require_zoned(at: datetime | None) -> None:
    """Refuses a time without a time zone, which each host would read as its own local time.

    Args:
        at: The caller's time, or None.
    """
    if at is not None and at.utcoffset() is None:
        raise SchemaError(
            f"time {at.isoformat()} has no time zone",
            hint="pass an aware time, such as datetime.now(UTC)",
            context={"at": at.isoformat()},
        )


def report_of(scan: RepositoryScan) -> FsckReport:
    """Builds the report of a scan: every finding, sorted by show, code and path."""
    found: list[FsckFinding] = [*scan.findings]
    for show in scan.shows:
        found.extend(show.findings)
    findings = tuple(sorted(found, key=lambda item: (item.show, item.code, item.path)))
    return FsckReport(findings=findings, ok=not any(f.code in FAILING_CODES for f in findings))


def fsck(root: Path, show_id: str | None = None, *, at: datetime | None = None) -> FsckReport:
    """Checks a layout-2 file repository, reading everything and changing nothing.

    It checks the layout file, each manifest, each slot (chain, gaps, torn slots), each record
    (ID, slot number, tree, and that each tree is the previous one with the revision's changes),
    and each blob a tree names (present, hashing to its name). It lists records and blobs no slot
    reaches, temp files, and every entry that is not part of the layout. Findings coded
    UNREACHABLE and STALE_TEMP are left for gc and do not fail the check; every other code does.
    No link under the root is followed; a symlinked root is.

    Args:
        root: Repository root folder.
        show_id: The one show to check; None checks the whole root.
        at: The caller's time, with a time zone; temp files modified within STALE_TEMP_AGE
            before it belong to running writers and are not reported. None reports them all.

    Returns:
        report: Every finding, sorted by show, code and path, and whether the root passes.

    Raises:
        VersionError: The root holds layout 1, or a newer layout than this release reads.
        StorageError: The root is missing (IO), holds no repository (CORRUPT), or cannot be
            read (IO).
        UnknownRefError: `show_id` names no show in the root.

    >>> import tempfile
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.document_repository import DocumentRepository
    >>> from e8s.show.file_backend import FileBackend
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     repo = DocumentRepository(FileBackend(Path(folder)))
    ...     _ = repo.put_show("show_1", two_set_show(), expected_revision="", at=at)
    ...     fsck(Path(folder)).ok
    True
    """
    require_zoned(at)
    return report_of(scan_repository(root, show_id, at))
