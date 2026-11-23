"""
bounds.py --- bounds tests: on the sheet, in the walkable area

Contains:
    on_surface(): reports whether a point lies on the sheet, edge included
    walkable(): reports whether a point lies in the walkable area
"""

import shapely

from e8s.core.deprecation import leaving_call
from e8s.core.types import Point, Region
from e8s.sheet.models import Sheet


def _covers(region: Region, p: Point) -> bool:
    return bool(region.to_shapely().covers(shapely.Point(p)))


def _on_surface(sheet: Sheet, p: Point) -> bool:
    """Reports whether `p` lies on the sheet outline or inside it.

    >>> from e8s.sheet.templates import _blank_sheet
    >>> _on_surface(_blank_sheet(10, 6), (5.0, 6.0)), _on_surface(_blank_sheet(10, 6), (0.0, -1.0))
    (True, False)
    """
    return _covers(Region(exterior=sheet.outline), p)


@leaving_call(since="0.4.0", remove="0.7.0", replacement="on_sheet")
def on_surface(sheet: Sheet, p: Point) -> bool:
    """Reports whether `p` lies on the sheet outline or inside it.

    Deprecated: this name leaves the library in 0.7.0.
    """
    return _on_surface(sheet, p)


def walkable(sheet: Sheet, p: Point) -> bool:
    """Reports whether `p` lies in the walkable area, holes excluded.

    >>> from e8s.sheet.templates import _blank_sheet
    >>> walkable(_blank_sheet(10, 6), (1.0, 1.0))
    True
    """
    return _covers(sheet.walkable, p)
