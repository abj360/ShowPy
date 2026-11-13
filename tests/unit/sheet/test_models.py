#!/usr/bin/env python3

"""
test_models.py --- tests sheet data classes: landmarks, zones, grids, coordinate systems
"""

import pytest

from showpy.core.errors import GeometryError, ShowPyError, TimeRangeError
from showpy.core.types import Region
from showpy.examples import two_set_show
from showpy.sheet.models import CoordinateSystem, GuideLine, Landmark, Sheet, Zone, ZoneKind
from showpy.sheet.templates import blank_sheet
from showpy.show.models import Show

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


def _sheet_with(**changes: object) -> Sheet:
    return Sheet.model_validate({**blank_sheet(10, 6).model_dump(), **changes})


@pytest.mark.defect("UP-9.4-08")
@pytest.mark.xfail(strict=True, reason="UP-9.4-08")
def test_regression_sheet_rejects_negative_size() -> None:
    """Rejects a negative sheet size at construction (audit 9.4)."""
    with pytest.raises(ShowPyError):
        _sheet_with(size_m=(-10.0, 6.0))


@pytest.mark.defect("UP-9.4-09")
@pytest.mark.xfail(strict=True, reason="UP-9.4-09")
def test_regression_sheet_rejects_one_point_outline() -> None:
    """Rejects an outline with fewer than three points at construction (audit 9.4)."""
    with pytest.raises(ShowPyError):
        _sheet_with(outline=((0.0, 0.0),))


@pytest.mark.defect("UP-9.4-10")
@pytest.mark.xfail(strict=True, reason="UP-9.4-10")
def test_regression_sheet_rejects_malformed_ref() -> None:
    """Rejects the ref "nonsense" at construction (audit 9.4)."""
    with pytest.raises(ShowPyError):
        _sheet_with(ref="nonsense")


@pytest.mark.defect("UP-9.4-11")
@pytest.mark.xfail(strict=True, reason="UP-9.4-11")
def test_regression_sheet_rejects_duplicate_landmark_ids() -> None:
    """Rejects two landmarks sharing an ID at construction (audit 9.4)."""
    mark = Landmark(id="dup", name="first", kind="point", geometry=((1.0, 1.0),))
    other = Landmark(id="dup", name="second", kind="point", geometry=((2.0, 2.0),))
    with pytest.raises(ShowPyError):
        _sheet_with(landmarks=(mark, other))


@pytest.mark.defect("UP-9.4-12")
@pytest.mark.xfail(strict=True, reason="UP-9.4-12")
def test_regression_region_rejects_two_point_ring() -> None:
    """Rejects a two-point region ring, which has no area (audit 9.4)."""
    with pytest.raises(ShowPyError):
        Region(exterior=((0.0, 0.0), (1.0, 1.0)))


@pytest.mark.defect("UP-9.4-13")
@pytest.mark.xfail(strict=True, reason="UP-9.4-13")
def test_regression_region_rejects_bow_tie_ring() -> None:
    """Rejects a self-intersecting bow-tie ring (audit 9.4)."""
    with pytest.raises(ShowPyError):
        Region(exterior=((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)))


@pytest.mark.defect("UP-9.4-14")
@pytest.mark.xfail(strict=True, reason="UP-9.4-14")
def test_regression_guide_line_rejects_no_points() -> None:
    """Rejects a guide line with no points at construction (audit 9.4)."""
    with pytest.raises(ShowPyError):
        GuideLine(id="empty", kind="custom", points=())


@pytest.mark.defect("UP-9.4-44")
@pytest.mark.xfail(strict=True, reason="UP-9.4-44")
def test_regression_sheet_rejects_landmark_alias_colliding_with_name() -> None:
    """Rejects landmarks whose name and alias collide instead of resolving by order (audit 9.4)."""
    first = Landmark(id="a", name="spike", kind="point", geometry=((1.0, 1.0),))
    second = Landmark(
        id="b", name="other", kind="point", geometry=((2.0, 2.0),), aliases=("Spike",)
    )
    with pytest.raises(ShowPyError):
        _sheet_with(landmarks=(first, second))


@pytest.mark.defect("UP-9.4-45")
@pytest.mark.xfail(strict=True, reason="UP-9.4-45")
def test_regression_show_rejects_zone_id_shared_with_sheet() -> None:
    """Rejects a show zone reusing a sheet zone ID (audit 9.4)."""
    base = two_set_show()
    sheet_zone = base.sheet.zones[0]
    clash = Zone(id=sheet_zone.id, kind=ZoneKind.CUSTOM, polygon=SQUARE)
    with pytest.raises(ShowPyError):
        Show.model_validate({**base.model_dump(), "zones": (clash,)})
