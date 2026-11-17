"""
form_text.py --- turns a text recipe into exactly n slots on its glyph strokes

Slots follow reading order: lines top to bottom, glyphs in logical order, strokes in font
order, each stroke from its start. Every stroke gets at least its minimum spots, the rest are
shared by length, and no two slots ever sit closer than `COINCIDENT_TOL_M`.

Contains:
    STROKE_MINIMUM_OPEN: spots an open stroke needs to read as a line
    STROKE_MINIMUM_CLOSED: spots a closed stroke needs to read as a loop
    LEGIBLE_SPACING_SHARE: largest spot spacing that still reads, as a share of the cap height
    COINCIDENT_TOL_M: smallest distance allowed between two slots
    MAX_TEXT_SPOTS: most spots one text form may have
    text_recipe(): validates a text form's stored geometry into a TextRecipe
    text_strokes(): lays a recipe out and returns its visible strokes in reading order
    min_text_spots(): counts the spots the strokes need at least
    text_slots(): places exactly n slots on the strokes, with unit tangents
    require_member_count(): rejects a text form whose member count differs from its slot count
    reject_count_change(): rejects an edit of a text form that changes its count
    resolve_edit_fonts(): resolves the font names of a text edit to URIs
    require_edited_members(): rejects an edited text form whose member count left its slot count
"""

import math
import warnings
from collections.abc import Mapping, Sequence
from numbers import Integral
from types import MappingProxyType
from typing import Any, get_args

import numpy as np

from showpy.choreo.models import TextRecipe
from showpy.core.errors import (
    GeometryError,
    LegibilityWarning,
    SchemaError,
    SpacingError,
    bounded_repr,
    field_errors,
)
from showpy.core.types import Points
from showpy.geo.curve_base import Curve, FloatArray
from showpy.geo.curves import Polyline, SvgCurve
from showpy.geo.fonts import FontRegistry, bundled_fonts
from showpy.geo.models import LaidGlyph, TextAlign, TextAnchor
from showpy.geo.spacing import apportion
from showpy.geo.text_layout import MAX_LAYOUT_CHARS, layout_text

STROKE_MINIMUM_OPEN = 2
STROKE_MINIMUM_CLOSED = 3
# A vertical stroke reads as a line only with spots at its top, middle and bottom; a spacing
# above half the cap height loses the middle spot.
LEGIBLE_SPACING_SHARE = 0.5
COINCIDENT_TOL_M = 1e-6
MAX_TEXT_SPOTS = 10_000
SHARED_KEYS = frozenset({"n", "transform"})
NEIGHBOR_CELLS = tuple((dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
FIELD_HINTS: Mapping[str, str] = MappingProxyType(
    {
        "text": f"give text of 1 to {MAX_LAYOUT_CHARS} characters",
        "fonts": "give at least one font as an asset:sha256:<64 hex> URI; resolve a name with "
        "bundled_fonts().resolve(...)",
        "mode": "use mode centerline",
        "height": "give height as a finite cap height > 0 in meters",
        "at": "give at as a finite (x, y) point in meters",
        "rotation": "give rotation as finite degrees",
        "align": f"use align {', '.join(get_args(TextAlign.__value__))}",
        "anchor": f"use anchor {', '.join(get_args(TextAnchor.__value__))}",
        "letter_spacing": "give letter_spacing as a finite number of ems",
        "line_spacing": "give line_spacing as a finite number of ems > 0",
    }
)

type Cell = tuple[int, int]
type Stroke = Polyline | SvgCurve


class _SlotGrid:
    """Finds slots closer than `COINCIDENT_TOL_M` through a hash of tolerance-sized cells.

    Any two points closer than the tolerance sit in the same or adjacent cells, so each check
    reads nine cells and the whole placement stays linear in the slot count.

    Attributes:
        cells: Accepted slots keyed by the cell that holds them.
    """

    def __init__(self) -> None:
        self.cells: dict[Cell, list[list[float]]] = {}

    def fits(self, points: Points) -> bool:
        """Returns whether the points keep the tolerance from each other and every slot."""
        pending: dict[Cell, list[list[float]]] = {}
        for point in points.tolist():
            cell = _cell(point)
            if _crowded(point, cell, self.cells) or _crowded(point, cell, pending):
                return False
            pending.setdefault(cell, []).append(point)
        return True

    def add(self, points: Points) -> None:
        """Accepts the points as slots."""
        for point in points.tolist():
            self.cells.setdefault(_cell(point), []).append(point)


def _cell(point: list[float]) -> Cell:
    return math.floor(point[0] / COINCIDENT_TOL_M), math.floor(point[1] / COINCIDENT_TOL_M)


def _crowded(point: list[float], cell: Cell, cells: Mapping[Cell, list[list[float]]]) -> bool:
    return any(
        math.dist(point, other) < COINCIDENT_TOL_M
        for dx, dy in NEIGHBOR_CELLS
        for other in cells.get((cell[0] + dx, cell[1] + dy), ())
    )


def text_recipe(geometry: Mapping[str, Any]) -> TextRecipe:
    """Validates a text form's stored geometry into a TextRecipe, ignoring `n` and `transform`.

    Args:
        geometry: The form's JSON recipe; any key other than the recipe fields and the shared
            `n` and `transform` is an error.

    Returns:
        recipe: The validated recipe.

    Raises:
        SchemaError: `context["field"]` names the first failing field and the hint its allowed
            values.

    >>> from showpy.geo.fonts import bundled_fonts
    >>> simplex = bundled_fonts().resolve("simplex")
    >>> text_recipe({"text": "HI", "fonts": [simplex], "height": 2.0, "at": [0, 10], "n": 36}).at
    (0.0, 10.0)
    """
    fields = {key: value for key, value in geometry.items() if key not in SHARED_KEYS}
    try:
        return TextRecipe.model_validate(fields)
    except SchemaError as exc:
        errors = field_errors(exc)
        field = str(errors[0]["loc"]).split(".", 1)[0]
        allowed = ", ".join(TextRecipe.model_fields)
        hint = FIELD_HINTS.get(field, f"use only the keys {allowed}, n and transform")
        raise SchemaError(
            f"bad text recipe field {field}: {errors[0]['msg']}",
            hint=hint,
            context={"field": field, "errors": errors},
        ) from exc


def _laid_strokes(recipe: TextRecipe, font_registry: FontRegistry | None) -> list[LaidGlyph]:
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
    visible = [glyph for glyph in glyphs if glyph.strokes]
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

    >>> from showpy.geo.fonts import bundled_fonts
    >>> hi = TextRecipe(text="HI", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                 at=(0.0, 0.0))
    >>> len(text_strokes(hi))
    6
    """
    return [stroke for glyph in _laid_strokes(recipe, font_registry) for stroke in glyph.strokes]


def _stroke_minimum(stroke: Curve) -> int:
    return STROKE_MINIMUM_CLOSED if stroke.closed else STROKE_MINIMUM_OPEN


def min_text_spots(strokes: Sequence[Curve]) -> int:
    """Counts the spots the strokes need at least: 2 per open stroke, 3 per closed one.

    Args:
        strokes: Glyph strokes, as `text_strokes` returns them.

    Returns:
        min_n: The smallest n `text_slots` accepts for these strokes.

    >>> from showpy.geo.fonts import bundled_fonts
    >>> hi = TextRecipe(text="HI", fonts=(bundled_fonts().resolve("simplex"),), height=6.0,
    ...                 at=(0.0, 0.0))
    >>> min_text_spots(text_strokes(hi))
    12
    """
    return sum(_stroke_minimum(stroke) for stroke in strokes)


def _require_whole_count(n: int) -> None:
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
            code="TOO_MANY_SPOTS",
        )


def _require_count(n: int, min_n: int, text: str) -> None:
    if n < min_n:
        raise SpacingError(
            f"{n} spots cannot draw this text; its strokes need at least {min_n}",
            hint=f"use n >= {min_n} for this text, or shorten it",
            context={"n": n, "min_n": min_n, "text": bounded_repr(text)},
            code="TOO_FEW_SPOTS",
        )


def _candidate_stations(length: float, k: int, closed: bool) -> list[FloatArray]:
    """Returns arc-length stations to try for k spots, preferred spread first."""
    j = np.arange(k, dtype=float)
    if closed:
        return [j * length / k, (j + 0.5) * length / k]
    return [
        j * length / (k - 1),
        (j + 1) * length / k,
        j * length / k,
        (j + 1) * length / (k + 1),
        (j + 0.5) * length / k,
    ]


def _place_stroke(stroke: Stroke, k: int, grid: _SlotGrid, char: str) -> tuple[Points, Points]:
    """Places k slots on one stroke at the first candidate that keeps every slot apart.

    Args:
        stroke: Glyph stroke in stage meters.
        k: Spots this stroke gets.
        grid: Slots already placed; the chosen slots are added to it.
        char: Character the stroke draws, named in an error.

    Returns:
        slots: The stroke's slot points and their unit tangents.
    """
    for stations in _candidate_stations(stroke.length(), k, stroke.closed):
        points = stroke.point_at(stations)
        if grid.fits(points):
            grid.add(points)
            return points, stroke.tangent_at(stations)
    raise GeometryError(
        f"no spacing of {k} spots on a stroke of {char!r} keeps them apart",
        hint="change n or the text",
        context={"char": char, "spots": k, "tolerance": COINCIDENT_TOL_M},
        code="COINCIDENT_SLOTS",
    )


def _warn_if_sparse(strokes: Sequence[Stroke], counts: Sequence[int], height: float) -> None:
    """Warns, at the caller of `text_slots`, when a stroke's spots sit too far apart to read."""
    widest = max(
        stroke.length() / (k if stroke.closed else k - 1)
        for stroke, k in zip(strokes, counts, strict=True)
    )
    if widest > LEGIBLE_SPACING_SHARE * height:
        warnings.warn(
            f"W: text spots are up to {widest:.2f} m apart on {height:.2f} m letters; raise n",
            LegibilityWarning,
            stacklevel=3,
        )


def text_slots(
    recipe: TextRecipe, n: int, *, font_registry: FontRegistry | None = None
) -> tuple[Points, Points]:
    """Places exactly n slots on the recipe's strokes, in reading order, with unit tangents.

    Each stroke gets its minimum (2 open, 3 closed) and the rest by length. A stroke tries a
    few spreads in turn and takes the first that keeps every slot `COINCIDENT_TOL_M` from the
    others, so touching and crossing strokes never stack two spots. A `LegibilityWarning`
    says when the widest stroke spacing passes `LEGIBLE_SPACING_SHARE` of the cap height.

    Args:
        recipe: Text, fonts and layout to draw.
        n: Spots to place.
        font_registry: Registry holding the recipe's fonts; None means `bundled_fonts()`.

    Returns:
        slots: An (n, 2) array of points and an (n, 2) array of unit tangents, before any
            stored transform.

    Raises:
        SpacingError: `TOO_MANY_SPOTS` when n is over `MAX_TEXT_SPOTS`, checked before any
            layout; `TOO_FEW_SPOTS` when n is below `min_text_spots`.
        GeometryError: `COINCIDENT_SLOTS` when a stroke has no spread that keeps slots apart.

    >>> from showpy.geo.fonts import bundled_fonts
    >>> hi = TextRecipe(text="HI", fonts=(bundled_fonts().resolve("simplex"),), height=2.0,
    ...                 at=(0.0, 10.0))
    >>> points, tangents = text_slots(hi, 36)
    >>> points.shape, points[0].tolist()
    ((36, 2), [0.0, 10.0])
    """
    _require_whole_count(n)
    glyphs = _laid_strokes(recipe, font_registry)
    strokes = [stroke for glyph in glyphs for stroke in glyph.strokes]
    chars = [glyph.char for glyph in glyphs for _ in glyph.strokes]
    _require_count(n, min_text_spots(strokes), recipe.text)
    minimums = [_stroke_minimum(stroke) for stroke in strokes]
    counts = apportion([stroke.length() for stroke in strokes], n, minimums)
    grid = _SlotGrid()
    placed = [
        _place_stroke(stroke, k, grid, char)
        for stroke, k, char in zip(strokes, counts, chars, strict=True)
    ]
    _warn_if_sparse(strokes, counts, recipe.height)
    return np.vstack([points for points, _ in placed]), np.vstack([tan for _, tan in placed])


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
        code="MEMBER_COUNT_MISMATCH",
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
                code="TEXT_COUNT_EDIT",
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
            code="MEMBER_COUNT_MISMATCH",
        ) from exc
