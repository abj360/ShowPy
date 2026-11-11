#!/usr/bin/env python3

"""
test_bounds.py --- tests surface and walkable tests, landmark lookup, nearest landmarks, zones
"""

import pytest

from stage.core.errors import UnknownRefError
from stage.core.types import Region
from stage.core.util import approx_equal
from stage.sheet.bounds import landmark, nearest_landmarks, on_surface, walkable, zones_at
from stage.sheet.models import Landmark, Zone, ZoneKind
from stage.sheet.templates import add_landmark, get_sheet
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
