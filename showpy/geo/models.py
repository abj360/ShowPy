"""
models.py --- geometry data classes: snap reports, spacing stats, form and speed metrics, fonts

Contains:
    SnapReport: reports how many points a snap moved and by how much at most
    IntervalStats: summarizes the gaps between spots
    FormMetrics: summarizes the shape quality of a form's points
    SpeedStats: summarizes the movement demand of one spot over one transition
    FontGlyph: holds the advance and stroke path data of one character
    Font: holds a stroke font's metrics and glyph table
    Font.cap(): returns the font's cap height
    TextAlign: names how each line sits horizontally on the layout point
    TextAnchor: names which line of the text block sits vertically on the layout point
    DEFAULT_LINE_SPACING: baseline-to-baseline distance in ems of the first font
    LaidGlyph: holds one laid-out character's font, line and strokes in stage meters
"""

from collections.abc import Mapping
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, field_validator

from showpy.core.readonly import ReadOnly
from showpy.core.types import Finite, FrozenModel
from showpy.geo.curves import Polyline, SvgCurve
from showpy.geo.font_strokes import sample_stroke

FONT_FORMAT = "showpy.font.v1"
FONT_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]*$"
FONT_NAME_MAX_LENGTH = 64
CAP_REFERENCE_GLYPH = "H"
DEFAULT_LINE_SPACING = 1.2

type TextAlign = Literal["left", "center", "right"]
type TextAnchor = Literal["baseline", "middle", "top"]
type LaidStroke = Annotated[Polyline | SvgCurve, Field(discriminator="kind")]


class SnapReport(FrozenModel):
    """Reports how many points a snap moved and by how much at most.

    Attributes:
        moved: Points whose position changed.
        max_shift: Largest distance any point moved.
    """

    moved: int
    max_shift: Finite


class IntervalStats(FrozenModel):
    """Summarizes the gaps between spots.

    Attributes:
        mean: Mean gap.
        min: Smallest gap.
        max: Largest gap.
        std: Population standard deviation of the gaps.
        gaps: Every gap: consecutive pairs when ordered, nearest neighbour per spot otherwise.
    """

    mean: Finite
    min: Finite
    max: Finite
    std: Finite
    gaps: tuple[Finite, ...]


class FormMetrics(FrozenModel):
    """Summarizes the shape quality of a form's points, read as an ordered polyline.

    Attributes:
        length: Polyline length through the points in order.
        intervals: Consecutive gap statistics.
        curvature_range: Smallest and largest discrete curvature (1/m) at interior points.
        symmetry_error: Largest mirror mismatch about the best vertical axis.
    """

    length: Finite
    intervals: IntervalStats
    curvature_range: tuple[float, float]
    symmetry_error: Finite


class SpeedStats(FrozenModel):
    """Summarizes the movement demand of one spot over one transition.

    Attributes:
        distance: Meters travelled along the path.
        counts: Counts spent moving.
        m_per_count: Mean meters per count.
        m_per_s: Mean meters per second.
        max_instant: Largest instantaneous speed in meters per count.
    """

    distance: Finite
    counts: float
    m_per_count: float
    m_per_s: float
    max_instant: float


class FontGlyph(FrozenModel):
    """Holds the advance and stroke path data of one character.

    Attributes:
        advance: Horizontal distance to the next glyph, in font units.
        strokes: SVG path data per stroke, each one subpath, in drawing order.
    """

    advance: float = Field(ge=0)
    strokes: tuple[str, ...] = ()


class Font(FrozenModel):
    """Holds a stroke font's metrics and glyph table, in font units with y pointing up.

    Attributes:
        format: Font file format tag.
        name: Short lowercase name the font registers under.
        family: Human-readable family name.
        license: SPDX identifier of the font's licence.
        source: Where the font data came from.
        units_per_em: Font units in one em.
        cap_height: Height of capital letters, or None to measure it from `H`.
        ascender: Highest ink above the baseline.
        descender: Lowest ink relative to the baseline, negative below it.
        glyphs: Read-only glyph table keyed by one character.
    """

    format: Literal["showpy.font.v1"]
    name: str = Field(pattern=FONT_NAME_PATTERN, max_length=FONT_NAME_MAX_LENGTH)
    family: str
    license: str
    source: str
    units_per_em: float = Field(gt=0)
    cap_height: float | None = Field(default=None, gt=0)
    ascender: float
    descender: float
    glyphs: Annotated[Mapping[str, FontGlyph], ReadOnly()]

    @field_validator("glyphs")
    @classmethod
    def _check_glyph_keys(cls, glyphs: Mapping[str, FontGlyph]) -> Mapping[str, FontGlyph]:
        for key in glyphs:
            if len(key) != 1:
                raise ValueError(f"glyph key {key!r} must be exactly one character")
        return glyphs

    def cap(self) -> float:
        """Returns `cap_height`, else the ink height of `H`, else `ascender`.

        Returns:
            cap_height: Height of capital letters in font units.

        Examples:
            >>> from showpy.geo.fonts import bundled_fonts
            >>> bundled_fonts().get(bundled_fonts().resolve("simplex")).cap()
            6.0
        """
        if self.cap_height is not None:
            return self.cap_height
        glyph = self.glyphs.get(CAP_REFERENCE_GLYPH)
        if glyph is None:
            return self.ascender
        points = [p for d in glyph.strokes for sub in sample_stroke(d) for p in sub]
        if not points:
            return self.ascender
        heights = np.asarray(points)[:, 1]
        return float(heights.max() - heights.min())


class LaidGlyph(FrozenModel):
    """Holds one laid-out character: the font it came from, its line and its strokes.

    Attributes:
        char: The character after NFC normalization.
        font: URI of the font in the chain that drew it.
        line: Zero-based line index, top line first.
        strokes: Strokes in stage meters, in font order; empty for a space.
    """

    char: str = Field(min_length=1, max_length=1)
    font: str
    line: int = Field(ge=0)
    strokes: tuple[LaidStroke, ...] = ()
