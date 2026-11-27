"""
storage_models.py --- the reports that checking, cleaning, upgrading and testing a repository give

Contains:
    FsckFinding: holds one problem a repository check found
    FsckReport: holds every finding of a repository check and whether it passed
    GcReport: holds what a clean-up removed or would remove
    UpgradeReport: holds what an upgrade of a repository moved
    ConformanceReport: holds which backend conformance checks passed and which failed
"""

from typing import Annotated

from pydantic import Field

from e8s.core.types import FrozenModel

type Count = Annotated[int, Field(ge=0)]


class FsckFinding(FrozenModel):
    """Holds one problem a repository check found.

    Attributes:
        code: Short name of the problem kind.
        path: Repository path the problem is at, relative to the root, with `/` separators.
        message: What is wrong.
        show: ID of the show the problem is in; empty outside a show, or where no readable
            manifest names the show.
    """

    code: str
    path: str
    message: str
    show: str = ""


class FsckReport(FrozenModel):
    """Holds every finding of a repository check and whether the repository passed.

    Attributes:
        findings: Problems found, sorted by show, code and path.
        ok: Whether the repository passes; the check decides, as some findings do not fail it.
    """

    findings: tuple[FsckFinding, ...] = ()
    ok: bool


class GcReport(FrozenModel):
    """Holds what a clean-up removed, or would remove in a dry run.

    Attributes:
        removed: Paths removed.
        bytes_freed: Bytes the removed files held.
        dry_run: Whether nothing was removed.
    """

    removed: tuple[str, ...] = ()
    bytes_freed: Count = 0
    dry_run: bool = False


class UpgradeReport(FrozenModel):
    """Holds what an upgrade of a repository to the current layout moved.

    Attributes:
        shows: IDs of the shows upgraded.
        revisions: Revisions rewritten.
        blobs_linked: Blobs linked into the new layout.
        blobs_copied: Blobs copied into the new layout.
    """

    shows: tuple[str, ...] = ()
    revisions: Count = 0
    blobs_linked: Count = 0
    blobs_copied: Count = 0


class ConformanceReport(FrozenModel):
    """Holds which backend conformance checks passed and which failed.

    Attributes:
        passed: Names of the checks that passed.
        failed: Pairs of check name and why it failed.
    """

    passed: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
