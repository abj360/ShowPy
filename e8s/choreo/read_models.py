"""
read_models.py --- data classes returned by the floor readers

Contains:
    Frames: every selected spot's position and facing at regular frame times
"""

from typing import Annotated, Self

from pydantic import model_validator

from e8s.core.arrays import ArrayModel, FloatArray, ReadOnlyArray
from e8s.core.ids import SpotId


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
