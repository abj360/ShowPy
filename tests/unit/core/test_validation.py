#!/usr/bin/env python3

"""
test_validation.py --- tests ValidationReport status and one-line rendering
"""

from typing import Literal

from stage.core.validation import ValidationIssue, ValidationReport


def _issue(level: Literal["error", "warning"]) -> ValidationIssue:
    return ValidationIssue(level=level, code="X", where="set 1", message="bad", hint="fix")


def test_report_ok_when_only_warnings() -> None:
    assert ValidationReport(issues=(_issue("warning"),)).ok


def test_report_not_ok_with_error() -> None:
    assert not ValidationReport(issues=(_issue("warning"), _issue("error"))).ok


def test_report_renders_one_line_per_issue() -> None:
    text = str(ValidationReport(issues=(_issue("error"),)))
    assert text == "E: X set 1: bad (hint: fix)"


def test_merge_concatenates_issues() -> None:
    merged = ValidationReport.merge(
        ValidationReport(issues=(_issue("error"),)), ValidationReport(issues=())
    )
    assert len(merged.issues) == 1
