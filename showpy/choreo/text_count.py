"""
text_count.py --- derives a text form's spot count from n or from a spacing between spots

An open stroke of length L at spacing s spans L/s gaps and holds L/s + 1 spots; a closed
stroke holds L/s. The derived count is `max(min_text_spots, floor(ΣL / s + open + 0.5))`, so
half rounds up, and it is checked against `MAX_TEXT_SPOTS` before any slot is placed.

Contains:
    spacing_count(): counts the spots that strokes hold at a spacing
    count_recipe(): returns a text recipe with its count set from exactly one of n and spacing
"""

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from showpy.choreo.form_text import (
    MAX_TEXT_SPOTS,
    min_text_spots,
    require_whole_count,
    stage_strokes,
    text_recipe,
    text_strokes,
)
from showpy.core.errors import SchemaError, SpacingError, bounded_repr
from showpy.core.types import Meters
from showpy.core.validation import require_finite
from showpy.geo.curve_base import Curve
from showpy.geo.fonts import FontRegistry

HALF = 0.5
HINT_DECIMALS = 1_000
SPLIT_HINT = "shorten the text or split it into several forms"


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

    >>> from showpy.choreo.models import TextRecipe
    >>> from showpy.geo.fonts import bundled_fonts
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
        code="TOO_MANY_SPOTS",
    )


# Any: recipes are JSON objects whose values depend on the key.
def count_recipe(
    geometry: Mapping[str, Any],
    n: int | None,
    spacing: Meters | None,
    font_registry: FontRegistry | None,
) -> dict[str, Any]:
    """Returns a text recipe with `n` set from exactly one of `n` and `spacing`.

    Args:
        geometry: The text form's JSON recipe.
        n: Exact spot count, or None to derive it from `spacing`.
        spacing: Meters between spots, or None when `n` is given.
        font_registry: Registry holding the recipe's fonts; None means `bundled_fonts()`.

    Returns:
        recipe: The geometry with `n` and `spacing` set; `spacing` is None for an exact count.
            A spacing is measured along the strokes after any stored `transform`, in stage
            meters.

    Raises:
        SchemaError: `N_OR_SPACING` unless exactly one is given; a bad spacing names the field.
        SpacingError: `n` is not a whole number from 1 to `MAX_TEXT_SPOTS`, or the derived
            count passes the cap.

    >>> from showpy.choreo.models import TextRecipe
    >>> from showpy.geo.fonts import bundled_fonts
    >>> ell = TextRecipe(text="L", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                  at=(0.0, 0.0))
    >>> count_recipe(ell.model_dump(mode="json"), None, 1.0, None)["n"]
    11
    """
    if n is not None and spacing is None:
        require_whole_count(n)
        return {**geometry, "spacing": None, "n": n}
    if spacing is not None and n is None:
        recipe = text_recipe({**geometry, "spacing": spacing})
        stored = geometry.get("transform")
        transform = None if stored is None else np.asarray(stored, dtype=float)
        strokes = stage_strokes(text_strokes(recipe, font_registry=font_registry), transform)
        return {**geometry, "spacing": recipe.spacing, "n": spacing_count(strokes, spacing)}
    raise SchemaError(
        "a text form takes exactly one of n and spacing",
        hint="pass n= for an exact spot count or spacing= for meters between spots, not both",
        context={"n": bounded_repr(n), "spacing": bounded_repr(spacing)},
        code="N_OR_SPACING",
    )
