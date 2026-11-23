#!/usr/bin/env python3

"""
test_caps.py --- tests the output-size caps and the require_size check

Contains:
    test_regression_require_size_rejects_over_cap: raises SchemaError carrying what, requested
        and cap
    test_require_size_accepts_at_cap: lets a request equal to the cap through
    test_regression_require_size_rejects_a_negative_request: raises SchemaError for a size below 0
    test_require_size_honors_error_type: raises the error class the caller names
    test_caps_have_approved_values: pins every cap to its maintainer-approved value
"""

import pytest

from e8s.core.caps import (
    MAX_COORD_M,
    MAX_COUNTS,
    MAX_DEPTH,
    MAX_GRID_CELLS,
    MAX_IMAGE_PIXELS,
    MAX_POINTS,
    MAX_RANGE,
    MAX_TABLE,
    MAX_TEXT_CHARS,
    MAX_VIDEO_PIXELS,
    MIN_LAYOUT_HEIGHT_M,
    MIN_TEMPO,
    require_size,
)
from e8s.core.errors import SchemaError, SpacingError


def test_regression_require_size_rejects_over_cap() -> None:
    with pytest.raises(SchemaError) as caught:
        require_size(1_000_001, MAX_POINTS, what="points")
    assert caught.value.message == "points would need 1000001, over the cap of 1000000"
    assert caught.value.context == {"what": "points", "requested": 1_000_001, "cap": 1_000_000}
    assert caught.value.hint


def test_require_size_accepts_at_cap() -> None:
    require_size(MAX_POINTS, MAX_POINTS, what="points")
    require_size(0, MAX_POINTS, what="points")


def test_require_size_honors_error_type() -> None:
    with pytest.raises(SpacingError):
        require_size(MAX_RANGE + 1, MAX_RANGE, what="ids", error=SpacingError)


def test_caps_have_approved_values() -> None:
    assert MAX_POINTS == 1_000_000
    assert MAX_TABLE == 1_000_000
    assert MAX_GRID_CELLS == 4_000_000
    assert MAX_RANGE == 10_000
    assert MAX_DEPTH == 64
    assert MAX_TEXT_CHARS == 1_000_000
    assert MAX_IMAGE_PIXELS == 40_000_000
    assert MAX_VIDEO_PIXELS == 3840 * 2160
    assert MIN_LAYOUT_HEIGHT_M == 0.001
    assert MAX_COORD_M == 1e6
    assert MAX_COUNTS == 1_000_000
    assert MIN_TEMPO == 1e-3


def test_regression_require_size_rejects_a_negative_request() -> None:
    with pytest.raises(SchemaError) as caught:
        require_size(-1, MAX_POINTS, what="points")
    assert caught.value.context == {"what": "points", "requested": -1, "cap": MAX_POINTS}
