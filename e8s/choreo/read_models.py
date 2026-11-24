"""
read_models.py --- data classes returned by the floor readers

Contains:
    Frames: every selected spot's position and facing at regular frame times
    CoordinateTable: selected spots' positions at each set, unrounded, in one length unit
    SegmentKind: names what a spot does in one segment of a move
    SegmentDescription: one segment of a spot's move: a hold or one kind of travel
    TransitionDescription: every selected spot's segments in one transition, alike spots grouped
"""

from collections.abc import Mapping
from typing import Annotated, Literal, Self

import numpy as np
from pydantic import model_validator

from e8s.core.arrays import ArrayModel, BoolArray, FloatArray, ReadOnlyArray
from e8s.core.ids import SetId, SpotId, TransitionId
from e8s.core.readonly import ReadOnly
from e8s.core.types import Degrees, FrozenModel, Meters

type SegmentKind = Literal["hold", "straight", "curve", "follow", "rotate", "custom"]


class Frames(ArrayModel):
    """Holds every selected spot's position and facing at regular frame times.

    Row `i` of `x`, `y` and `facing` is frame `i`; column `j` is spot `spots[j]`.

    Attributes:
        spots: Spot IDs in column order.
        fps: Frames per second of show time.
        seconds: Show seconds of each frame, one frame every 1/fps seconds; shape (frames,).
        counts: Global count of each frame; it stays still during a hold; shape (frames,).
        x: Positions across the sheet in meters; shape (frames, spots).
        y: Positions away from the audience in meters; shape (frames, spots).
        facing: Facings in degrees, 0 facing the audience; shape (frames, spots).
    """

    spots: tuple[SpotId, ...]
    fps: float
    seconds: Annotated[FloatArray, ReadOnlyArray(1)]
    counts: Annotated[FloatArray, ReadOnlyArray(1)]
    x: Annotated[FloatArray, ReadOnlyArray(2, columns="spots")]
    y: Annotated[FloatArray, ReadOnlyArray(2, columns="spots")]
    facing: Annotated[FloatArray, ReadOnlyArray(2, columns="spots")]

    @model_validator(mode="after")
    def _shapes_agree(self) -> Self:
        if self.counts.shape != self.seconds.shape:
            raise ValueError(f"{len(self.counts)} counts for {len(self.seconds)} frame times")
        expected = (len(self.seconds), len(self.spots))
        for name in ("x", "y", "facing"):
            shape = getattr(self, name).shape
            if shape != expected:
                raise ValueError(f"{name} has shape {shape}, not (frames, spots) {expected}")
        return self


class CoordinateTable(ArrayModel):
    """Holds selected spots' positions at each set, unrounded, in one length unit.

    Row `i` of every (sets, spots) array is set `sets[i]`; column `j` is spot `spots[j]`. The
    axes are the library's stage frame: x runs from audience-left to audience-right, y away
    from the audience, and the origin is the sheet's front-center.

    Attributes:
        spots: Spot IDs in column order.
        sets: Set IDs in row order.
        unit: Name of the length unit `x` and `y` are in, recorded from the call that built the
            table. It is a label: the table is not checked against any unit registry, since
            the registry the caller converted with may not be the default one.
        counts: Global count of each set; shape (sets,).
        x: Positions toward audience-right; shape (sets, spots).
        y: Positions away from the audience; shape (sets, spots).
        on_field: Whether each spot is on the field at each set.
    """

    spots: tuple[SpotId, ...]
    sets: tuple[SetId, ...]
    unit: str
    counts: Annotated[FloatArray, ReadOnlyArray(1)]
    x: Annotated[FloatArray, ReadOnlyArray(2, columns="spots")]
    y: Annotated[FloatArray, ReadOnlyArray(2, columns="spots")]
    on_field: Annotated[BoolArray, ReadOnlyArray(2, np.bool_, columns="spots")]

    @model_validator(mode="after")
    def _shapes_agree(self) -> Self:
        if self.counts.shape != (len(self.sets),):
            raise ValueError(f"{len(self.counts)} counts for {len(self.sets)} sets")
        expected = (len(self.sets), len(self.spots))
        for name in ("x", "y", "on_field"):
            shape = getattr(self, name).shape
            if shape != expected:
                raise ValueError(f"{name} has shape {shape}, not (sets, spots) {expected}")
        return self


class SegmentDescription(FrozenModel):
    """Describes one segment of a spot's move: a hold or one kind of travel.

    A straight path bends only between segments, so each straight segment has one direction.

    Attributes:
        kind: What the spot does, read from its path's curve: hold, or travel straight, on a
            curve, along a route, around a pivot, or by a custom motion.
        start: Local count the segment starts at.
        counts: Length of the segment in counts.
        direction: Direction from the segment's start to its end, as a facing (0 toward the
            audience, counter-clockwise positive); None while holding or when it ends where it
            starts.
        distance: Meters travelled along the path.
        stride_m: Mean meters per step, distance / (counts * pace); None while holding.
        pace: Steps per count, as the path row sets it; 2 is double time.
    """

    kind: SegmentKind
    start: float
    counts: float
    direction: Degrees | None
    distance: Meters
    stride_m: float | None
    pace: float


class TransitionDescription(FrozenModel):
    """Holds every selected spot's segments in one transition, with alike spots grouped.

    Attributes:
        transition: The transition described.
        counts: Length of the transition in counts.
        moves: Each selected spot's segments in time order, keyed in selection order.
        groups: Spots whose segments agree in kind, timing, direction, distance, stride and
            pace, each group in selection order, groups ordered by their first spot.
    """

    transition: TransitionId
    counts: float
    moves: Annotated[Mapping[SpotId, tuple[SegmentDescription, ...]], ReadOnly()]
    groups: tuple[tuple[SpotId, ...], ...]
