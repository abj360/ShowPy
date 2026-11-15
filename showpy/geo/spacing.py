"""
spacing.py --- places spots evenly along curves, inside areas, in blocks, rings and scatters

Contains:
    space_along(): spaces n spots evenly by arc length
    space_by_interval(): spaces spots at a fixed interval
    space_proportional(): shares n spots across curves at one interval
    space_with_gaps(): spaces spots evenly with extra room after chosen spots
    fill(): fills an area with a grid, offset or hex lattice
    block(): builds a rectangular block, front row first
    ranks_and_files(): indexes block points by row and column
    concentric(): spaces spots around concentric rings
    scatter(): scatters spots organically with a minimum gap (Bridson Poisson disk)
    distribute_on_points(): picks well-spread spots from candidates
"""

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
import shapely
import shapely.affinity
from shapely.prepared import prep

from showpy.core.caps import MAX_POINTS, require_size
from showpy.core.errors import SpacingError
from showpy.core.types import Degrees, Meters, Point, Points
from showpy.core.units import facing_vector
from showpy.core.util import as_points, seeded_rng
from showpy.geo.checks import require_finite_as
from showpy.geo.curve_base import Curve
from showpy.geo.regions import Area

BRIDSON_ATTEMPTS = 30
HEX_ROW_FACTOR = math.sqrt(3) / 2
FIT_EPS = 1e-9
LATTICE_ROW_PAD = 4
LATTICE_COL_PAD = 6

type FillPattern = Literal["grid", "offset", "hex"]
type BlockAnchor = Literal[
    "center", "front-left", "front-center", "front-right", "back-left", "back-center", "back-right"
]


def _require_points(estimate: float, what: str) -> None:
    if not math.isfinite(estimate):
        raise SpacingError(
            f"{what} count is not finite",
            hint="use a larger gap or a finite area",
            context={"what": what},
        )
    require_size(math.ceil(estimate), MAX_POINTS, what=what, error=SpacingError)


def _require_count(n: int) -> None:
    if n < 1:
        raise SpacingError("need at least 1 spot", hint="pass n >= 1", context={"n": n})


def space_along(
    curve: Curve, n: int, start: Meters = 0.0, end: Meters | None = None, endpoints: bool = True
) -> Points:
    """Spaces n spots evenly by arc length between `start` and `end`.

    With `endpoints=False`, spots sit at the centers of n equal cells. A closed curve spaced
    over its full length never repeats the start.

    >>> from showpy.geo.shapes import line
    >>> space_along(line((0, 0), (4, 0)), 3).tolist()
    [[0.0, 0.0], [2.0, 0.0], [4.0, 0.0]]
    """
    _require_count(n)
    stop = curve.length() if end is None else end
    if n == 1:
        return curve.point_at(np.array([(start + stop) / 2]))
    full_loop = curve.closed and math.isclose(stop - start, curve.length())
    if not endpoints:
        step = (stop - start) / n
        return curve.point_at(start + step * (np.arange(n) + 0.5))
    divisions = n if full_loop else n - 1
    return curve.point_at(start + (stop - start) * np.arange(n) / divisions)


def space_by_interval(
    curve: Curve,
    interval: Meters,
    start: Meters = 0.0,
    n: int | None = None,
    align: Literal["start", "end", "center"] = "start",
) -> Points:
    """Spaces spots `interval` meters apart; without n, places as many as fit.

    >>> from showpy.geo.shapes import line
    >>> len(space_by_interval(line((0, 0), (9, 0)), 3.0))
    4
    """
    require_finite_as(SpacingError, interval, name="interval", low=0.0, low_open=True)
    available = curve.length() - start
    if not math.isfinite(available / interval):
        raise SpacingError(
            f"interval {interval} m is too small for {available:.2f} m",
            hint="use a larger interval",
            context={"interval": interval},
        )
    max_fit = math.floor(available / interval + FIT_EPS) + 1
    count = max_fit if n is None else n
    if count > max_fit:
        raise SpacingError(
            f"{count} spots at {interval} m do not fit in {available:.2f} m",
            hint=f"use at most {max_fit} spots or a smaller interval",
            context={"max_fit": max_fit, "requested": count},
        )
    _require_count(count)
    require_size(count, MAX_POINTS, what="spots", error=SpacingError)
    spare = available - (count - 1) * interval
    shift = {"start": 0.0, "end": spare, "center": spare / 2}[align]
    return curve.point_at(start + shift + interval * np.arange(count))


def _apportion(lengths: Sequence[float], n: int) -> list[int]:
    if n < len(lengths):
        raise SpacingError(
            "fewer spots than curves", hint="give each curve at least one spot", context={"n": n}
        )
    total = sum(lengths)
    extra = n - len(lengths)
    quotas = [extra * length / total for length in lengths]
    counts = [1 + math.floor(q) for q in quotas]
    order = sorted(range(len(quotas)), key=lambda i: (-(quotas[i] - math.floor(quotas[i])), i))
    for i in order[: n - sum(counts)]:
        counts[i] += 1
    return counts


def space_proportional(curves: Sequence[Curve], n: int) -> list[Points]:
    """Shares n spots across curves in proportion to length, so intervals match.

    Counts use largest-remainder apportionment; every curve gets at least one spot.

    >>> from showpy.geo.shapes import line
    >>> [len(p) for p in space_proportional([line((0, 0), (4, 0)), line((0, 1), (12, 1))], 6)]
    [2, 4]
    """
    counts = _apportion([c.length() for c in curves], n)
    return [space_along(curve, k) for curve, k in zip(curves, counts, strict=True)]


def space_with_gaps(curve: Curve, n: int, gaps: Sequence[tuple[int, Meters]]) -> Points:
    """Spaces n spots evenly, adding `extra_m` after the spot at each listed index.

    >>> from showpy.geo.shapes import line
    >>> space_with_gaps(line((0, 0), (10, 0)), 3, [(0, 2.0)])[:, 0].tolist()
    [0.0, 6.0, 10.0]
    """
    _require_count(n)
    extra = np.zeros(max(n - 1, 0))
    for index, meters in gaps:
        if not 0 <= index < n - 1:
            raise SpacingError(f"gap index {index} out of range", hint=f"use 0..{n - 2}")
        extra[index] += meters
    step = (curve.length() - extra.sum()) / max(n - 1, 1)
    if step < 0:
        raise SpacingError("gaps exceed the curve length", hint="shrink the gaps")
    stations = np.concatenate([[0.0], np.cumsum(step + extra)])
    return curve.point_at(stations)


def _require_lattice_size(
    bounds: tuple[float, ...], interval: Meters, pattern: FillPattern
) -> None:
    minx, miny, maxx, maxy = bounds
    row_step = interval * HEX_ROW_FACTOR if pattern == "hex" else interval
    edges = (miny / row_step, maxy / row_step, minx / interval, maxx / interval)
    if not all(math.isfinite(edge) for edge in edges):
        _require_points(math.inf, "lattice points")
    rows = edges[1] - edges[0] + LATTICE_ROW_PAD
    cols = edges[3] - edges[2] + LATTICE_COL_PAD
    _require_points(rows * cols, "lattice points")


def _lattice_axes(
    bounds: tuple[float, ...], interval: Meters, pattern: FillPattern
) -> tuple[Meters, range, range]:
    minx, miny, maxx, maxy = bounds
    row_step = interval * HEX_ROW_FACTOR if pattern == "hex" else interval
    rows = range(math.floor(miny / row_step) - 1, math.ceil(maxy / row_step) + 2)
    cols = range(math.floor(minx / interval) - 2, math.ceil(maxx / interval) + 3)
    return row_step, rows, cols


def _lattice(bounds: tuple[float, ...], interval: Meters, pattern: FillPattern) -> Points:
    row_step, row_range, col_range = _lattice_axes(bounds, interval, pattern)
    rows, cols = np.fromiter(row_range, dtype=float), np.fromiter(col_range, dtype=float)
    pts = []
    for r in rows:
        shift = interval / 2 if pattern != "grid" and r % 2 else 0.0
        pts.append(np.stack([cols * interval + shift, np.full(len(cols), r * row_step)], axis=1))
    return np.vstack(pts)


def _rotate_about(pts: Points, about: Point, degrees: Degrees) -> Points:
    rad = math.radians(degrees)
    c, s = math.cos(rad), math.sin(rad)
    local = pts - np.asarray(about)
    result: Points = local @ np.array([[c, s], [-s, c]]) + np.asarray(about)
    return result


def fill(
    area: Area,
    interval: Meters,
    pattern: FillPattern = "grid",
    angle_deg: Degrees = 0.0,
    anchor: Point | None = None,
    n: int | None = None,
    margin: Meters = 0.0,
) -> Points:
    """Fills an area with lattice spots `interval` apart, one lattice point on `anchor`.

    With n, keeps the n spots closest to the anchor (ties broken by position order).

    >>> from showpy.geo.regions import region
    >>> len(fill(region([(0, 0), (2, 0), (2, 2), (0, 2)]), 1.0, anchor=(0, 0)))
    9
    """
    require_finite_as(SpacingError, interval, name="interval", low=0.0, low_open=True)
    usable = area.buffer(-margin) if margin else area
    origin = anchor or (usable.centroid.x, usable.centroid.y)
    local_area = shapely.affinity.rotate(usable, -angle_deg, origin=origin)
    minx, miny, maxx, maxy = local_area.bounds
    shifted = (minx - origin[0], miny - origin[1], maxx - origin[0], maxy - origin[1])
    _require_lattice_size(shifted, interval, pattern)
    lattice = _lattice(shifted, interval, pattern) + np.asarray(origin)
    inside = prep(local_area.buffer(FIT_EPS))
    kept = np.array([p for p in lattice if inside.contains(shapely.Point(p))]).reshape(-1, 2)
    pts = _rotate_about(kept, origin, angle_deg)
    if n is None:
        return pts
    if n > len(pts):
        raise SpacingError(
            f"only {len(pts)} spots fit",
            hint="lower n or the interval",
            context={"max_fit": len(pts)},
        )
    order = np.lexsort((pts[:, 0], pts[:, 1], np.linalg.norm(pts - np.asarray(origin), axis=1)))
    return pts[np.sort(order[:n])]


def block(
    origin: Point,
    rows: int,
    cols: int,
    interval: Meters,
    facing: Degrees = 0.0,
    anchor: BlockAnchor = "center",
) -> Points:
    """Builds a rows x cols block, front row first, columns left to right.

    "Front" is the facing direction; left and right are as the audience sees a block
    facing 0°, and the whole frame turns with `facing`.

    >>> block((0, 0), 1, 2, 1.0, anchor="front-left").tolist()
    [[0.0, 0.0], [1.0, 0.0]]
    """
    if rows < 1 or cols < 1 or interval <= 0:
        raise SpacingError("block needs rows, cols >= 1 and interval > 0", hint="fix the block")
    front = np.asarray(facing_vector(facing))
    lateral = np.array([-front[1], front[0]])
    r, c = np.divmod(np.arange(rows * cols), cols)
    depth_extent, width_extent = (rows - 1) * interval, (cols - 1) * interval
    back_off = {"front": 0.0, "center": depth_extent / 2, "back": depth_extent}
    side_off = {"left": 0.0, "center": width_extent / 2, "right": width_extent}
    edge, _, side = anchor.partition("-") if anchor != "center" else ("center", "", "center")
    lateral_pos = c * interval - side_off[side]
    back_pos = r * interval - back_off[edge]
    return np.asarray(origin) + np.outer(lateral_pos, lateral) - np.outer(back_pos, front)


def ranks_and_files(points: Points, rows: int, cols: int) -> list[list[int]]:
    """Indexes points produced in block order (front row first) by row, then column.

    >>> ranks_and_files(block((0, 0), 2, 2, 1.0), 2, 2)
    [[0, 1], [2, 3]]
    """
    if len(points) != rows * cols:
        raise SpacingError(
            f"{len(points)} points do not make a {rows}x{cols} block",
            hint="pass exactly rows * cols points in block order",
        )
    return [[r * cols + c for c in range(cols)] for r in range(rows)]


def concentric(
    center: Point, radii: Sequence[Meters], counts: Sequence[int], start_deg: Degrees = 0.0
) -> list[Points]:
    """Spaces counts[i] spots evenly around the ring of radii[i], counter-clockwise.

    >>> [len(r) for r in concentric((0, 0), [1.0, 2.0], [3, 6])]
    [3, 6]
    """
    rings = []
    for radius, count in zip(radii, counts, strict=True):
        _require_count(count)
        theta = np.radians(start_deg + 360.0 * np.arange(count) / count)
        rings.append(
            np.stack([center[0] + radius * np.cos(theta), center[1] + radius * np.sin(theta)], 1)
        )
    return rings


class _PoissonGrid:
    """Tracks accepted Poisson-disk samples in a background grid for O(1) gap checks."""

    def __init__(self, area: Area, min_gap: Meters) -> None:
        self.inside = prep(area)
        self.minx, self.miny, self.maxx, self.maxy = area.bounds
        self.min_gap = min_gap
        self.cell = min_gap / math.sqrt(2)
        self.cells: dict[tuple[int, int], Point] = {}

    def _key(self, p: Point) -> tuple[int, int]:
        return int((p[0] - self.minx) // self.cell), int((p[1] - self.miny) // self.cell)

    def fits(self, p: Point) -> bool:
        in_box = self.minx <= p[0] <= self.maxx and self.miny <= p[1] <= self.maxy
        if not in_box or not self.inside.contains(shapely.Point(p)):
            return False
        kx, ky = self._key(p)
        near = (self.cells.get((kx + i, ky + j)) for i in range(-2, 3) for j in range(-2, 3))
        return all(q is None or math.dist(p, q) >= self.min_gap for q in near)

    def add(self, p: Point) -> None:
        self.cells[self._key(p)] = p


def _bridson(area: Area, min_gap: Meters, seed: int) -> Points:
    rng = seeded_rng(seed)
    grid = _PoissonGrid(area, min_gap)
    seed_point = area.representative_point()
    first = (seed_point.x, seed_point.y)
    grid.add(first)
    accepted, active = [first], [first]
    while active:
        index = int(rng.integers(len(active)))
        base = active[index]
        for _ in range(BRIDSON_ATTEMPTS):
            radius, theta = min_gap * (1 + rng.random()), 2 * math.pi * rng.random()
            p = (base[0] + radius * math.cos(theta), base[1] + radius * math.sin(theta))
            if grid.fits(p):
                grid.add(p)
                accepted.append(p)
                active.append(p)
                break
        else:
            active.pop(index)
    return np.asarray(accepted, dtype=float)


def _poisson_sample_bound(area: Area, min_gap: Meters) -> float:
    minx, miny, maxx, maxy = area.bounds
    cell = min_gap / math.sqrt(2)
    return _axis_cells((maxx - minx) / cell) * _axis_cells((maxy - miny) / cell)


def _axis_cells(extent_in_cells: float) -> float:
    if not math.isfinite(extent_in_cells):
        return extent_in_cells
    return math.floor(extent_in_cells) + 1.0


def scatter(area: Area, n: int, min_gap: Meters, seed: int = 0) -> Points:
    """Scatters n spots organically, every pair at least `min_gap` apart.

    Bridson's Poisson-disk sampling fills the area, then farthest-point selection keeps n.
    Each cell of the `min_gap / sqrt(2)` background grid holds at most one sample, so the cells
    covering the bounding box, floor(w / cell) + 1 per axis, bound the sample count; a bound
    over MAX_POINTS is rejected.

    >>> from showpy.geo.regions import region
    >>> len(scatter(region([(0, 0), (10, 0), (10, 10), (0, 10)]), 5, 1.0))
    5
    """
    _require_count(n)
    require_finite_as(SpacingError, min_gap, name="min_gap", low=0.0, low_open=True)
    require_size(n, MAX_POINTS, what="spots", error=SpacingError)
    _require_points(_poisson_sample_bound(area, min_gap), "samples")
    candidates = _bridson(area, min_gap, seed)
    if n > len(candidates):
        raise SpacingError(
            f"only {len(candidates)} spots fit at gap {min_gap} m",
            hint="lower n or min_gap",
            context={"max_fit": len(candidates)},
        )
    return distribute_on_points(candidates, n)


def distribute_on_points(
    candidates: object, n: int, method: Literal["farthest", "random"] = "farthest", seed: int = 0
) -> Points:
    """Picks n well-spread candidates: greedy farthest-point, or a seeded random draw.

    Farthest-point starts from the candidate farthest from the centroid (first index on
    ties), so the result is deterministic.

    >>> distribute_on_points([(0, 0), (1, 0), (9, 0)], 2).tolist()
    [[0.0, 0.0], [9.0, 0.0]]
    """
    pts = as_points(candidates)
    _require_count(n)
    if n > len(pts):
        raise SpacingError(
            f"only {len(pts)} candidates", hint="lower n", context={"max_fit": len(pts)}
        )
    if method == "random":
        return pts[np.sort(seeded_rng(seed).choice(len(pts), size=n, replace=False))]
    chosen = [int(np.argmax(np.linalg.norm(pts - pts.mean(axis=0), axis=1)))]
    nearest = np.linalg.norm(pts - pts[chosen[0]], axis=1)
    while len(chosen) < n:
        chosen.append(int(np.argmax(nearest)))
        nearest = np.minimum(nearest, np.linalg.norm(pts - pts[chosen[-1]], axis=1))
    picked: Points = pts[np.sort(chosen)]
    return picked
