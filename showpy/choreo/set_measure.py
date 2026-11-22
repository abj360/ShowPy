"""
set_measure.py --- static measures of a set: which spots are on the field, neighbours, symmetry

Contains:
    on_field_ids(): returns the selected spots that are on the field, in selection order
    neighbors(): returns the on-field spots closest to one spot
    nearest(): returns the on-field spots closest to a point
    symmetry_error(): returns the largest mirror mismatch of selected spots about an axis
"""

import math

from showpy.choreo.models import Set
from showpy.core.errors import SchemaError
from showpy.core.ids import SpotId
from showpy.core.selection import operand_ids
from showpy.core.types import Meters, Point
from showpy.core.util import natural_sort
from showpy.geo.measure import ALL, SetSelector, mirror_gaps


def on_field_ids(s: Set, sel: SetSelector = ALL) -> tuple[SpotId, ...]:
    """Returns the selected spots that are on the field, in selection order.

    A selection naming an unknown spot raises UnknownRefError.

    >>> from showpy.choreo.models import Placement
    >>> s = Set(id="1", counts=0, placements=(Placement(entity="A", x=0, y=0, on_field=False),))
    >>> on_field_ids(s)
    ()
    """
    if isinstance(sel, str) and sel != ALL:
        raise SchemaError(
            f"selector {sel!r} is not a list of spot IDs",
            hint='pass "all", a list of spot IDs or a Selection',
            context={"selector": sel},
        )
    ids = s.entity_ids() if sel == ALL else tuple(dict.fromkeys(operand_ids(sel)))
    return tuple(p.entity for p in map(s.get, ids) if p.on_field)


def _closest(s: Set, target: Point, k: int, skip: str | None) -> list[tuple[SpotId, Meters]]:
    ids = [i for i in on_field_ids(s) if i != skip]
    rank = {spot: n for n, spot in enumerate(natural_sort(ids))}
    scored = [(math.dist(target, s.get(i).point), rank[i], i) for i in ids]
    return [(i, d) for d, _, i in sorted(scored)[:k]]


def neighbors(s: Set, entity: str, k: int = 2) -> list[tuple[SpotId, Meters]]:
    """Returns the k on-field spots closest to `entity`, nearest first, ties by natural ID.

    Off-field spots are never neighbours; the entity itself is measured from where it stands.

    >>> from showpy.choreo.models import Placement
    >>> rows = tuple(Placement(entity=e, x=x, y=0) for e, x in (("A", 0), ("B", 1), ("C", 3)))
    >>> neighbors(Set(id="1", counts=0, placements=rows), "A", k=1)
    [('B', 1.0)]
    """
    return _closest(s, s.get(entity).point, k, entity)


def nearest(s: Set, point: Point, k: int = 1) -> list[tuple[SpotId, Meters]]:
    """Returns the k on-field spots closest to a point, nearest first, ties by natural ID.

    >>> from showpy.choreo.models import Placement
    >>> rows = tuple(Placement(entity=e, x=x, y=0) for e, x in (("A", 0), ("B", 4)))
    >>> nearest(Set(id="1", counts=0, placements=rows), (3.0, 0.0))
    [('B', 1.0)]
    """
    return _closest(s, point, k, None)


def symmetry_error(s: Set, sel: SetSelector = ALL, axis_x: float = 0.0) -> Meters:
    """Returns the largest distance from a selected spot's mirror image to its nearest partner.

    Only on-field spots count; with none selected the error is 0.

    >>> from showpy.choreo.models import Placement
    >>> rows = tuple(Placement(entity=e, x=x, y=1) for e, x in (("A", -2), ("B", 2.5)))
    >>> symmetry_error(Set(id="1", counts=0, placements=rows))
    0.5
    """
    pts = s.positions(on_field_ids(s, sel))
    gaps = mirror_gaps(pts, axis_x)
    return float(gaps.max()) if len(gaps) else 0.0
