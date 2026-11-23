"""
form_text.py --- turns a text recipe into exactly n slots on its glyph strokes

Slots follow reading order: lines top to bottom, glyphs in logical order, strokes in font
order, each stroke from its start. Every stroke gets at least its minimum spots, the rest are
shared by length, and `e8s.choreo.text_spread` spreads them so touching strokes keep a
junction gap; `text_report` names every stroke that could not.

Contains:
    STROKE_MINIMUM_OPEN: spots an open stroke needs to read as a line
    STROKE_MINIMUM_CLOSED: spots a closed stroke needs to read as a loop
    LEGIBLE_SPACING_SHARE: largest spot spacing that still reads, as a share of the cap height
    MAX_TEXT_SPOTS: most spots one text form may have
    text_recipe(): validates a JSON text recipe into a TextRecipe, naming the failing field
    text_strokes(): lays a recipe out and returns its visible strokes in reading order
    stage_strokes(): maps strokes through a form's stored transform into stage meters
    min_text_spots(): counts the spots the strokes need at least
    require_whole_count(): rejects a spot count that is not a whole number up to the cap
    text_slots(): places exactly n slots on the strokes, with unit tangents
    text_report(): reports the strokes of a text form whose spots crowd another stroke's
    require_text_form(): rejects a form that is not a text form
    stored_count(): returns a text recipe's stored spot count, refusing a recipe without one
    require_member_count(): rejects a text form whose member count differs from its slot count
    reject_count_change(): rejects an edit of a text form that changes its count
    resolve_edit_fonts(): resolves the font names of a text edit to URIs
    require_edited_members(): rejects an edited text form whose member count left its slot count
    require_text_members(): rejects a text form whose members no longer fill its slots
    too_few_for_edit(): returns a TOO_FEW_SPOTS error whose hint names edit_text_count
"""

import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral
from types import MappingProxyType
from typing import Any, get_args

import numpy as np

from e8s.choreo.form_models import MAX_TEXT_FONTS, CrowdedStroke, Form, TextRecipe, TextReport
from e8s.choreo.text_spread import SlotSpreader, StrokeSpread, stroke_gap
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    LegibilityWarning,
    SchemaError,
    SpacingError,
    bounded_repr,
    field_errors,
)
from e8s.core.types import Points
from e8s.geo.affine import Matrix, transform_curve
from e8s.geo.curve_base import Curve
from e8s.geo.curves import Polyline, SvgCurve
from e8s.geo.fonts import FontRegistry, bundled_fonts
from e8s.geo.models import LaidGlyph, TextAlign, TextAnchor
from e8s.geo.spacing import apportion
from e8s.geo.text_layout import MAX_LAYOUT_CHARS, layout_text

STROKE_MINIMUM_OPEN = 2
STROKE_MINIMUM_CLOSED = 3
# A vertical stroke reads as a line only with spots at its top, middle and bottom; a spacing
# above half the cap height loses the middle spot.
LEGIBLE_SPACING_SHARE = 0.5
MAX_TEXT_SPOTS = 10_000
SHARED_KEYS = frozenset({"n", "transform"})
FIELD_HINTS: Mapping[str, str] = MappingProxyType(
    {
        "text": f"give text of 1 to {MAX_LAYOUT_CHARS} characters",
        "fonts": f"give 1 to {MAX_TEXT_FONTS} fonts as asset:sha256:<64 hex> URIs; resolve a "
        "name with bundled_fonts().resolve(...)",
        "mode": "use mode centerline",
        "height": "give height as a finite cap height > 0 in meters",
        "at": "give at as a finite (x, y) point in meters",
        "rotation": "give rotation as finite degrees",
        "align": f"use align {', '.join(get_args(TextAlign.__value__))}",
        "anchor": f"use anchor {', '.join(get_args(TextAnchor.__value__))}",
        "letter_spacing": "give letter_spacing as a finite number of ems",
        "line_spacing": "give line_spacing as a finite number of ems > 0",
        "spacing": "give spacing as a finite distance > 0 in meters, or null",
    }
)

type Stroke = Polyline | SvgCurve


@dataclass(frozen=True)
class _StrokePlace:
    char: str
    line: int
    column: int
    stroke: int


def text_recipe(geometry: Mapping[str, Any]) -> TextRecipe:
    """Validates a JSON text recipe into a TextRecipe of its layout, dropping `n` and `transform`.

    Args:
        geometry: A JSON text recipe; any key other than the recipe fields is an error.

    Returns:
        recipe: The validated recipe.

    Raises:
        SchemaError: `context["field"]` names the first failing field and the hint its allowed
            values.

    >>> from e8s.geo.fonts import bundled_fonts
    >>> simplex = bundled_fonts().resolve("simplex")
    >>> text_recipe({"text": "HI", "fonts": [simplex], "height": 2.0, "at": [0, 10], "n": 36}).at
    (0.0, 10.0)
    """
    fields = {key: value for key, value in geometry.items() if key not in SHARED_KEYS}
    try:
        return TextRecipe._validated(fields)
    except SchemaError as exc:
        errors = field_errors(exc)
        field = str(errors[0]["loc"]).split(".", 1)[0]
        allowed = ", ".join(TextRecipe._field_names())
        hint = FIELD_HINTS.get(field, f"use only the keys {allowed}")
        raise SchemaError(
            f"bad text recipe field {field}: {errors[0]['msg']}",
            hint=hint,
            context={"field": field, "errors": errors},
        ) from exc


def _laid_strokes(
    recipe: TextRecipe, font_registry: FontRegistry | None
) -> list[tuple[LaidGlyph, int]]:
    """Lays the recipe out and returns each visible glyph with its column in its line."""
    glyphs = layout_text(
        recipe.text,
        recipe.fonts,
        recipe.height,
        recipe.at,
        rotation=recipe.rotation,
        align=recipe.align,
        anchor=recipe.anchor,
        letter_spacing=recipe.letter_spacing,
        line_spacing=recipe.line_spacing,
        font_registry=font_registry,
    )
    columns = [sum(1 for other in glyphs[:i] if other.line == glyph.line) for i, glyph in
               enumerate(glyphs)]  # fmt: skip
    visible = [(glyph, column) for glyph, column in zip(glyphs, columns, strict=True)
               if glyph.strokes]  # fmt: skip
    if not visible:
        raise SchemaError(
            f"text {bounded_repr(recipe.text)!r} has no visible strokes",
            hint="give text with at least one visible character",
            context={"text": bounded_repr(recipe.text)},
        )
    return visible


def text_strokes(recipe: TextRecipe, *, font_registry: FontRegistry | None = None) -> list[Stroke]:
    """Lays a recipe out and returns its visible strokes in reading order.

    Args:
        recipe: Text, fonts and layout to draw.
        font_registry: Registry holding the recipe's fonts; None means `bundled_fonts()`.

    Returns:
        strokes: Every glyph stroke in stage meters, lines top to bottom, glyphs in logical
            order, strokes in font order.

    Raises:
        SchemaError: The text has no visible character.

    >>> from e8s.geo.fonts import bundled_fonts
    >>> hi = TextRecipe(text="HI", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                 at=(0.0, 0.0))
    >>> len(text_strokes(hi))
    6
    """
    laid = _laid_strokes(recipe, font_registry)
    return [stroke for glyph, _ in laid for stroke in glyph.strokes]


def stage_strokes(strokes: Sequence[Stroke], transform: Matrix | None) -> list[Curve]:
    """Maps strokes through a form's stored transform, so their lengths are stage meters.

    Args:
        strokes: Glyph strokes, as `text_strokes` returns them.
        transform: The form's stored 3x3 affine matrix, or None for none.

    Returns:
        mapped: The strokes after the transform; the same strokes when there is none.

    >>> from e8s.geo.affine import scale_matrix
    >>> from e8s.geo.fonts import bundled_fonts
    >>> ell = TextRecipe(text="L", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                  at=(0.0, 0.0))
    >>> [stroke.length() for stroke in stage_strokes(text_strokes(ell), scale_matrix(2, 2, (0, 0)))]
    [20.0]
    """
    if transform is None:
        return list(strokes)
    return [transform_curve(stroke, transform) for stroke in strokes]


def _stroke_minimum(stroke: Curve) -> int:
    return STROKE_MINIMUM_CLOSED if stroke.closed else STROKE_MINIMUM_OPEN


def min_text_spots(strokes: Sequence[Curve]) -> int:
    """Counts the spots the strokes need at least: 2 per open stroke, 3 per closed one.

    Args:
        strokes: Glyph strokes, as `text_strokes` returns them.

    Returns:
        min_n: The smallest n `text_slots` accepts for these strokes.

    >>> from e8s.geo.fonts import bundled_fonts
    >>> hi = TextRecipe(text="HI", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                 at=(0.0, 0.0))
    >>> min_text_spots(text_strokes(hi))
    12
    """
    return sum(_stroke_minimum(stroke) for stroke in strokes)


def require_whole_count(n: int) -> None:
    """Rejects a text spot count that is not a whole number from 1 to `MAX_TEXT_SPOTS`.

    Raises:
        SpacingError: `TOO_MANY_SPOTS` above the cap; no code for a non-int or n < 1.
    """
    if isinstance(n, bool) or not isinstance(n, Integral) or n < 1:
        raise SpacingError(
            "n must be a whole number of at least 1",
            hint="pass n as an int >= 1",
            context={"n": bounded_repr(n)},
        )
    if n > MAX_TEXT_SPOTS:
        raise SpacingError(
            f"{n} spots is over the cap of {MAX_TEXT_SPOTS} for one text form",
            hint=f"use n <= {MAX_TEXT_SPOTS} or split the text into several forms",
            context={"n": n, "max_n": MAX_TEXT_SPOTS},
            code=ErrorCode.TOO_MANY_SPOTS,
        )


def _require_count(n: int, min_n: int, text: str) -> None:
    if n < min_n:
        raise SpacingError(
            f"{n} spots cannot draw this text; its strokes need at least {min_n}",
            hint=f"use n >= {min_n} for this text, or shorten it",
            context={"n": n, "min_n": min_n, "text": bounded_repr(text)},
            code=ErrorCode.TOO_FEW_SPOTS,
        )


def _warn_if_sparse(
    strokes: Sequence[Stroke], counts: Sequence[int], height: float, transform: Matrix | None
) -> None:
    """Warns, at the caller of `text_slots`, when a stroke's spots sit too far apart to read.

    Spacing and letter height are measured after the stored transform; the height scales by
    the square root of the transform's area factor.
    """
    mapped = stage_strokes(strokes, transform)
    if transform is not None:
        height *= math.sqrt(abs(float(np.linalg.det(transform[:2, :2]))))
    widest = max(
        stroke.length() / (k if stroke.closed else k - 1)
        for stroke, k in zip(mapped, counts, strict=True)
    )
    if widest > LEGIBLE_SPACING_SHARE * height:
        warnings.warn(
            f"W: text spots are up to {widest:.2f} m apart on {height:.2f} m letters; raise n, "
            "or add spots with edit_text_count",
            LegibilityWarning,
            stacklevel=3,
        )


def text_slots(
    recipe: TextRecipe,
    n: int,
    *,
    font_registry: FontRegistry | None = None,
    transform: Matrix | None = None,
) -> tuple[Points, Points]:
    """Places exactly n slots on the recipe's strokes, in reading order, with unit tangents.

    Each stroke gets its minimum (2 open, 3 closed) and the rest by length. A stroke tries
    spreads in turn and takes the first that keeps its spots a junction gap from other slots;
    where none does it takes the least crowded one and `text_report` lists it. Two spots never
    share a point. A `LegibilityWarning` says when the widest stroke spacing passes
    `LEGIBLE_SPACING_SHARE` of the cap height.

    Args:
        recipe: Text, fonts and layout to draw.
        n: Spots to place.
        font_registry: Registry holding the recipe's fonts; None means `bundled_fonts()`.
        transform: The form's stored transform; only the legibility check uses it, so the
            warning names stage meters.

    Returns:
        slots: An (n, 2) array of points and an (n, 2) array of unit tangents, before any
            stored transform.

    Raises:
        SpacingError: `TOO_MANY_SPOTS` when n is over `MAX_TEXT_SPOTS`, checked before any
            layout; `TOO_FEW_SPOTS` when n is below `min_text_spots`.
        GeometryError: `COINCIDENT_SLOTS` when every spread of a stroke puts a spot within
            `COINCIDENT_TOL_M` of another slot.

    >>> from e8s.geo.fonts import bundled_fonts
    >>> hi = TextRecipe(text="HI", fonts=(bundled_fonts().resolve("simplex"),), height=2.0,
    ...                 at=(0.0, 10.0))
    >>> points, tangents = text_slots(hi, 36)
    >>> points.shape, points[0].tolist()
    ((36, 2), [0.0, 10.0])
    """
    require_whole_count(n)
    strokes, counts, spreads, _ = _spread(recipe, n, font_registry)
    _warn_if_sparse(strokes, counts, recipe.height, transform)
    return (
        np.vstack([spread.points for spread in spreads]),
        np.vstack([spread.tangents for spread in spreads]),
    )


def _spread(
    recipe: TextRecipe, n: int, font_registry: FontRegistry | None
) -> tuple[list[Stroke], list[int], list[StrokeSpread], list[_StrokePlace]]:
    """Shares n spots over the recipe's strokes and spreads each stroke's spots.

    Returns:
        spread: The strokes, their spot counts, their spreads and where each stroke sits in
            the text.
    """
    laid = _laid_strokes(recipe, font_registry)
    strokes = [stroke for glyph, _ in laid for stroke in glyph.strokes]
    places = [
        _StrokePlace(glyph.char, glyph.line, column, index)
        for glyph, column in laid
        for index in range(len(glyph.strokes))
    ]
    _require_count(n, min_text_spots(strokes), recipe.text)
    minimums = [_stroke_minimum(stroke) for stroke in strokes]
    counts = apportion([stroke.length() for stroke in strokes], n, minimums)
    spreader = SlotSpreader(max(stroke_gap(s, k) for s, k in zip(strokes, counts, strict=True)))
    spreads = [
        spreader.place(stroke, k, place.char)
        for stroke, k, place in zip(strokes, counts, places, strict=True)
    ]
    return strokes, counts, spreads, places


def text_report(form: Form, *, font_registry: FontRegistry | None = None) -> TextReport:
    """Reports the strokes of a text form whose spots sit closer than their junction gap.

    A stroke keeps `JUNCTION_GAP_SHARE` of its spot spacing from other strokes' spots where any
    spread allows it. Doubled or converging strokes, as in several Hershey glyphs, may leave no
    such spread; the stroke then takes the spread whose closest spot is farthest away and is
    listed here, so no fallback goes unseen. Distances are before any stored transform.

    Args:
        form: A text form.
        font_registry: Registry holding the form's fonts; None means `bundled_fonts()`.

    Returns:
        report: Every crowded stroke in slot order; empty when every junction gap holds.

    Raises:
        SchemaError: `NOT_A_TEXT_FORM` for another kind.

    >>> from e8s.choreo.forms import text_form
    >>> go = text_form("go", [f"S{i}" for i in range(24)], "GO", fonts=["hershey-roman"],
    ...     height=4.0, at=(0, 10), n=24)
    >>> text_report(go).crowded
    ()
    """
    recipe = require_text_form(form)
    n = stored_count(form.id, recipe)
    _, _, spreads, places = _spread(recipe, n, font_registry)
    starts = np.cumsum([0, *(len(spread.points) for spread in spreads)]).tolist()
    return TextReport(
        crowded=tuple(
            CrowdedStroke(
                char=place.char,
                line=place.line,
                column=place.column,
                stroke=place.stroke,
                first_slot=first,
                spots=len(spread.points),
                gap=spread.nearest,
                wanted=spread.gap,
            )
            for spread, place, first in zip(spreads, places, starts[:-1], strict=True)
            if spread.is_crowded
        )
    )


def require_text_form(form: Form) -> TextRecipe:
    """Rejects a form that is not a text form.

    Returns:
        recipe: The form's text recipe.

    Raises:
        SchemaError: `NOT_A_TEXT_FORM`; the hint points other kinds at `edit()`.
    """
    if not isinstance(form.recipe, TextRecipe):
        raise SchemaError(
            f"form {form.id} is a {form.kind} form, not text",
            hint="use edit() for other kinds",
            context={"form": form.id, "kind": form.kind},
            code=ErrorCode.NOT_A_TEXT_FORM,
        )
    return form.recipe


def stored_count(form_id: str, recipe: TextRecipe) -> int:
    """Returns a text recipe's stored spot count, refusing a recipe without one.

    Args:
        form_id: ID of the form, named in the error.
        recipe: The form's text recipe.

    Returns:
        n: The exact spot count.

    Raises:
        SchemaError: The recipe stores no count; the hint names `text_form`.
    """
    if recipe.n is None:
        raise SchemaError(
            f"text form {form_id} stores no spot count",
            hint="rebuild it with text_form(...)",
            context={"form": form_id},
        )
    return recipe.n


def require_member_count(form_id: str, n: int, member_count: int) -> None:
    """Rejects a text form whose member count differs from its slot count.

    A text form never has open slots: member i stands on slot i, and every slot has a member.

    Args:
        form_id: ID of the form, named in the error.
        n: Slots the form has.
        member_count: Members the form lists.

    Raises:
        SpacingError: `MEMBER_COUNT_MISMATCH`; the hint says how many spots to add or remove.

    >>> require_member_count("go", 24, 24)
    """
    if member_count == n:
        return
    gap = abs(n - member_count)
    hint = f"add {gap} more spots" if member_count < n else f"remove {gap} spots"
    raise SpacingError(
        f"text form {form_id} has {member_count} members for {n} slots",
        hint=hint,
        context={"form": form_id, "n": n, "members": member_count},
        code=ErrorCode.MEMBER_COUNT_MISMATCH,
    )


_EDIT_TEXT_COUNT_HINT = (
    "use edit_text_count(form, add=[...], remove=[...], n=...) to change the count"
)
_COUNT_FIELDS = ("n", "spacing")


def reject_count_change(form_id: str, changes: Mapping[str, Any]) -> None:
    """Rejects an edit of a text form that sets `n` or `spacing`.

    Args:
        form_id: ID of the form, named in the error.
        changes: The edit's field changes.

    Raises:
        SchemaError: `TEXT_COUNT_EDIT`; the hint names `edit_text_count`.
    """
    for field in _COUNT_FIELDS:
        if field in changes:
            raise SchemaError(
                f"text form {form_id} cannot change {field} through edit",
                hint=_EDIT_TEXT_COUNT_HINT,
                context={"form": form_id, "field": field},
                code=ErrorCode.TEXT_COUNT_EDIT,
            )


def resolve_edit_fonts(
    form_id: str, changes: Mapping[str, Any], font_registry: FontRegistry | None
) -> dict[str, Any]:
    """Returns the changes with any `fonts` entry resolved to font URIs.

    Args:
        form_id: ID of the form, named in the error.
        changes: The edit's recipe changes.
        font_registry: Registry that resolves the names; None means `bundled_fonts()`.

    Returns:
        resolved: The changes; `fonts` holds a list of URIs when it was given.

    Raises:
        SchemaError: `fonts` is a string or not a list or tuple.
    """
    if "fonts" not in changes:
        return dict(changes)
    fonts = changes["fonts"]
    if not isinstance(fonts, list | tuple):
        raise SchemaError(
            f"text form {form_id} needs fonts as a list, got {type(fonts).__name__}",
            hint="pass a list of font names or URIs",
            context={"form": form_id, "field": "fonts"},
        )
    registry = bundled_fonts() if font_registry is None else font_registry
    return {**changes, "fonts": [registry.resolve(font) for font in fonts]}


def require_edited_members(form_id: str, n: int, member_count: int) -> None:
    """Rejects an edited text form whose member count no longer matches its slot count.

    Args:
        form_id: ID of the form, named in the error.
        n: Slots the form has.
        member_count: Members the edited form lists.

    Raises:
        SpacingError: `MEMBER_COUNT_MISMATCH`; the hint also names `edit_text_count`.
    """
    try:
        require_member_count(form_id, n, member_count)
    except SpacingError as exc:
        raise SpacingError(
            exc.message,
            hint=f"{exc.hint}; {_EDIT_TEXT_COUNT_HINT}",
            context=exc.context,
            code=ErrorCode.MEMBER_COUNT_MISMATCH,
        ) from exc


def require_text_members(form: Form) -> None:
    """Rejects a text form whose member count differs from its stored count; other kinds pass.

    `delete_spots` may leave a text form short; regenerating its placements then fails
    closed until `edit_text_count` restores one member per slot.

    Args:
        form: Any form; only a text form with a stored `n` is checked.

    Raises:
        SpacingError: `MEMBER_COUNT_MISMATCH`; the hint names `edit_text_count`.

    >>> from e8s.choreo.forms import text_form
    >>> go = text_form("go", [f"S{i}" for i in range(24)], "GO", fonts=["hershey-roman"],
    ...     height=4.0, at=(0, 10), n=24)
    >>> require_text_members(go)
    """
    recipe = form.recipe
    if isinstance(recipe, TextRecipe) and recipe.n is not None:
        require_edited_members(form.id, recipe.n, len(form.members))


def too_few_for_edit(exc: SpacingError) -> SpacingError:
    """Returns a TOO_FEW_SPOTS error whose hint says how many spots `edit_text_count` must add.

    `edit` cannot change a text form's count, so its hint names the call that can.

    Args:
        exc: The `TOO_FEW_SPOTS` error `text_slots` raised, with `n` and `min_n` in context.

    Returns:
        error: The same error with the edit hint, or `exc` itself when its context lacks the
            two counts.
    """
    n, min_n = exc.context.get("n"), exc.context.get("min_n")
    if not isinstance(n, int) or not isinstance(min_n, int):
        return exc
    return SpacingError(
        exc.message,
        hint=f"add {min_n - n} spots with edit_text_count(form, add=[...], n={min_n}), "
        "or shorten the text",
        context=exc.context,
        code=ErrorCode.TOO_FEW_SPOTS,
    )
