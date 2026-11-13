#!/usr/bin/env python3

"""
test_models.py --- tests sheet data classes: landmarks, zones, grids, coordinate systems
"""

import pytest

from showpy.core.errors import GeometryError, TimeRangeError
from showpy.core.types import Region
from showpy.sheet.models import CoordinateSystem, Landmark, Zone, ZoneKind

SQUARE = Region(exterior=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))


def test_landmark_geometry_must_match_kind() -> None:
    assert Landmark(id="c", name="center", kind="point", geometry=((0.0, 0.0),)).point == (0, 0)
    with pytest.raises(GeometryError):
        Landmark(id="c", name="center", kind="point", geometry=((0, 0), (1, 1)))
    with pytest.raises(GeometryError):
        Landmark(id="l", name="line", kind="line", geometry=((0, 0),))
    with pytest.raises(GeometryError):
        Landmark(id="a", name="area", kind="area", geometry=((0, 0), (1, 1)))


def test_zone_active_windows_validated() -> None:
    zone = Zone(id="pit", kind=ZoneKind.PIT, polygon=SQUARE, active=((0.0, 16.0),))
    assert zone.is_active(8.0)
    assert not zone.is_active(20.0)
    assert Zone(id="z", kind=ZoneKind.CUSTOM, polygon=SQUARE).is_active(1e9)
    with pytest.raises(TimeRangeError):
        Zone(id="z", kind=ZoneKind.CUSTOM, polygon=SQUARE, active=((5.0, 5.0),))


def test_coordinate_system_defaults() -> None:
    system = CoordinateSystem()
    assert system.units == "m"
    assert system.origin == (0.0, 0.0)
