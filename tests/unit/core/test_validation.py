#!/usr/bin/env python3

"""
test_validation.py --- tests validation reports, validate_or_schema_error and require_finite

Contains:
    test_report_*(), test_merge_concatenates_issues: status, rendering and merging of reports
    test_validate_or_schema_error_*(): one behavior of the SchemaError-raising validator each
    test_require_finite_*(): one rule of the finite-number check each
    test_regression_require_finite_rejects_an_int_too_large_for_float: rejects 10**400 instead of
        leaking OverflowError
    test_regression_require_finite_echoes_a_huge_int_by_its_bit_count: says a 5,000-digit int is
        too large for a float and keeps the error context printable
"""

from typing import Literal

import numpy as np
import pytest
from pydantic import BaseModel

from showpy.core.errors import SchemaError
from showpy.core.validation import (
    ValidationIssue,
    ValidationReport,
    require_finite,
    validate_or_schema_error,
)


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


def test_require_finite_returns_float() -> None:
    assert require_finite(3, name="n") == 3.0
    assert isinstance(require_finite(3, name="n"), float)


def test_require_finite_names_the_parameter_in_context() -> None:
    with pytest.raises(SchemaError) as caught:
        require_finite(0.0, name="tol", low=0.0, low_open=True)
    assert caught.value.context == {"name": "tol", "value": "0.0", "low": 0.0, "high": None}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_require_finite_rejects_non_finite(bad: float) -> None:
    with pytest.raises(SchemaError):
        require_finite(bad, name="x")


@pytest.mark.parametrize("bad", [True, False])
def test_require_finite_rejects_bools(bad: bool) -> None:
    with pytest.raises(SchemaError):
        require_finite(bad, name="n")


def test_require_finite_rejects_non_numbers() -> None:
    with pytest.raises(SchemaError):
        require_finite("1.0", name="n")  # type: ignore[arg-type]


def test_require_finite_bounds() -> None:
    assert require_finite(0.0, name="n", low=0.0) == 0.0
    assert require_finite(1.0, name="n", high=1.0) == 1.0
    with pytest.raises(SchemaError):
        require_finite(-0.1, name="n", low=0.0)
    with pytest.raises(SchemaError):
        require_finite(1.1, name="n", high=1.0)


def test_regression_require_finite_rejects_an_int_too_large_for_float() -> None:
    with pytest.raises(SchemaError) as caught:
        require_finite(10**400, name="n")
    assert caught.value.context["name"] == "n"
    assert "finite" in caught.value.message


def test_require_finite_accepts_numpy_scalars_and_returns_python_float() -> None:
    for scalar in (np.int64(3), np.float32(0.5)):
        result = require_finite(scalar, name="n")  # type: ignore[arg-type]
        assert type(result) is float
        assert result == float(scalar)


def test_require_finite_rejects_numpy_bool() -> None:
    with pytest.raises(SchemaError):
        require_finite(np.bool_(True), name="n")  # type: ignore[arg-type]


def test_regression_require_finite_echoes_a_huge_int_by_its_bit_count() -> None:
    huge = 10**5000
    with pytest.raises(SchemaError) as caught:
        require_finite(huge, name="n")
    assert "too large for a float" in caught.value.message
    assert caught.value.context["value"] == f"<int of {huge.bit_length()} bits>"
    assert "bits" in repr(caught.value.context)
