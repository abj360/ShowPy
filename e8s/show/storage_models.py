"""
storage_models.py --- the reports that checking, cleaning, upgrading and testing a repository give

Contains:
    FsckFinding: holds one problem a repository check found
    FsckReport: holds every finding of a repository check and whether it passed
    GcReport: holds what a clean-up removed or would remove
"""

from typing import Annotated

from pydantic import Field

from e8s.core.types import FrozenModel

type Count = Annotated[int, Field(ge=0)]


class FsckFinding(FrozenModel):
    """Holds one problem a repository check found.

    Attributes:
        code: Short name of the problem kind.
        path: Repository path the problem is at.
        message: What is wrong.
    """

    code: str
    path: str
    message: str


class FsckReport(FrozenModel):
    """Holds every finding of a repository check and whether the repository passed.

    Attributes:
        findings: Problems found, in the order the check met them.
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
