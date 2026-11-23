"""
form_ops.py --- edits forms: reshape, reorder, split, merge, morph, gate, pinwheel, transform

Every function returns new forms and refuses locked forms unless force=True. Transforms
stay exact: curve recipes map through showpy.geo.affine.transform_curve, blocks under
rotation and uniform scale keep their recipe, custom points map directly, and other recipes
(or any recipe that already holds one) gain a composed `transform` matrix, so they stay
editable.

Contains:
    edit(): returns a form with construction or form fields changed
    reorder(): returns a form whose members sit in a new order along it
    split_form(): splits a form's members into two forms that keep their positions
    merge_forms(): joins two forms' members into one custom form
    morph_targets(): returns where each member of one form stands in another
    transform_form(): maps a form through an affine matrix exactly
    gate(): rotates a form about its first slot, last slot or a slot index
    pinwheel(): rotates several forms together about one center
    form_templates(): lists the form constructors by kind
"""

from collections.abc import Callable, Sequence
from typing import Any, Literal

import numpy as np

from showpy.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from showpy.choreo.form_layout import curve_stations, layout, slot_count
from showpy.choreo.form_models import FacingSpec, Form, Recipe
from showpy.choreo.form_text import (
    reject_count_change,
    require_edited_members,
    resolve_edit_fonts,
    too_few_for_edit,
)
from showpy.choreo.forms import (
    arc_form,
    block_form,
    check_unlocked,
    circle_form,
    curve_form,
    custom_form,
    fill_form,
    generated_form,
    line_form,
    polyline_form,
    scatter_form,
    slot_poses,
    slots,
    svg_form,
    text_form,
    wedge_form,
)
from showpy.choreo.match import match
from showpy.choreo.models import Set
from showpy.choreo.recipe_models import (
    BlockRecipe,
    CurveRecipeBase,
    CustomRecipe,
    RecipeBase,
    SvgRecipe,
)
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError, SpacingError
from showpy.core.ids import SpotId
from showpy.core.types import Degrees, Point, Points
from showpy.core.units import normalize_angle
from showpy.core.util import close_matches, natural_sort
from showpy.geo.affine import (
    Matrix,
    is_similarity,
    rotation_matrix,
    transform_curve,
    transform_facing,
    transform_points,
)
from showpy.geo.curves import TrimmedCurve, curve_from_construction
from showpy.geo.fonts import FontRegistry

FORM_FIELDS = frozenset({"members", "spacing", "facing", "locked", "meta", "set"})
FIXED_RECIPE_KEYS = frozenset({"kind", "recipe_version"})
TRIMMABLE_FACINGS = frozenset({"audience", "fixed", "along", "normal", "toward_point"})
HALF_TURN = 180.0
# Slot and spot coordinates come from sin and cos, whose last bits differ between platforms;
# ranking them to a nanometer keeps points on one file tied, so the other axis decides.
AXIS_RANK_DIGITS = 9

type Order = Sequence[str] | Literal["reverse", "by_x", "by_y", "nearest_prev"]


def _allowed_keys(recipe: Recipe) -> frozenset[str]:
    if isinstance(recipe, CurveRecipeBase):
        return frozenset((set(type(recipe.curve).model_fields) - {"kind"}) | {"n"})
    return frozenset(type(recipe).model_fields) - FIXED_RECIPE_KEYS


def _derived_count(recipe: RecipeBase, changes: dict[str, Any]) -> dict[str, int]:
    """Returns the slot count a block or custom edit implies, unless the caller set `n`."""
    if "n" in changes:
        return {}
    if isinstance(recipe, BlockRecipe) and {"rows", "cols"} & changes.keys():
        rows, cols = changes.get("rows", recipe.rows), changes.get("cols", recipe.cols)
        return {"n": rows * cols} if isinstance(rows, int) and isinstance(cols, int) else {}
    if isinstance(recipe, CustomRecipe) and "points" in changes:
        return {"n": len(changes["points"])}
    return {}


# Any: changes are construction values whose type depends on the key.
def _edited_recipe(form: Form, changes: dict[str, Any]) -> dict[str, Any]:
    recipe = form.recipe
    allowed = _allowed_keys(recipe)
    for key in changes:
        if key not in allowed:
            close = close_matches(key, sorted(allowed | FORM_FIELDS))
            raise SchemaError(
                f"{form.kind} form {form.id} has no field {key!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(allowed)}",
                context={"form": form.id, "field": key},
            )
    stored = recipe.model_dump()
    if not isinstance(recipe, CurveRecipeBase):
        return {**stored, **changes, **_derived_count(recipe, changes)}
    n = changes.pop("n", recipe.n)
    curve = curve_from_construction({**recipe.curve.construction(), **changes})
    return {**stored, "curve": curve, "n": n}


# Any: changes are construction values or form fields of any type.
def edit(
    form: Form,
    *,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    font_registry: FontRegistry | None = None,
    **changes: Any,
) -> Form:
    """Returns a form with construction fields (e.g. radius) or form fields changed.

    The result is validated by regenerating its slots when the slot count is known. A text
    form keeps its count: `n` and `spacing` raise `TEXT_COUNT_EDIT` (use `edit_text_count`),
    and `members` must keep the count. `fonts` names resolve through `font_registry`. Editing a
    block's `rows` or `cols`, or a custom form's `points`, moves its `n` to the new slot count
    unless `n` is passed; more members than `n` raises.

    >>> f = circle_form("c", ["A", "B"], (0, 5), 2.0)
    >>> edit(f, radius=3.0).recipe.curve.radius
    3.0
    """
    check_unlocked(form, force=force)
    if form.kind == "text":
        reject_count_change(form.id, changes)
        changes = resolve_edit_fonts(form.id, changes, font_registry)
    fields = {k: v for k, v in changes.items() if k in FORM_FIELDS}
    recipe = {k: v for k, v in changes.items() if k not in FORM_FIELDS}
    edited_recipe = _edited_recipe(form, recipe) if recipe else form.recipe
    if form.kind == "text" and form.recipe.n is not None and "members" in fields:
        require_edited_members(form.id, form.recipe.n, len(fields["members"]))
    dumped = form.model_dump()
    edited = Form.model_validate({**dumped, **fields, "recipe": edited_recipe})
    count = slot_count(edited)
    if edited.kind == "text" and count is not None:
        require_edited_members(edited.id, count, len(edited.members))
    if count is not None:
        _check_layout(edited, registry, font_registry)
    return edited


def _check_layout(form: Form, registry: FormFnRegistry, font_registry: FontRegistry | None) -> None:
    """Builds an edited form's slots; a text form short of spots names edit_text_count."""
    try:
        layout(form, registry, font_registry=font_registry)
    except SpacingError as exc:
        if form.kind == "text" and exc.code == ErrorCode.TOO_FEW_SPOTS:
            raise too_few_for_edit(exc) from exc
        raise


def _axis_rank(value: float) -> float:
    return round(float(value), AXIS_RANK_DIGITS)


def _ranked_slots(pts: Points, axis: int) -> list[int]:
    other = 1 - axis
    return sorted(
        range(len(pts)), key=lambda i: (_axis_rank(pts[i, axis]), _axis_rank(pts[i, other]), i)
    )


def _ranked_members(form: Form, axis: int, prev_set: Set | None) -> list[SpotId]:
    if prev_set is None:
        return natural_sort(form.spots)
    other = 1 - axis
    points = {m: prev_set.get(m).point for m in form.spots}
    return sorted(
        form.spots, key=lambda m: (_axis_rank(points[m][axis]), _axis_rank(points[m][other]), m)
    )


def _occupied_slots(form: Form) -> list[int]:
    return [index for index, member in enumerate(form.members) if member is not None]


def _occupied_points(
    form: Form, registry: FormFnRegistry, font_registry: FontRegistry | None
) -> Points:
    pts: Points = slots(form, registry, font_registry=font_registry)[_occupied_slots(form)]
    return pts


def _by_axis(
    form: Form,
    axis: int,
    prev_set: Set | None,
    registry: FormFnRegistry,
    font_registry: FontRegistry | None,
) -> list[SpotId]:
    pts = _occupied_points(form, registry, font_registry)
    ranked = zip(_ranked_slots(pts, axis), _ranked_members(form, axis, prev_set), strict=True)
    by_slot = dict(ranked)
    return [by_slot[slot] for slot in range(len(form.spots))]


def _nearest_prev(
    form: Form, prev_set: Set, registry: FormFnRegistry, font_registry: FontRegistry | None
) -> list[SpotId]:
    pts = _occupied_points(form, registry, font_registry)
    result = match(prev_set, pts, entities=form.spots)
    return sorted(form.spots, key=lambda m: result.assignment[m])


def _explicit(form: Form, order: Sequence[str]) -> list[SpotId]:
    if sorted(order) != sorted(form.spots):
        raise SchemaError(
            f"new order for form {form.id} is not a reordering of its members",
            hint="list every current member exactly once",
            context={"form": form.id, "members": list(form.spots)},
        )
    members: dict[str, SpotId] = {member: member for member in form.spots}
    return [members[spot_id] for spot_id in order]


def _on_occupied_slots(form: Form, spots: Sequence[SpotId]) -> tuple[SpotId | None, ...]:
    members = list(form.members)
    for index, spot in zip(_occupied_slots(form), spots, strict=True):
        members[index] = spot
    return tuple(members)


def reorder(
    form: Form,
    order: Order,
    prev_set: Set | None = None,
    *,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    font_registry: FontRegistry | None = None,
) -> Form:
    """Returns a form whose members sit in a new order along it.

    `order` is an explicit member list, "reverse", "by_x"/"by_y" (the leftmost or
    frontmost member in `prev_set`, or the first by ID without one, takes the leftmost or
    frontmost slot), or "nearest_prev" (optimal match from positions in `prev_set`). Open slots
    stay open: the spots move among the slots that hold one. `font_registry` holds a text form's
    fonts; None means `bundled_fonts()`.

    >>> reorder(line_form("f", ["A", "B", "C"], (0, 0), (2, 0)), "reverse").members
    ('C', 'B', 'A')
    """
    check_unlocked(form, force=force)
    if order == "reverse":
        spots = list(form.spots[::-1])
    elif order in ("by_x", "by_y"):
        spots = _by_axis(form, 0 if order == "by_x" else 1, prev_set, registry, font_registry)
    elif order == "nearest_prev":
        if prev_set is None:
            raise SchemaError("nearest_prev needs prev_set", hint="pass the previous set")
        spots = _nearest_prev(form, prev_set, registry, font_registry)
    else:
        spots = _explicit(form, order)
    return form.replace(members=_on_occupied_slots(form, spots))


def _split_index(form: Form, at: int | str) -> int:
    index = form.members.index(at) if isinstance(at, str) and at in form.members else at
    if not isinstance(index, int) or not 0 < index < len(form.members):
        raise SchemaError(
            f"cannot split form {form.id} at {at!r}",
            hint=f"give a member or an index in 1..{len(form.members) - 1}",
            context={"form": form.id, "at": at},
        )
    return index


def _custom_part(
    form: Form,
    part_id: str,
    bounds: tuple[int, int],
    registry: FormFnRegistry,
    font_registry: FontRegistry | None,
) -> Form:
    lo, hi = bounds
    pts, facings = slot_poses(form, registry, font_registry=font_registry)
    part = custom_form(part_id, form.members[lo:hi], pts[lo:hi], facings[lo:hi], set_id=form.set)
    return part.replace(meta=dict(form.meta))


def _trimmable(form: Form, lo: int, hi: int) -> bool:
    spacing = form.spacing
    plain = spacing.mode == "count" and not spacing.gaps
    return plain and hi - lo >= 2 and form.facing.mode in (TRIMMABLE_FACINGS)


def _trimmed_part(form: Form, recipe: CurveRecipeBase, part_id: str, lo: int, hi: int) -> Form:
    curve = recipe.curve
    stations = curve_stations(curve, form.spacing, slot_count(form))
    trimmed = TrimmedCurve(base=curve, s0=float(stations[lo]), s1=float(stations[hi - 1]))
    return form.replace(
        id=part_id,
        recipe=recipe.replace(curve=trimmed, n=None),
        members=form.members[lo:hi],
        spacing=form.spacing.replace(start=0.0, end=None),
    )


def split_form(
    form: Form,
    at: int | str,
    ids: tuple[str, str] | None = None,
    *,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    font_registry: FontRegistry | None = None,
) -> tuple[Form, Form]:
    """Splits members before index (or member) `at` into two forms; nobody moves.

    Evenly spaced curve forms split into trimmed pieces of the same curve; other forms,
    text forms included, split into custom forms holding the exact positions and facings. A
    text form's fonts resolve through `font_registry`; None means `bundled_fonts()`.

    >>> a, b = split_form(line_form("f", list("ABCD"), (0, 0), (3, 0)), "C")
    >>> a.members, slots(b).tolist()
    (('A', 'B'), [[2.0, 0.0], [3.0, 0.0]])
    """
    check_unlocked(form, force=force)
    index = _split_index(form, at)
    first_id, second_id = ids or (f"{form.id}-1", f"{form.id}-2")
    parts = []
    for part_id, lo, hi in ((first_id, 0, index), (second_id, index, len(form.members))):
        recipe = form.recipe
        parts.append(
            _trimmed_part(form, recipe, part_id, lo, hi)
            if isinstance(recipe, CurveRecipeBase) and _trimmable(form, lo, hi)
            else _custom_part(form, part_id, (lo, hi), registry, font_registry)
        )
    return parts[0], parts[1]


def merge_forms(
    a: Form,
    b: Form,
    form_id: str,
    *,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    font_registry: FontRegistry | None = None,
) -> Form:
    """Joins two forms of one set into a custom form: a's members, then b's; nobody moves.

    Text forms merge into a custom form too; their fonts resolve through `font_registry`, and
    None means `bundled_fonts()`.

    >>> merged = merge_forms(line_form("a", ["A"], (0, 0), (1, 0), set_id="1"),
    ...     line_form("b", ["B"], (5, 0), (6, 0), set_id="1"), "ab")
    >>> merged.members, slots(merged).tolist()
    (('A', 'B'), [[0.5, 0.0], [5.5, 0.0]])
    """
    check_unlocked(a, force=force)
    check_unlocked(b, force=force)
    if a.set != b.set:
        raise SchemaError(
            f"forms {a.id} and {b.id} belong to sets {a.set} and {b.set}",
            hint="merge forms of the same set",
        )
    pts_a, facings_a = slot_poses(a, registry, font_registry=font_registry)
    pts_b, facings_b = slot_poses(b, registry, font_registry=font_registry)
    na, nb = len(a.members), len(b.members)
    points = np.vstack([pts_a[:na], pts_b[:nb]])
    facings = [*facings_a[:na], *facings_b[:nb]]
    return custom_form(form_id, (*a.members, *b.members), points, facings, set_id=a.set)


def morph_targets(
    a: Form,
    b: Form,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
) -> Points:
    """Returns where each member of `a` (in a's order) stands in `b`; members must match.

    `font_registry` holds the fonts of a text form `b`; None means `bundled_fonts()`.

    >>> morph_targets(line_form("a", ["A", "B"], (0, 0), (1, 0)),
    ...     line_form("b", ["B", "A"], (0, 5), (1, 5))).tolist()
    [[1.0, 5.0], [0.0, 5.0]]
    """
    if set(a.spots) != set(b.spots):
        raise SchemaError(
            f"forms {a.id} and {b.id} have different members",
            hint="morph between forms holding the same spots",
            context={"only_a": sorted(set(a.spots) - set(b.spots))},
        )
    pts = slots(b, registry, font_registry=font_registry)
    index = {member: i for i, member in enumerate(b.members) if member is not None}
    return np.array([pts[index[m]] for m in a.spots], dtype=float).reshape(-1, 2)


def _mapped_facing(spec: FacingSpec, matrix: Matrix) -> FacingSpec:
    flips = bool(np.linalg.det(matrix[:2, :2]) < 0)
    if spec.mode == "fixed" and isinstance(spec.value, int | float):
        return spec.replace(value=transform_facing(float(spec.value), matrix))
    if spec.mode == "toward_point" and isinstance(spec.value, tuple):
        q = transform_points(np.asarray([spec.value], dtype=float), matrix)[0]
        return spec.replace(value=(float(q[0]), float(q[1])))
    if spec.mode == "normal" and flips:
        offset = float(spec.value) if isinstance(spec.value, int | float) else 0.0
        return spec.replace(value=normalize_angle(offset + HALF_TURN))
    return spec


def _proper_similarity(matrix: Matrix) -> bool:
    return is_similarity(matrix) and bool(np.linalg.det(matrix[:2, :2]) > 0)


def _mapped_block(recipe: BlockRecipe, matrix: Matrix) -> BlockRecipe:
    origin = transform_points(np.asarray([recipe.origin], dtype=float), matrix)[0]
    scale = float(np.sqrt(np.linalg.det(matrix[:2, :2])))
    return recipe.replace(
        origin=(float(origin[0]), float(origin[1])),
        interval=recipe.interval * scale,
        facing=transform_facing(recipe.facing, matrix),
    )


def _mapped_custom(recipe: CustomRecipe, matrix: Matrix) -> CustomRecipe:
    pts = transform_points(np.asarray(recipe.points, dtype=float).reshape(-1, 2), matrix)
    facings = recipe.facings
    mapped = None if facings is None else [transform_facing(f, matrix) for f in facings]
    return recipe.replace(points=[(float(x), float(y)) for x, y in pts], facings=mapped)


def _composed(recipe: RecipeBase, matrix: Matrix) -> RecipeBase:
    stored = np.eye(3) if recipe.transform is None else np.asarray(recipe.transform, dtype=float)
    return recipe.replace(transform=(matrix @ stored).tolist())


def _mapped_recipe(recipe: Recipe, matrix: Matrix) -> RecipeBase:
    if recipe.transform is not None:
        return _composed(recipe, matrix)
    if isinstance(recipe, CurveRecipeBase):
        return recipe.replace(curve=transform_curve(recipe.curve, matrix))
    if isinstance(recipe, SvgRecipe):
        return recipe.replace(curves=tuple(transform_curve(c, matrix) for c in recipe.curves))
    if isinstance(recipe, BlockRecipe) and _proper_similarity(matrix):
        return _mapped_block(recipe, matrix)
    if isinstance(recipe, CustomRecipe):
        return _mapped_custom(recipe, matrix)
    return _composed(recipe, matrix)


def transform_form(form: Form, matrix: Matrix, *, force: bool = False) -> Form:
    """Maps a form through a 3x3 affine matrix; slots and facings map exactly.

    >>> from showpy.geo.affine import translation_matrix
    >>> moved = transform_form(line_form("f", ["A", "B"], (0, 0), (2, 0)),
    ...     translation_matrix(0, 3))
    >>> slots(moved).tolist()
    [[0.0, 3.0], [2.0, 3.0]]
    """
    check_unlocked(form, force=force)
    return form.replace(
        recipe=_mapped_recipe(form.recipe, matrix), facing=_mapped_facing(form.facing, matrix)
    )


def _hinge_point(
    form: Form,
    hinge: Literal["start", "end"] | int,
    registry: FormFnRegistry,
    font_registry: FontRegistry | None,
) -> Point:
    pts = slots(form, registry, font_registry=font_registry)
    index = {"start": 0, "end": len(pts) - 1}.get(hinge, hinge) if isinstance(hinge, str) else hinge
    if not isinstance(index, int) or not 0 <= index < len(pts):
        raise SchemaError(
            f"bad hinge {hinge!r} for form {form.id}",
            hint=f'use "start", "end" or a slot index in 0..{len(pts) - 1}',
            context={"form": form.id},
        )
    return float(pts[index, 0]), float(pts[index, 1])


def gate(
    form: Form,
    hinge: Literal["start", "end"] | int,
    deg: Degrees,
    *,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    font_registry: FontRegistry | None = None,
) -> Form:
    """Rotates a form counter-clockwise by `deg` about its first slot, last slot or a slot.

    `font_registry` holds a text form's fonts; None means `bundled_fonts()`.

    >>> gated = gate(line_form("f", ["A", "B"], (0, 0), (2, 0)), "start", 90.0)
    >>> slots(gated).round(6).tolist()
    [[0.0, 0.0], [0.0, 2.0]]
    """
    pivot = _hinge_point(form, hinge, registry, font_registry)
    return transform_form(form, rotation_matrix(deg, pivot), force=force)


def pinwheel(
    forms: Sequence[Form], center: Point, deg: Degrees, *, force: bool = False
) -> list[Form]:
    """Rotates every form counter-clockwise by `deg` about one center, like spokes.

    >>> spokes = [line_form("a", ["A"], (1, 0), (2, 0)), line_form("b", ["B"], (-1, 0), (-2, 0))]
    >>> [(slots(f).round(6) + 0.0).tolist() for f in pinwheel(spokes, (0, 0), 90.0)]
    [[[0.0, 1.5]], [[0.0, -1.5]]]
    """
    matrix = rotation_matrix(deg, center)
    return [transform_form(form, matrix, force=force) for form in forms]


def form_templates() -> dict[str, Callable[..., Form]]:
    """Lists the form constructors by kind; packs may add prefabs later.

    >>> sorted(form_templates())[:3]
    ['arc', 'block', 'circle']
    """
    return {
        "line": line_form,
        "arc": arc_form,
        "curve": curve_form,
        "circle": circle_form,
        "block": block_form,
        "wedge": wedge_form,
        "polyline": polyline_form,
        "svg": svg_form,
        "fill": fill_form,
        "scatter": scatter_form,
        "custom": custom_form,
        "generated": generated_form,
        "text": text_form,
    }
