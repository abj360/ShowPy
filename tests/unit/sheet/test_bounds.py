#!/usr/bin/env python3

"""
test_bounds.py --- tests surface and walkable tests, landmark lookup, nearest landmarks, zones

Contains:
    Existing behavior tests, unlisted
    test_regression_landmark_anchor_ignores_repeated_closing_vertex()
    test_regression_landmark_anchor_is_the_area_centroid()
    test_regression_nearest_landmarks_rejects_fractional_k()
    test_regression_nearest_landmarks_rejects_nan_point()
    test_regression_nearest_landmarks_rejects_unknown_kind()
"""

import pytest

from showpy.core.errors import SchemaError, ShowPyError, UnknownRefError
from showpy.core.types import Region
from showpy.core.util import approx_equal
from showpy.sheet.bounds import (
    landmark,
    landmark_anchor,
    nearest_landmarks,
    on_surface,
    walkable,
    zones_at,
)
from showpy.sheet.models import Landmark, Zone, ZoneKind
from showpy.sheet.templates import add_landmark, get_sheet
from tests.fixtures.shows import field_sheet, two_set_show

TOL = 1e-9
HOLE = ((-1.0, 1.0), (1.0, 1.0), (1.0, 2.0), (-1.0, 2.0))


def test_on_surface_includes_the_edge() -> None:
    sheet = field_sheet()
    assert on_surface(sheet, (0.0, 0.0))
    assert on_surface(sheet, (20.0, 20.0))
    assert not on_surface(sheet, (0.0, -0.01))


def test_walkable_respects_holes() -> None:
    sheet = field_sheet()
    ring = sheet.outline
    holed = sheet.model_copy(update={"walkable": Region(exterior=ring, holes=(HOLE,))})
    assert walkable(sheet, (0.0, 1.5))
    assert not walkable(holed, (0.0, 1.5))
    assert on_surface(holed, (0.0, 1.5))


def test_landmark_matches_name_id_and_alias_case_insensitively() -> None:
    sheet = get_sheet("sheet:hs-football@1")
    assert landmark(sheet, "Home Hash").id == "front-hash"
    assert landmark(sheet, "front-hash").id == "front-hash"
    assert landmark(field_sheet(), "CENTER").id == "c"


def test_landmark_unknown_suggests_close_matches() -> None:
    with pytest.raises(UnknownRefError) as info:
        landmark(field_sheet(), "centre")
    assert "center" in info.value.hint


def test_nearest_landmarks_measures_lines_and_areas() -> None:
    sheet = field_sheet()
    line = Landmark(id="ln", name="tape", kind="line", geometry=((-5.0, 3.0), (5.0, 3.0)))
    area = Landmark(id="ar", name="box", kind="area", geometry=HOLE)
    sheet = add_landmark(add_landmark(sheet, line), area)
    found = nearest_landmarks(sheet, (0.0, 1.5), k=3)
    assert [lm.id for lm, _ in found] == ["ar", "ln", "c"]
    assert approx_equal([d for _, d in found], [0.0, 1.5, 8.5], TOL)


def test_nearest_landmarks_filters_kinds_and_limits() -> None:
    sheet = get_sheet("sheet:hs-football@1")
    found = nearest_landmarks(sheet, (0.0, 15.0), k=1, kinds=("point",))
    assert [lm.id for lm, _ in found] == ["front-hash-50"]
    assert nearest_landmarks(sheet, (0.0, 15.0), k=0) == []


def test_zones_at_merges_show_zones_and_filters_by_count() -> None:
    show = two_set_show()
    timed = Zone(
        id="timed",
        kind=ZoneKind.RESTRICTED,
        polygon=Region(exterior=((-1.0, -2.0), (1.0, -2.0), (1.0, -1.0), (-1.0, -1.0))),
        active=((0.0, 4.0),),
    )
    show = show.model_copy(update={"zones": (timed,)})
    assert [z.id for z in zones_at(show, (0.0, -1.5))] == ["pit", "timed"]
    assert [z.id for z in zones_at(show, (0.0, -1.5), t=8.0)] == ["pit"]
    assert zones_at(show, (10.0, 10.0)) == []


CENTER_TOL = 1e-9
SQUARE_SIDE = 4.0
NEAR_POINT = (1.0, 1.0)


@pytest.mark.defect("UP-9.4-25")
@pytest.mark.xfail(strict=True, reason="UP-9.4-25")
def test_regression_landmark_anchor_ignores_repeated_closing_vertex() -> None:
    """Counts a repeated closing vertex once when anchoring an area (audit 9.4)."""
    side = SQUARE_SIDE
    ring = ((0.0, 0.0), (side, 0.0), (side, side), (0.0, side), (0.0, 0.0))
    area = Landmark(id="box", name="box", kind="area", geometry=ring)
    assert approx_equal(landmark_anchor(area), (side / 2, side / 2), CENTER_TOL)


@pytest.mark.defect("UP-9.4-26")
@pytest.mark.xfail(strict=True, reason="UP-9.4-26")
def test_regression_landmark_anchor_is_the_area_centroid() -> None:
    """Anchors an area at its centroid, not the mean of its vertices (audit 9.4)."""
    ring = ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0))
    area = Landmark(id="strip", name="strip", kind="area", geometry=ring)
    assert approx_equal(landmark_anchor(area), (2.0, 1.0), CENTER_TOL)


@pytest.mark.defect("UP-9.4-27")
@pytest.mark.xfail(strict=True, reason="UP-9.4-27")
def test_regression_nearest_landmarks_rejects_fractional_k() -> None:
    """Rejects k=1.5 with a ShowPyError, not TypeError (audit 9.4)."""
    with pytest.raises(SchemaError):
        nearest_landmarks(field_sheet(), NEAR_POINT, k=1.5)  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.4-28")
@pytest.mark.xfail(strict=True, reason="UP-9.4-28")
def test_regression_nearest_landmarks_rejects_nan_point() -> None:
    """Rejects a NaN point instead of returning arbitrary distances (audit 9.4)."""
    with pytest.raises(ShowPyError):
        nearest_landmarks(field_sheet(), (float("nan"), 0.0))


@pytest.mark.defect("UP-9.4-29")
@pytest.mark.xfail(strict=True, reason="UP-9.4-29")
def test_regression_nearest_landmarks_rejects_unknown_kind() -> None:
    """Rejects an unknown landmark kind instead of returning an empty list (audit 9.4)."""
    with pytest.raises(ShowPyError):
        nearest_landmarks(field_sheet(), NEAR_POINT, kinds=["blob"])  # type: ignore[list-item]
