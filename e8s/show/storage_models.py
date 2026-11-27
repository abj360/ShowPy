"""
storage_models.py --- the reports that checking, cleaning, upgrading and testing a repository give

Contains:
    FsckFinding: holds one problem a repository check found
    FsckReport: holds every finding of a repository check and whether it passed
    GcReport: holds what a clean-up removed or would remove
    ShowUpgrade: holds what an upgrade moved of one show and what it left out
    UpgradeReport: holds what an upgrade of a repository moved, show by show
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
        repaired: Paths a repair removed before the check that gave the findings.
    """

    findings: tuple[FsckFinding, ...] = ()
    ok: bool
    repaired: tuple[str, ...] = ()


class GcReport(FrozenModel):
    """Holds what a clean-up removed, or would remove in a dry run.

    Attributes:
        removed: Paths removed, or that a dry run would remove, relative to the root.
        bytes_freed: Bytes the removed files held.
        dry_run: Whether nothing was removed.
        skipped: Pairs of a show folder left untouched and why: a check found damage in it, the
            layout file is torn, or a slot landed in it while the clean-up ran.
    """

    removed: tuple[str, ...] = ()
    bytes_freed: Count = 0
    dry_run: bool = False
    skipped: tuple[tuple[str, str], ...] = ()


class ShowUpgrade(FrozenModel):
    """Holds what an upgrade moved of one show and what it left out.

    Attributes:
        show: ID of the show.
        revisions: Revisions copied, each with its ID, parent, time and tree kept.
        blobs_linked: Blobs hard-linked from the source, which shares a file system.
        blobs_copied: Blobs copied, where a hard link was refused.
        skipped: Pairs of a path in the show folder, relative to the source root, and why the
            upgrade left it out: a temp file, or a file or folder no revision uses.
        dropped: Pairs of a revision ID and the stored fields its copy no longer holds, such as
            `revision.actor` and `revision.message`, which v0.1 and v0.2 stored and the revision
            ID still hashes; `record.<key>` names an extra key beside the revision and tree.
    """

    show: str
    revisions: Count = 0
    blobs_linked: Count = 0
    blobs_copied: Count = 0
    skipped: tuple[tuple[str, str], ...] = ()
    dropped: tuple[tuple[str, tuple[str, ...]], ...] = ()


class UpgradeReport(FrozenModel):
    """Holds what an upgrade of a repository to the current layout moved, show by show.

    Attributes:
        shows: What each show's upgrade moved, sorted by show ID.
        skipped: Pairs of an entry of the source root that holds no show and why the upgrade left
            it out.
    """

    shows: tuple[ShowUpgrade, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()


class ConformanceReport(FrozenModel):
    """Holds which backend conformance checks passed and which failed.

    Attributes:
        passed: Names of the checks that passed.
        failed: Pairs of check name and why it failed.
    """

    passed: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
