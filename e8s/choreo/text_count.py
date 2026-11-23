"""
text_count.py --- derives a text form's spot count from n or from a spacing between spots

An open stroke of length L at spacing s spans L/s gaps and holds L/s + 1 spots; a closed
stroke holds L/s. The derived count is `max(min_text_spots, floor(ΣL / s + open + 0.5))`, so
half rounds up, and it is checked against `MAX_TEXT_SPOTS` before any slot is placed.

Contains:
    spacing_count(): counts the spots that strokes hold at a spacing
    count_recipe(): returns a text recipe with its count set from exactly one of n and spacing
    text_spot_count(): counts the spots `text_form(spacing=...)` derives, before any members exist
"""

import math
from collections.abc import Sequence

import numpy as np

from e8s.choreo.form_models import TextRecipe
from e8s.choreo.form_text import (
    MAX_TEXT_SPOTS,
    min_text_spots,
    require_whole_count,
    stage_strokes,
    stored_count,
    text_recipe,
    text_strokes,
)
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, SpacingError, bounded_repr
from e8s.core.types import Degrees, Meters
from e8s.core.validation import require_finite
from e8s.geo.curve_base import Curve
from e8s.geo.fonts import FontRegistry, bundled_fonts
from e8s.geo.models import DEFAULT_LINE_SPACING, TextAlign, TextAnchor

HALF = 0.5
HINT_DECIMALS = 1_000
SPLIT_HINT = "shorten the text or split it into several forms"
ORIGIN = (0.0, 0.0)


def _requested(min_n: int, ideal: float) -> int | str:
    if not math.isfinite(ideal):
        return bounded_repr(ideal)
    return max(min_n, math.floor(ideal + HALF))


def _spacing_hint(total: float, open_strokes: int, min_n: int) -> str:
    if min_n > MAX_TEXT_SPOTS:
        return SPLIT_HINT
    least = math.ceil(total / (MAX_TEXT_SPOTS - open_strokes) * HINT_DECIMALS) / HINT_DECIMALS
    return f"use spacing >= {least:.3f} m"


def spacing_count(strokes: Sequence[Curve], spacing: Meters) -> int:
    """Counts the spots that strokes hold when spots sit `spacing` meters apart along them.

    Every stroke keeps its minimum (2 open, 3 closed), so a wide spacing clamps up to
    `min_text_spots`. The count is compared in floating point before it becomes an int, so a
    tiny spacing raises instead of overflowing.

    Args:
        strokes: Glyph strokes, as `text_strokes` returns them.
        spacing: Requested meters between neighbouring spots on a stroke.

    Returns:
        n: The derived spot count.

    Raises:
        SchemaError: `spacing` is not a finite number > 0.
        SpacingError: `TOO_MANY_SPOTS` when the count passes `MAX_TEXT_SPOTS`.

    >>> from e8s.choreo.form_models import TextRecipe
    >>> from e8s.geo.fonts import bundled_fonts
    >>> ell = TextRecipe(text="L", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                  at=(0.0, 0.0))
    >>> spacing_count(text_strokes(ell), 1.0)
    11
    """
    step = require_finite(spacing, name="spacing", low=0.0, low_open=True)
    total = math.fsum(stroke.length() for stroke in strokes)
    open_strokes = sum(1 for stroke in strokes if not stroke.closed)
    min_n = min_text_spots(strokes)
    ideal = total / step + open_strokes
    if min_n <= MAX_TEXT_SPOTS and ideal < MAX_TEXT_SPOTS + HALF:
        return max(min_n, math.floor(ideal + HALF))
    requested = _requested(min_n, ideal)
    raise SpacingError(
        f"spacing {step:g} m needs {bounded_repr(requested)} spots, over the cap of "
        f"{MAX_TEXT_SPOTS} for one text form",
        hint=_spacing_hint(total, open_strokes, min_n),
        context={"n": requested, "max_n": MAX_TEXT_SPOTS, "spacing": step},
        code=ErrorCode.TOO_MANY_SPOTS,
    )


def count_recipe(
    recipe: TextRecipe,
    n: int | None,
    spacing: Meters | None,
    font_registry: FontRegistry | None,
) -> TextRecipe:
    """Returns a text recipe with `n` set from exactly one of `n` and `spacing`.

    Args:
        recipe: The text form's recipe.
        n: Exact spot count, or None to derive it from `spacing`.
        spacing: Meters between spots, or None when `n` is given.
        font_registry: Registry holding the recipe's fonts; None means `bundled_fonts()`.

    Returns:
        counted: The recipe with `n` and `spacing` set; `spacing` is None for an exact count.
            A spacing is measured along the strokes after any stored `transform`, in stage
            meters.

    Raises:
        SchemaError: `N_OR_SPACING` unless exactly one is given; a bad spacing names the field.
        SpacingError: `n` is not a whole number from 1 to `MAX_TEXT_SPOTS`, or the derived
            count passes the cap.

    >>> from e8s.choreo.form_models import TextRecipe
    >>> from e8s.geo.fonts import bundled_fonts
    >>> ell = TextRecipe(text="L", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                  at=(0.0, 0.0))
    >>> count_recipe(ell, None, 1.0, None).n
    11
    """
    if n is not None and spacing is None:
        require_whole_count(n)
        return recipe.replace(spacing=None, n=n)
    if spacing is not None and n is None:
        layout = text_recipe({**recipe.model_dump(mode="json"), "spacing": spacing})
        stored = recipe.transform
        transform = None if stored is None else np.asarray(stored, dtype=float)
        strokes = stage_strokes(text_strokes(layout, font_registry=font_registry), transform)
        return recipe.replace(spacing=layout.spacing, n=spacing_count(strokes, spacing))
    raise SchemaError(
        "a text form takes exactly one of n and spacing",
        hint="pass n= for an exact spot count or spacing= for meters between spots, not both",
        context={"n": bounded_repr(n), "spacing": bounded_repr(spacing)},
        code=ErrorCode.N_OR_SPACING,
    )


def text_spot_count(
    text: str,
    *,
    fonts: Sequence[str],
    height: Meters,
    spacing: Meters,
    rotation: Degrees = 0.0,
    align: TextAlign = "left",
    anchor: TextAnchor = "baseline",
    letter_spacing: float = 0.0,
    line_spacing: float = DEFAULT_LINE_SPACING,
    font_registry: FontRegistry | None = None,
) -> int:
    r"""Counts the spots `text_form(spacing=...)` derives, so the caller can name its members.

    A text form needs exactly one member per spot; with `spacing` the count comes from the
    stroke lengths. The position `at` never changes the count, so it is not a parameter.

    Args:
        text: Characters to write, `\n` between lines.
        fonts: Font names or URIs, first choice first.
        height: Cap height in meters.
        spacing: Meters between spots along the strokes.
        rotation: Degrees counter-clockwise.
        align: Where each line sits horizontally.
        anchor: Which line of the text block sits vertically on the layout point.
        letter_spacing: Extra gap between glyphs, in ems of the first font.
        line_spacing: Baseline-to-baseline distance, in ems of the first font.
        font_registry: Registry that resolves the fonts; None means `bundled_fonts()`.

    Returns:
        n: The spot count, the same as the form's `recipe.n`.

    Raises:
        SchemaError: A bad layout field or spacing.
        SpacingError: `TOO_MANY_SPOTS` when the count passes `MAX_TEXT_SPOTS`.
        MissingFontError: A font is not in the registry.

    >>> text_spot_count("L", fonts=["simplex"], height=6.0, spacing=1.0)
    11
    """
    registry = bundled_fonts() if font_registry is None else font_registry
    recipe = TextRecipe(
        text=text,
        fonts=tuple(registry.resolve(font) for font in fonts),
        height=height,
        at=ORIGIN,
        rotation=rotation,
        align=align,
        anchor=anchor,
        letter_spacing=letter_spacing,
        line_spacing=line_spacing,
    )
    return stored_count("text", count_recipe(recipe, None, spacing, registry))
