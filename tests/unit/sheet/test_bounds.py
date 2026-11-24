#!/usr/bin/env python3

"""
test_bounds.py --- tests the surface and walkable checks

Contains:
    test_*(): one behavior of e8s.sheet.bounds each
"""

from e8s.core.types import Region
from e8s.sheet.bounds import on_surface, walkable
from tests.fixtures.shows import field_sheet

HOLE = ((-1.0, 1.0), (1.0, 1.0), (1.0, 2.0), (-1.0, 2.0))


def test_on_surface_includes_the_edge() -> None:
    sheet = field_sheet()
    assert on_surface(sheet, (0.0, 0.0))
    assert on_surface(sheet, (20.0, 20.0))
    assert not on_surface(sheet, (0.0, -0.01))


def test_walkable_respects_holes() -> None:
    sheet = field_sheet()
    ring = sheet.outline
    holed = sheet.replace(walkable=Region(exterior=ring, holes=(HOLE,)))
    assert walkable(sheet, (0.0, 1.5))
    assert not walkable(holed, (0.0, 1.5))
    assert on_surface(holed, (0.0, 1.5))
