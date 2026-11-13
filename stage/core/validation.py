#!/usr/bin/env python3

"""
validation.py --- ValidationIssue and ValidationReport shared by every structural validator

Contains:
    ValidationIssue: describes one error or warning with location and hint
    ValidationReport: collects issues, reports ok, renders one line per issue
    ValidationReport.merge(): combines several reports into one
"""

from typing import Literal

from stage.core.types import FrozenModel

LEVEL_PREFIX = {"error": "E", "warning": "W"}


class ValidationIssue(FrozenModel):
    """Describes one structural problem.

    Attributes:
        level: Whether the issue blocks (`error`) or only informs (`warning`).
        code: Stable issue code.
        where: Location, e.g. `set 12` or `12→13 T4`.
        message: What is wrong.
        hint: What would make it valid.
    """

    level: Literal["error", "warning"]
    code: str
    where: str
    message: str
    hint: str

    def __str__(self) -> str:
        prefix = LEVEL_PREFIX[self.level]
        return f"{prefix}: {self.code} {self.where}: {self.message} (hint: {self.hint})"


class ValidationReport(FrozenModel):
    """Collects validation issues.

    Attributes:
        issues: Every issue found, in discovery order.
    """

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """Returns True when no issue is an error."""
        return all(issue.level != "error" for issue in self.issues)

    @classmethod
    def merge(cls, *reports: "ValidationReport") -> "ValidationReport":
        """Combines reports, keeping issue order."""
        return cls(issues=tuple(issue for report in reports for issue in report.issues))

    def __str__(self) -> str:
        return "\n".join(str(issue) for issue in self.issues)
