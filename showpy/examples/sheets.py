"""
sheets.py --- the example sheet shared by the example shows

Contains:
    WIDTH: width of the example sheet in meters
    DEPTH: depth of the example sheet in meters
    field_sheet(): builds a 40 x 20 m sheet with a center landmark and a front pit zone
"""

from showpy.core.types import Region
from showpy.sheet.models import Landmark, Sheet, SurfaceType, Zone, ZoneKind

WIDTH = 40.0
DEPTH = 20.0


def field_sheet() -> Sheet:
    """Builds a 40 x 20 m sheet: origin front-center, a center landmark, a front pit zone.

    >>> field_sheet().size_m
    (40.0, 20.0)
    """
    half = WIDTH / 2
    ring = ((-half, 0.0), (half, 0.0), (half, DEPTH), (-half, DEPTH))
    pit = Region(exterior=((-4.0, -3.0), (4.0, -3.0), (4.0, -0.5), (-4.0, -0.5)))
    return Sheet(
        ref="sheet:test-field@1",
        name="Test field",
        surface_type=SurfaceType.BLANK,
        size_m=(WIDTH, DEPTH),
        outline=ring,
        walkable=Region(exterior=ring),
        landmarks=(
            Landmark.model_validate(
                {"id": "c", "name": "center", "kind": "point", "geometry": ((0.0, 10.0),)}
            ),
        ),
        zones=(Zone.model_validate({"id": "pit", "kind": ZoneKind.PIT, "polygon": pit}),),
    )
