"""
sheets.py --- the example sheet shared by the example shows

Contains:
    WIDTH: width of the example sheet in meters
    DEPTH: depth of the example sheet in meters
    field_sheet(): builds a 40 x 20 m blank sheet, origin front-center
"""

from e8s.core.types import Region
from e8s.sheet.models import Sheet, SurfaceType

WIDTH = 40.0
DEPTH = 20.0


def field_sheet() -> Sheet:
    """Builds a 40 x 20 m blank sheet with its origin at the front-center.

    >>> field_sheet().size_m
    (40.0, 20.0)
    """
    half = WIDTH / 2
    ring = ((-half, 0.0), (half, 0.0), (half, DEPTH), (-half, DEPTH))
    return Sheet(
        ref="sheet:test-field@1",
        name="Test field",
        surface_type=SurfaceType.BLANK,
        size_m=(WIDTH, DEPTH),
        outline=ring,
        walkable=Region(exterior=ring),
    )
