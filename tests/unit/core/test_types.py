#!/usr/bin/env python3

"""
test_types.py --- tests the JSON-safe Region and Box2 models
"""

import pytest
import shapely
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import ShowPyError
from showpy.core.types import Box2, Region


def test_region_round_trips_through_shapely() -> None:
    poly = shapely.Polygon([(0, 0), (4, 0), (4, 4), (0, 4)], [[(1, 1), (2, 1), (2, 2)]])
    region = Region.from_shapely(poly)
    assert region.to_shapely().equals(poly)


def test_region_round_trips_through_json() -> None:
    region = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)))
    assert Region.model_validate_json(region.model_dump_json()) == region


def test_box2_size_and_center() -> None:
    box = Box2(min=(0.0, 0.0), max=(4.0, 2.0))
    assert box.size == (4.0, 2.0)
    assert box.center == (2.0, 1.0)


@pytest.mark.defect("UP-9.3-36")
@pytest.mark.xfail(strict=True, reason="UP-9.3-36")
def test_regression_region_rejects_fewer_than_three_points() -> None:
    """Rejects a two-point region, which has no area (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (1.0, 1.0)))


@pytest.mark.defect("UP-9.3-37")
@pytest.mark.xfail(strict=True, reason="UP-9.3-37")
def test_regression_region_rejects_nan_points() -> None:
    """Rejects NaN coordinates (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (1.0, float("nan")), (1.0, 1.0)))


@pytest.mark.defect("UP-9.3-38")
@pytest.mark.xfail(strict=True, reason="UP-9.3-38")
def test_regression_region_rejects_self_intersection() -> None:
    """Rejects a bow-tie ring that crosses itself (audit 9.3)."""
    with pytest.raises((ShowPyError, PydanticValidationError)):
        Region(exterior=((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)))
