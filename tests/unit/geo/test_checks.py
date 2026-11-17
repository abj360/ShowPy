#!/usr/bin/env python3

"""
test_checks.py --- tests the geometry parameter checks

Contains:
    test_require_finite_as_returns_a_valid_value: passes a value inside its bounds through
    test_require_finite_as_translates_the_error_class: raises the caller's class, cause chained
"""

import pytest

from showpy.core.errors import GeometryError, SchemaError, SpacingError
from showpy.geo.checks import require_finite_as


def test_require_finite_as_returns_a_valid_value() -> None:
    """Returns an in-bounds value as a float."""
    assert require_finite_as(GeometryError, 2, name="tol", low=0.0, low_open=True) == 2.0


@pytest.mark.parametrize("error", [GeometryError, SpacingError])
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_require_finite_as_translates_the_error_class(
    error: type[GeometryError] | type[SpacingError], bad: float
) -> None:
    """Raises the caller's error class with the original message, hint and context."""
    with pytest.raises(error) as caught:
        require_finite_as(error, bad, name="tol", low=0.0, low_open=True)
    cause = caught.value.__cause__
    assert isinstance(cause, SchemaError)
    assert caught.value.message == cause.message
    assert caught.value.hint == cause.hint
    assert caught.value.context == cause.context
    assert caught.value.context["name"] == "tol"
