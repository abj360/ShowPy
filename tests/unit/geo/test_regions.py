#!/usr/bin/env python3

"""
test_regions.py --- tests area construction and boolean operations
"""

import pytest

from stage.core.errors import GeometryError
from stage.core.util import approx_equal
from stage.geo.regions import buffer, difference, intersection, region, union
from stage.geo.shapes import circle, rect


def test_region_from_closed_curve() -> None:
    assert approx_equal(region(rect((0, 0), 4, 2)).area, 8.0)


def test_region_from_points() -> None:
    assert approx_equal(region([(0, 0), (2, 0), (2, 2), (0, 2)]).area, 4.0)


def test_region_rejects_self_intersection() -> None:
    with pytest.raises(GeometryError) as info:
        region([(0, 0), (2, 2), (2, 0), (0, 2)])
    assert "Self-intersection" in info.value.message


def test_region_rejects_too_few_points() -> None:
    with pytest.raises(GeometryError):
        region([(0, 0), (1, 1)])


def test_boolean_operations() -> None:
    a = region(rect((0, 0), 4, 4))
    b = region(rect((2, 0), 4, 4))
    assert approx_equal(union(a, b).area, 24.0)
    assert approx_equal(intersection(a, b).area, 8.0)
    assert approx_equal(difference(a, b).area, 8.0)
    assert buffer(region(circle((0, 0), 1.0)), 1.0).area > 12.0
