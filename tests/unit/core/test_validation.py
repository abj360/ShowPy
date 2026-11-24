#!/usr/bin/env python3

"""
test_validation.py --- tests validation reports, SchemaError-raising validators, require_finite

Contains:
    test_report_*(), test_merge_concatenates_issues: status, rendering and merging of reports
    test_raise_for_errors_*(): raises a ShowValidationError only when the report holds an error
    test_issue_context_*(): the read-only, bounded context of one issue
    test_issue_hashes_with_and_without_context: issues are hashable, nested context included
    test_regression_issue_refuses_an_uncatalogued_code: an issue code must be an ErrorCode member
    test_validate_or_schema_error_*(): one behavior of the SchemaError-raising validator each
    test_validate_adapter_*(): SchemaError from standalone TypeAdapters, Python or JSON input
    test_require_finite_*(): one rule of the finite-number check each, bounds included
    test_regression_issue_context_is_bounded: caps the items of an issue's context
    test_require_finite_rejects_nan_bounds: raises SchemaError for a NaN low or high bound
    test_regression_require_finite_rejects_an_int_too_large_for_float: rejects 10**400 instead of
        leaking OverflowError
    test_regression_require_finite_echoes_a_huge_int_by_its_bit_count: says a 5,000-digit int is
        too large for a float and keeps the error context printable
"""

import json
from typing import Literal

import numpy as np
import pytest
from pydantic import BaseModel, TypeAdapter

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    MAX_CONTEXT_ITEMS,
    SchemaError,
    ShowValidationError,
    bounded_repr,
    field_errors,
)
from e8s.core.validation import (
    ValidationIssue,
    ValidationReport,
    require_finite,
    validate_adapter,
    validate_adapter_json,
    validate_or_schema_error,
)
from tests.helpers import context_entries

MANY_ISSUES = 30


def _issue(
    level: Literal["error", "warning"], code: ErrorCode = ErrorCode.SCHEMA
) -> ValidationIssue:
    return ValidationIssue(level=level, code=code, where="set 1", message="bad", hint="fix")


def test_report_ok_when_only_warnings() -> None:
    assert ValidationReport(issues=(_issue("warning"),)).ok


def test_report_not_ok_with_error() -> None:
    assert not ValidationReport(issues=(_issue("warning"), _issue("error"))).ok


def test_report_renders_one_line_per_issue() -> None:
    text = str(ValidationReport(issues=(_issue("error"),)))
    assert text == "E: SCHEMA set 1: bad (hint: fix)"


def test_report_splits_errors_and_warnings() -> None:
    error, warning = _issue("error", ErrorCode.LOCKED), _issue("warning", ErrorCode.TEMPO_ORDER)
    report = ValidationReport(issues=(warning, error))
    assert (report.errors, report.warnings) == ((error,), (warning,))


def test_report_codes_are_ordered_and_unique() -> None:
    issues = (
        _issue("error", ErrorCode.PATH),
        _issue("warning", ErrorCode.MATCH),
        _issue("error", ErrorCode.PATH),
    )
    assert ValidationReport(issues=issues).codes == ("PATH", "MATCH")


def test_report_to_json_round_trips() -> None:
    report = ValidationReport(issues=(_issue("error"), _issue("warning")))
    assert ValidationReport.from_json(report.to_json()) == report
    assert json.loads(json.dumps(report.to_json()))["issues"][0]["code"] == "SCHEMA"


def test_raise_for_errors_returns_when_only_warnings() -> None:
    ValidationReport(issues=(_issue("warning"),)).raise_for_errors()


def test_raise_for_errors_raises_with_the_issues_in_context() -> None:
    report = ValidationReport(
        issues=(_issue("warning", ErrorCode.TEMPO_ORDER), _issue("error", ErrorCode.LOCKED))
    )
    with pytest.raises(ShowValidationError) as info:
        report.raise_for_errors()
    assert "LOCKED" in info.value.message
    assert [issue["code"] for issue in context_entries(info.value, "issues")] == ["LOCKED"]


def test_raise_for_errors_caps_the_listed_issues() -> None:
    report = ValidationReport(
        issues=tuple(_issue("error", list(ErrorCode)[n]) for n in range(MANY_ISSUES))
    )
    with pytest.raises(ShowValidationError) as info:
        report.raise_for_errors()
    issues = info.value.context["issues"]
    assert isinstance(issues, list)
    assert issues[-1] == f"(+{MANY_ISSUES - MAX_CONTEXT_ITEMS} more)"


def test_issue_context_defaults_to_empty() -> None:
    assert _issue("error").context == {}


def test_issue_context_is_read_only() -> None:
    issue = ValidationIssue(
        level="error", code=ErrorCode.SCHEMA, where="w", message="m", hint="h", context={"n": 1}
    )
    with pytest.raises(TypeError):
        issue.context["n"] = 2  # type: ignore[index]


def test_regression_issue_context_is_bounded() -> None:
    issue = ValidationIssue(
        level="error",
        code=ErrorCode.SCHEMA,
        where="w",
        message="m",
        hint="h",
        context={"ids": [str(n) for n in range(MANY_ISSUES)], "name": "n" * 1_000},
    )
    assert issue.context["ids"] == (*map(str, range(MAX_CONTEXT_ITEMS)), "(+10 more)")
    assert issue.context["name"] == bounded_repr("n" * 1_000)


def test_issue_hashes_with_and_without_context() -> None:
    bare = _issue("error")
    nested = ValidationIssue(
        level="error",
        code=ErrorCode.SCHEMA,
        where="w",
        message="m",
        hint="h",
        context={"ids": ["a", "b"]},
    )
    assert hash(bare) == hash(_issue("error"))
    assert hash(nested) == hash(nested.replace())


def test_regression_issue_refuses_an_uncatalogued_code() -> None:
    fields = {"level": "error", "where": "w", "message": "m", "hint": "h"}
    with pytest.raises(SchemaError):
        ValidationIssue.from_json({**fields, "code": "NOT_A_CODE"})
    assert ValidationIssue.from_json({**fields, "code": "LOCKED"}).code is ErrorCode.LOCKED


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
    assert field_errors(info.value)[0]["loc"] == "size"


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
        result = require_finite(scalar, name="n")
        assert type(result) is float
        assert result == float(scalar)


def test_require_finite_rejects_numpy_bool() -> None:
    truth = True
    with pytest.raises(SchemaError):
        require_finite(np.bool_(truth), name="n")


def test_regression_require_finite_echoes_a_huge_int_by_its_bit_count() -> None:
    huge = 10**5000
    with pytest.raises(SchemaError) as caught:
        require_finite(huge, name="n")
    assert "too large for a float" in caught.value.message
    assert caught.value.context["value"] == f"<int of {huge.bit_length()} bits>"
    assert "bits" in repr(caught.value.context)


_INT_LIST: TypeAdapter[list[int]] = TypeAdapter(list[int])


def test_validate_adapter_returns_the_value() -> None:
    assert validate_adapter(_INT_LIST, ["1", 2], name="Ints") == [1, 2]


def test_validate_adapter_names_the_failing_item() -> None:
    with pytest.raises(SchemaError) as info:
        validate_adapter(_INT_LIST, [1, "x"], name="Ints")
    assert info.value.context["model"] == "Ints"
    assert field_errors(info.value)[0]["loc"] == "1"


@pytest.mark.parametrize("raw", [b"[1, 2", b'["x"]', b"\xff\xfe", b'{"a": 1}'])
def test_validate_adapter_json_raises_schema_error(raw: bytes) -> None:
    with pytest.raises(SchemaError) as info:
        validate_adapter_json(_INT_LIST, raw, name="Ints")
    assert field_errors(info.value)


def test_validate_adapter_json_returns_the_value() -> None:
    assert validate_adapter_json(_INT_LIST, b"[3]", name="Ints") == [3]


def test_require_finite_rejects_nan_bounds() -> None:
    with pytest.raises(SchemaError):
        require_finite(1.0, name="n", low=float("nan"))
    with pytest.raises(SchemaError):
        require_finite(1.0, name="n", high=float("nan"))
