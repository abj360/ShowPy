"""
text_spread.py --- spreads each glyph stroke's spots so that strokes which touch keep them apart

A stroke's junction gap is `JUNCTION_GAP_SHARE` of its spot spacing (its length over its gaps
between spots), never less than `COINCIDENT_TOL_M`. Two slots keep the smaller of their
strokes' gaps apart. Each stroke takes the first candidate spread that keeps that rule against
every earlier slot. Where no spread can, as where Hershey strokes run doubled or converge, it
takes the candidate whose closest spot is farthest from the rest, provided that distance is at
least `COINCIDENT_TOL_M`, and the stroke is marked crowded so the caller reports it. Two spots
never share a point: below `COINCIDENT_TOL_M` the stroke raises `COINCIDENT_SLOTS`.

Contains:
    COINCIDENT_TOL_M: smallest distance allowed between two slots
    JUNCTION_GAP_SHARE: share of a stroke's spot spacing its spots keep from other slots
    StrokeSpread: holds one stroke's slots, tangents, junction gap and closest distance
    stroke_gap(): returns the junction gap of a stroke with k spots
    SlotSpreader: spreads strokes one by one against the slots placed so far
    SlotSpreader.place(): spreads k spots on one stroke and keeps them as slots
"""

import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import GeometryError
from e8s.core.types import Points
from e8s.geo.curve_base import Curve, FloatArray

COINCIDENT_TOL_M = 1e-6
# A spot a quarter of its stroke's spacing from another stroke's spot still reads as its own
# dot; closer, two performers share one mark (final review I1, 2026-11-16).
JUNCTION_GAP_SHARE = 0.25
SHIFT_STEPS = 4
# The avoiding spread samples a stroke this many times per spot to find the free stretches.
SAMPLES_PER_SPOT = 8
MIN_SAMPLES = 64
NEIGHBOR_CELLS = tuple((dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

type Cell = tuple[int, int]
type Slot = tuple[float, float, float]


@dataclass(frozen=True)
class StrokeSpread:
    """Holds the slots chosen for one stroke.

    Attributes:
        points: Slot positions (k, 2) along the stroke.
        tangents: Unit tangents (k, 2) at the slots.
        gap: Junction gap the stroke's spots should keep, in meters.
        nearest: Closest distance from one of its spots to another slot; inf when none is
            within the largest gap.
    """

    points: Points
    tangents: Points
    gap: float
    nearest: float

    @property
    def is_crowded(self) -> bool:
        """Tells whether a spot sits closer than the junction gap to another slot."""
        return self.nearest < self.gap


def _spacing(stroke: Curve, k: int) -> float:
    return stroke.length() / (k if stroke.closed else k - 1)


def stroke_gap(stroke: Curve, k: int) -> float:
    """Returns the junction gap of a stroke with k spots: a share of its spot spacing.

    >>> from e8s.geo.curves import Polyline
    >>> stroke_gap(Polyline(points=((0.0, 0.0), (4.0, 0.0))), 3)
    0.5
    """
    return max(COINCIDENT_TOL_M, JUNCTION_GAP_SHARE * _spacing(stroke, k))


def _even_spreads(length: float, k: int, *, closed: bool) -> Iterator[FloatArray]:
    """Yields arc-length stations for k spots, preferred spread first, then shifted ones.

    An open spread `(j + t) L / (k - 1 + t + u)` leaves margins t and u (in spacings) at the
    ends; t and u step by quarters. A closed spread turns by a fraction of its spacing.
    """
    j = np.arange(k, dtype=float)
    if closed:
        for turn in (0.0, 0.5, 0.25, 0.75):
            yield (j + turn) * length / k
        return
    first = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0), (0.5, 0.5)]
    steps = [step / SHIFT_STEPS for step in range(SHIFT_STEPS + 1)]
    margins = first + [(t, u) for t in steps for u in steps if (t, u) not in first]
    for t, u in margins:
        yield (j + t) * length / (k - 1 + t + u)


class SlotSpreader:
    """Spreads strokes one by one, keeping each new slot its junction gap from earlier ones.

    Slots sit in a hash of cells as wide as the largest gap, so every check reads nine cells
    and a placement stays near linear in the slot count.

    Attributes:
        cell: Cell width in meters, the largest junction gap of any stroke to be placed.
        cells: Accepted slots, each as (x, y, gap), keyed by the cell that holds them.
    """

    def __init__(self, cell: float) -> None:
        self.cell = cell
        self.cells: dict[Cell, list[Slot]] = {}

    def place(self, stroke: Curve, k: int, char: str) -> StrokeSpread:
        """Spreads k spots on one stroke and keeps them as slots.

        Args:
            stroke: Glyph stroke in meters.
            k: Spots this stroke gets.
            char: Character the stroke draws, named in an error.

        Returns:
            spread: The chosen slots; `is_crowded` when no spread kept the junction gap.

        Raises:
            GeometryError: `COINCIDENT_SLOTS` when every spread puts a spot within
                `COINCIDENT_TOL_M` of another slot.
        """
        gap = stroke_gap(stroke, k)
        best: tuple[float, FloatArray] | None = None
        for stations in self._spreads(stroke, k, gap):
            points = stroke.point_at(stations)
            nearest = self._nearest(points, gap)
            if nearest >= gap:
                return self._keep(stroke, stations, gap, nearest)
            if best is None or nearest > best[0]:
                best = (nearest, stations)
        if best is not None and best[0] >= COINCIDENT_TOL_M:
            return self._keep(stroke, best[1], gap, best[0])
        raise GeometryError(
            f"no spacing of {k} spots on a stroke of {char!r} keeps them apart",
            hint="change n or the text",
            context={"char": char, "spots": k, "tolerance": COINCIDENT_TOL_M},
            code=ErrorCode.COINCIDENT_SLOTS,
        )

    def _spreads(self, stroke: Curve, k: int, gap: float) -> Iterator[FloatArray]:
        yield from _even_spreads(stroke.length(), k, closed=stroke.closed)
        avoiding = self._avoiding_spread(stroke, k, gap)
        if avoiding is not None:
            yield avoiding

    def _avoiding_spread(self, stroke: Curve, k: int, gap: float) -> FloatArray | None:
        """Returns k stations spread evenly over the stretches clear of earlier slots.

        The stroke is sampled; samples within the junction gap of an earlier slot are dropped,
        and the spots take evenly spaced ranks among the clear samples.
        """
        length = stroke.length()
        count = max(MIN_SAMPLES, SAMPLES_PER_SPOT * k)
        samples = (np.arange(count) + 0.5) * length / count
        points = stroke.point_at(samples).tolist()
        clear = np.array([not self._crowds((x, y, gap), self.cells) for x, y in points])
        free = int(clear.sum())
        if free < k:
            return None
        ranks = np.flatnonzero(clear)[((np.arange(k) + 0.5) * free / k).astype(int)]
        stations: FloatArray = samples[ranks]
        return stations

    def _nearest(self, points: Points, gap: float) -> float:
        """Returns the closest distance at which a point breaks a junction gap; inf if none does.

        Points are checked against the kept slots and against each other.
        """
        nearest = math.inf
        pending: dict[Cell, list[Slot]] = {}
        for x, y in points.tolist():
            slot = (x, y, gap)
            cell = self._cell(slot)
            for cells in (self.cells, pending):
                nearest = min(nearest, _closest_breach(slot, cell, cells))
            pending.setdefault(cell, []).append(slot)
        return nearest

    def _crowds(self, slot: Slot, cells: Mapping[Cell, list[Slot]]) -> bool:
        return _closest_breach(slot, self._cell(slot), cells) < math.inf

    def _keep(
        self, stroke: Curve, stations: FloatArray, gap: float, nearest: float
    ) -> StrokeSpread:
        points = stroke.point_at(stations)
        for x, y in points.tolist():
            slot = (x, y, gap)
            self.cells.setdefault(self._cell(slot), []).append(slot)
        return StrokeSpread(points, stroke.tangent_at(stations), gap, nearest)

    def _cell(self, slot: Slot) -> Cell:
        return math.floor(slot[0] / self.cell), math.floor(slot[1] / self.cell)


def _closest_breach(slot: Slot, cell: Cell, cells: Mapping[Cell, Sequence[Slot]]) -> float:
    """Returns the closest distance to a neighbour within the smaller of the two gaps, or inf."""
    closest = math.inf
    for dx, dy in NEIGHBOR_CELLS:
        for other in cells.get((cell[0] + dx, cell[1] + dy), ()):
            distance = math.dist(slot[:2], other[:2])
            if distance < min(slot[2], other[2]):
                closest = min(closest, distance)
    return closest
