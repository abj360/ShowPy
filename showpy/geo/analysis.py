"""
analysis.py --- design feedback on a picture: crowding, coverage, balance, symmetry, change

Nothing here passes or fails; results are numbers and grids that inform design. Only
on-field spots count. Measures of motion over time belong with the PathEngine.

Contains:
    density_map(): counts on-field spots per grid cell over the sheet
    coverage(): returns the share of the walkable area within reach of an on-field spot
    balance(): returns the visual weight left versus right, or front versus back
    symmetry_score(): returns how mirrored the picture is, from 0 to 1
    change_amount(): returns the mean distance spots moved between two sets
"""

import math
from typing import Literal

import numpy as np
import numpy.typing as npt
import shapely

from showpy.choreo.models import Set
from showpy.core.caps import MAX_GRID_CELLS
from showpy.core.errors import GeometryError, UnknownRefError
from showpy.core.types import Meters, Points
from showpy.core.util import close_matches
from showpy.geo.checks import require_finite_as
from showpy.geo.measure import mirror_gaps, on_field_ids
from showpy.sheet.models import Sheet

AXIS_INDEX = {"x": 0, "y": 1}
DISK_SEGMENTS = 32
SPREAD_FLOOR = 1e-9


def _require_grid_size(spans: Points, cell: Meters) -> None:
    with np.errstate(over="ignore"):
        cells = float(np.prod([max(1.0, float(np.ceil(span / cell))) for span in spans]))
    if math.isfinite(cells) and cells <= MAX_GRID_CELLS:
        return
    raise GeometryError(
        f"a cell of {cell} m gives over {MAX_GRID_CELLS} grid cells on this sheet",
        hint="pass a larger cell in meters",
        context={"cell": cell, "max_cells": MAX_GRID_CELLS},
    )


def _require_positive(name: str, value: float) -> None:
    if not value > 0:
        raise GeometryError(
            f"{name} must be above 0, got {value}",
            hint=f"pass a positive {name} in meters",
            context={name: value},
        )


def _on_field_points(s: Set) -> Points:
    return s.positions(on_field_ids(s))


def _extent(sheet: Sheet) -> tuple[Points, Points]:
    outline = np.asarray(sheet.outline, dtype=float)
    return outline.min(axis=0), outline.max(axis=0)


def density_map(s: Set, sheet: Sheet, cell: Meters = 1.0) -> npt.NDArray[np.int64]:
    """Counts on-field spots per square cell over the sheet outline's bounding box.

    Row 0 is the front edge and column 0 the audience-left edge; spots outside the box are
    not counted.

    >>> from showpy.choreo.models import Placement
    >>> from showpy.core.types import Region
    >>> ring = ((-1.0, 0.0), (1.0, 0.0), (1.0, 1.0), (-1.0, 1.0))
    >>> sheet = Sheet(
    ...     ref="sheet:demo@1", name="Demo", surface_type="blank", size_m=(2.0, 1.0),
    ...     outline=ring, walkable=Region(exterior=ring),
    ... )
    >>> s = Set(id="1", counts=0, placements=(Placement(entity="A", x=0.5, y=0.5),))
    >>> density_map(s, sheet).tolist()
    [[0, 1]]
    """
    require_finite_as(GeometryError, cell, name="cell", low=0.0, low_open=True)
    lo, hi = _extent(sheet)
    _require_grid_size(hi - lo, cell)
    cols, rows = (max(1, math.ceil(span / cell)) for span in hi - lo)
    pts = _on_field_points(s)
    grid, _, _ = np.histogram2d(
        pts[:, 1],
        pts[:, 0],
        bins=(lo[1] + cell * np.arange(rows + 1), lo[0] + cell * np.arange(cols + 1)),
    )
    counts: npt.NDArray[np.int64] = grid.astype(np.int64)
    return counts


def coverage(s: Set, sheet: Sheet, radius: Meters = 1.0) -> float:
    """Returns the share (0..1) of the walkable area within `radius` of an on-field spot.

    >>> from showpy.choreo.models import Placement
    >>> from showpy.core.types import Region
    >>> ring = ((-1.0, 0.0), (1.0, 0.0), (1.0, 1.0), (-1.0, 1.0))
    >>> sheet = Sheet(
    ...     ref="sheet:demo@1", name="Demo", surface_type="blank", size_m=(2.0, 1.0),
    ...     outline=ring, walkable=Region(exterior=ring),
    ... )
    >>> coverage(Set(id="1", counts=0, placements=(Placement(entity="A", x=0, y=0.5),)), sheet, 5)
    1.0
    """
    _require_positive("radius", radius)
    walkable = sheet.walkable.to_shapely()
    pts = _on_field_points(s)
    if not len(pts) or walkable.area == 0:
        return 0.0
    disks = shapely.buffer(shapely.points(pts), radius, quad_segs=DISK_SEGMENTS)
    reached = shapely.intersection(shapely.union_all(disks), walkable)
    return float(reached.area / walkable.area)


def balance(s: Set, sheet: Sheet, axis: Literal["x", "y"] = "x") -> float:
    """Returns the mean offset of on-field spots from the sheet center, as -1..1 of half its size.

    On x, positive means weight to audience right; on y, toward the back. No spots gives 0.

    >>> from showpy.choreo.models import Placement
    >>> from showpy.core.types import Region
    >>> ring = ((-2.0, 0.0), (2.0, 0.0), (2.0, 2.0), (-2.0, 2.0))
    >>> sheet = Sheet(
    ...     ref="sheet:demo@1", name="Demo", surface_type="blank", size_m=(4.0, 2.0),
    ...     outline=ring, walkable=Region(exterior=ring),
    ... )
    >>> balance(Set(id="1", counts=0, placements=(Placement(entity="A", x=1, y=1),)), sheet)
    0.5
    """
    if axis not in AXIS_INDEX:
        raise UnknownRefError(
            f"unknown axis {axis!r}",
            hint=f"use 'x' or 'y'; did you mean {close_matches(axis, AXIS_INDEX)}?",
            context={"axis": axis},
        )
    column = AXIS_INDEX[axis]
    pts = _on_field_points(s)
    lo, hi = _extent(sheet)
    half = (hi[column] - lo[column]) / 2
    if not len(pts) or half <= 0:
        return 0.0
    offset = (pts[:, column].mean() - (lo[column] + half)) / half
    return float(np.clip(offset, -1.0, 1.0))


def symmetry_score(s: Set, axis_x: float = 0.0) -> float:
    """Returns how mirrored the picture is about x = axis_x: 1 is perfect, 0 is unrelated.

    The score is 1 minus the mean mirror gap divided by the spread (RMS distance of spots
    from their centroid), floored at 0. No spots counts as perfectly mirrored.

    >>> from showpy.choreo.models import Placement
    >>> rows = tuple(Placement(entity=e, x=x, y=1) for e, x in (("A", -2), ("B", 2)))
    >>> symmetry_score(Set(id="1", counts=0, placements=rows))
    1.0
    """
    pts = _on_field_points(s)
    if not len(pts):
        return 1.0
    error = float(mirror_gaps(pts, axis_x).mean())
    spread = float(np.sqrt(((pts - pts.mean(axis=0)) ** 2).sum(axis=1).mean()))
    if error <= SPREAD_FLOOR:
        return 1.0
    return float(max(0.0, 1.0 - error / max(spread, SPREAD_FLOOR)))


def change_amount(a: Set, b: Set) -> Meters:
    """Returns the mean distance moved by spots on the field in both sets; 0 when none are.

    >>> from showpy.choreo.models import Placement
    >>> first = Set(id="1", counts=0, placements=(Placement(entity="A", x=0, y=0),))
    >>> second = Set(id="2", counts=8, placements=(Placement(entity="A", x=3, y=4),))
    >>> change_amount(first, second)
    5.0
    """
    in_b = set(on_field_ids(b))
    shared = [i for i in on_field_ids(a) if i in in_b]
    if not shared:
        return 0.0
    moved = np.linalg.norm(a.positions(shared) - b.positions(shared), axis=1)
    return float(moved.mean())
