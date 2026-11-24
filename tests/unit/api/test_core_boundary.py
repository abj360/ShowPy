#!/usr/bin/env python3

"""
test_core_boundary.py --- tests the boundary of the compiled core: errors and NaN inputs

Contains:
    test_core_error_raises_the_e8s_class_with_its_code: a core error reaches Python as its
        E8sError subclass with the same code, message, context and hint
    test_regression_core_rejects_nan_with_schema_error: NaN at the boundary raises SchemaError,
        never a panic or a silent pass
"""

import math

import pytest

from e8s._core import core
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, SchemaError

FINITE_HINT = "pass a finite number"


def test_core_error_raises_the_e8s_class_with_its_code() -> None:
    with pytest.raises(SchemaError) as raised:
        core._finite_probe(-math.inf)
    error = raised.value
    assert type(error) is SchemaError
    assert error.code is ErrorCode.SCHEMA
    assert error.message == "value must be a finite number, got -inf"
    assert error.hint == FINITE_HINT
    assert error.context == {"field": "value", "value": "-inf"}


def test_regression_core_rejects_nan_with_schema_error() -> None:
    with pytest.raises(SchemaError) as raised:
        core._finite_probe(math.nan)
    assert isinstance(raised.value, E8sError)
    assert raised.value.code is ErrorCode.SCHEMA
    assert raised.value.context["value"] == "nan"
    assert core._finite_probe(0.25) == 0.25
