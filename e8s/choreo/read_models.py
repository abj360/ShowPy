"""
read_models.py --- data classes returned by the floor readers

Contains:
    Frames: every selected spot's position and facing at regular frame times
    CoordinateTable: selected spots' positions at sets, in meters and in steps from the origin
"""

from typing import Annotated, Self

import numpy as np
from pydantic import model_validator

from e8s.core.arrays import ArrayModel, BoolArray, FloatArray, ReadOnlyArray
from e8s.core.ids import LandmarkId, SetId, SpotId
from e8s.core.types import Point
from e8s.sheet.models import AudienceSide


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
    x: Annotated[FloatArray, ReadOnlyArray(2)]
    y: Annotated[FloatArray, ReadOnlyArray(2)]
    facing: Annotated[FloatArray, ReadOnlyArray(2)]

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
    """Holds selected spots' positions at sets, in meters and in steps from the origin.

    Row `i` of every (sets, spots) array is set `sets[i]`; column `j` is spot `spots[j]`. Steps
    are counted from the show's coordinate origin along its axes: `across` is positive toward
    the audience's right, `depth` positive away from the audience, whichever edge of the sheet
    the audience sits at.

    Attributes:
        spots: Spot IDs in column order.
        sets: Set IDs in row order.
        counts: Global count of each set; shape (sets,).
        x: Positions across the sheet in meters; shape (sets, spots).
        y: Positions away from the audience in meters; shape (sets, spots).
        across: Steps across from the origin, rounded half away from zero to `resolution`.
        depth: Steps away from the audience from the origin, rounded like `across`.
        across_exact: Unrounded `across`, for callers that round differently.
        depth_exact: Unrounded `depth`.
        on_field: Whether each spot is on the field at each set.
        origin: Origin of the steps in stage meters.
        origin_landmark: Landmark the origin is named by, or None for a plain point.
        audience_side: Edge of the sheet where the audience sits.
        step: Length of one step in meters.
        resolution: Rounding of `across` and `depth`, in steps.
    """

    spots: tuple[SpotId, ...]
    sets: tuple[SetId, ...]
    counts: Annotated[FloatArray, ReadOnlyArray(1)]
    x: Annotated[FloatArray, ReadOnlyArray(2)]
    y: Annotated[FloatArray, ReadOnlyArray(2)]
    across: Annotated[FloatArray, ReadOnlyArray(2)]
    depth: Annotated[FloatArray, ReadOnlyArray(2)]
    across_exact: Annotated[FloatArray, ReadOnlyArray(2)]
    depth_exact: Annotated[FloatArray, ReadOnlyArray(2)]
    on_field: Annotated[BoolArray, ReadOnlyArray(2, np.bool_)]
    origin: Point
    origin_landmark: LandmarkId | None
    audience_side: AudienceSide
    step: float
    resolution: float

    @model_validator(mode="after")
    def _shapes_agree(self) -> Self:
        if self.counts.shape != (len(self.sets),):
            raise ValueError(f"{len(self.counts)} counts for {len(self.sets)} sets")
        expected = (len(self.sets), len(self.spots))
        for name in ("x", "y", "across", "depth", "across_exact", "depth_exact", "on_field"):
            shape = getattr(self, name).shape
            if shape != expected:
                raise ValueError(f"{name} has shape {shape}, not (sets, spots) {expected}")
        return self
