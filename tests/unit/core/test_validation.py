#!/usr/bin/env python3

"""
test_validation.py --- tests ValidationReport status and one-line rendering

Contains:
    test_*(): one behavior of showpy.core.validation each
"""

from typing import Literal

import pytest
from pydantic import BaseModel

from showpy.core.errors import SchemaError
from showpy.core.validation import ValidationIssue, ValidationReport, validate_or_schema_error


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


class _Plain(BaseModel):
    size: int


def test_validate_or_schema_error_returns_the_model() -> None:
    assert validate_or_schema_error(_Plain, {"size": 3}).size == 3


def test_validate_or_schema_error_names_the_failing_field() -> None:
    with pytest.raises(SchemaError) as info:
        validate_or_schema_error(_Plain, {"size": "big"})
    assert info.value.context["model"] == "_Plain"
    assert info.value.context["errors"][0]["loc"] == "size"
