"""
form_ops.py --- edits forms: reshape, reorder, split, merge, morph, gate, pinwheel, transform

Every function returns new forms and refuses locked forms unless force=True. Transforms
stay exact: curve recipes map through showpy.geo.affine.transform_curve, blocks under
rotation and uniform scale keep their recipe, and other point recipes gain a composed
`transform` matrix, so they stay editable.

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
from showpy.choreo.form_layout import CURVE_KINDS, curve_stations, layout, slot_count
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
from showpy.choreo.models import FacingSpec, Form, Set, TextRecipe
from showpy.core.errors import SchemaError, SpacingError
from showpy.core.ids import FormId, SpotId
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
SHARED_RECIPE_KEYS = frozenset({"n", "transform"})
RECIPE_KEYS = {
    "block": frozenset({"origin", "rows", "cols", "interval", "facing", "anchor"}),
    "fill": frozenset({"outline", "interval", "pattern", "angle_deg"}),
    "scatter": frozenset({"region", "min_gap", "seed"}),
    "custom": frozenset({"points", "facings"}),
    "generated": frozenset({"fn", "params"}),
    "text": frozenset(TextRecipe.model_fields),
}
TRIMMABLE_FACINGS = frozenset({"audience", "fixed", "along", "normal", "toward_point"})
HALF_TURN = 180.0

type Order = Sequence[SpotId] | Literal["reverse", "by_x", "by_y", "nearest_prev"]


def _allowed_keys(form: Form) -> frozenset[str]:
    if form.kind in CURVE_KINDS:
        curve_keys = set(form.geometry["curve"]) - {"kind"}
        return frozenset(curve_keys | {"n"})
    return RECIPE_KEYS[form.kind] | SHARED_RECIPE_KEYS


# Any: recipe values are JSON whose type depends on the key.
def _edited_geometry(form: Form, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = _allowed_keys(form)
    for key in changes:
        if key not in allowed:
            close = close_matches(key, sorted(allowed | FORM_FIELDS))
            raise SchemaError(
                f"{form.kind} form {form.id} has no field {key!r}",
                hint=f"did you mean {', '.join(close)}?" if close else f"use {sorted(allowed)}",
                context={"form": form.id, "field": key},
            )
    if form.kind not in CURVE_KINDS:
        return {**form.geometry, **changes}
    n = changes.pop("n", form.geometry.get("n"))
    curve = curve_from_construction({**form.geometry["curve"], **changes})
    recipe: dict[str, Any] = {**form.geometry, "curve": curve.construction()}
    return recipe if n is None else {**recipe, "n": n}


# Any: changes are construction values or form fields of any type.
def edit(
    form: Form,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
    **changes: Any,
) -> Form:
    """Returns a form with construction fields (e.g. radius) or form fields changed.

    The result is validated by regenerating its slots when the slot count is known. A text
    form keeps its count: `n` and `spacing` raise `TEXT_COUNT_EDIT` (use `edit_text_count`),
    and `members` must keep the count. `fonts` names resolve through `font_registry`.

    >>> f = circle_form("c", ["A", "B"], (0, 5), 2.0)
    >>> edit(f, radius=3.0).geometry["curve"]["radius"]
    3.0
    """
    check_unlocked(form, force)
    if form.kind == "text":
        reject_count_change(form.id, changes)
        changes = resolve_edit_fonts(form.id, changes, font_registry)
    fields = {k: v for k, v in changes.items() if k in FORM_FIELDS}
    recipe = {k: v for k, v in changes.items() if k not in FORM_FIELDS}
    geometry = _edited_geometry(form, recipe) if recipe else form.geometry
    dumped = form.model_dump()
    edited = Form.model_validate({**dumped, **fields, "geometry": geometry})
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
        if form.kind == "text" and exc.code == "TOO_FEW_SPOTS":
            raise too_few_for_edit(exc) from exc
        raise


def _ranked_slots(pts: Points, axis: int) -> list[int]:
    other = 1 - axis
    return sorted(range(len(pts)), key=lambda i: (pts[i, axis], pts[i, other], i))


def _ranked_members(form: Form, axis: int, prev_set: Set | None) -> list[SpotId]:
    if prev_set is None:
        return natural_sort(form.members)
    other = 1 - axis
    points = {m: prev_set.get(m).point for m in form.members}
    return sorted(form.members, key=lambda m: (points[m][axis], points[m][other], m))


def _by_axis(form: Form, axis: int, prev_set: Set | None, registry: FormFnRegistry) -> list[SpotId]:
    pts = slots(form, registry)[: len(form.members)]
    by_slot: list[SpotId] = [""] * len(form.members)
    ranked = zip(_ranked_slots(pts, axis), _ranked_members(form, axis, prev_set), strict=True)
    for slot, member in ranked:
        by_slot[slot] = member
    return by_slot


def _nearest_prev(form: Form, prev_set: Set, registry: FormFnRegistry) -> list[SpotId]:
    pts = slots(form, registry)[: len(form.members)]
    result = match(prev_set, pts, entities=form.members)
    return sorted(form.members, key=lambda m: result.assignment[m])


def _explicit(form: Form, order: Sequence[SpotId]) -> list[SpotId]:
    if sorted(order) != sorted(form.members):
        raise SchemaError(
            f"new order for form {form.id} is not a reordering of its members",
            hint="list every current member exactly once",
            context={"form": form.id, "members": list(form.members)},
        )
    return list(order)


def reorder(
    form: Form,
    order: Order,
    prev_set: Set | None = None,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
) -> Form:
    """Returns a form whose members sit in a new order along it.

    `order` is an explicit member list, "reverse", "by_x"/"by_y" (the leftmost or
    frontmost member in `prev_set`, or the first by ID without one, takes the leftmost or
    frontmost slot), or "nearest_prev" (optimal match from positions in `prev_set`).

    >>> reorder(line_form("f", ["A", "B", "C"], (0, 0), (2, 0)), "reverse").members
    ('C', 'B', 'A')
    """
    check_unlocked(form, force)
    if order == "reverse":
        members = list(form.members[::-1])
    elif order in ("by_x", "by_y"):
        members = _by_axis(form, 0 if order == "by_x" else 1, prev_set, registry)
    elif order == "nearest_prev":
        if prev_set is None:
            raise SchemaError("nearest_prev needs prev_set", hint="pass the previous set")
        members = _nearest_prev(form, prev_set, registry)
    else:
        members = _explicit(form, order)
    return form.replace(members=tuple(members))


def _split_index(form: Form, at: int | SpotId) -> int:
    index = form.members.index(at) if isinstance(at, str) and at in form.members else at
    if not isinstance(index, int) or not 0 < index < len(form.members):
        raise SchemaError(
            f"cannot split form {form.id} at {at!r}",
            hint=f"give a member or an index in 1..{len(form.members) - 1}",
            context={"form": form.id, "at": at},
        )
    return index


def _custom_part(form: Form, part_id: FormId, lo: int, hi: int, registry: FormFnRegistry) -> Form:
    pts, facings = slot_poses(form, registry)
    part = custom_form(part_id, form.members[lo:hi], pts[lo:hi], facings[lo:hi], set_id=form.set)
    return part.replace(meta=dict(form.meta))


def _trimmable(form: Form, lo: int, hi: int) -> bool:
    spacing = form.spacing
    plain = spacing.mode == "count" and not spacing.gaps
    return (
        form.kind in CURVE_KINDS
        and plain
        and hi - lo >= 2
        and form.facing.mode in (TRIMMABLE_FACINGS)
    )


def _trimmed_part(form: Form, part_id: FormId, lo: int, hi: int) -> Form:
    curve = curve_from_construction(form.geometry["curve"])
    stations = curve_stations(curve, form.spacing, slot_count(form))
    trimmed = TrimmedCurve(base=curve, s0=float(stations[lo]), s1=float(stations[hi - 1]))
    return form.replace(
        id=part_id,
        geometry={"curve": trimmed.construction()},
        members=form.members[lo:hi],
        spacing=form.spacing.replace(start=0.0, end=None),
    )


def split_form(
    form: Form,
    at: int | SpotId,
    ids: tuple[FormId, FormId] | None = None,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
) -> tuple[Form, Form]:
    """Splits members before index (or member) `at` into two forms; nobody moves.

    Evenly spaced curve forms split into trimmed pieces of the same curve; other forms
    split into custom forms holding the exact positions and facings.

    >>> a, b = split_form(line_form("f", list("ABCD"), (0, 0), (3, 0)), "C")
    >>> a.members, slots(b).tolist()
    (('A', 'B'), [[2.0, 0.0], [3.0, 0.0]])
    """
    check_unlocked(form, force)
    index = _split_index(form, at)
    first_id, second_id = ids or (f"{form.id}-1", f"{form.id}-2")
    parts = []
    for part_id, lo, hi in ((first_id, 0, index), (second_id, index, len(form.members))):
        trimmable = _trimmable(form, lo, hi)
        parts.append(
            _trimmed_part(form, part_id, lo, hi)
            if trimmable
            else _custom_part(form, part_id, lo, hi, registry)
        )
    return parts[0], parts[1]


def merge_forms(
    a: Form,
    b: Form,
    form_id: FormId,
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
) -> Form:
    """Joins two forms of one set into a custom form: a's members, then b's; nobody moves.

    >>> merged = merge_forms(line_form("a", ["A"], (0, 0), (1, 0), set_id="1"),
    ...     line_form("b", ["B"], (5, 0), (6, 0), set_id="1"), "ab")
    >>> merged.members, slots(merged).tolist()
    (('A', 'B'), [[0.5, 0.0], [5.5, 0.0]])
    """
    check_unlocked(a, force)
    check_unlocked(b, force)
    if a.set != b.set:
        raise SchemaError(
            f"forms {a.id} and {b.id} belong to sets {a.set} and {b.set}",
            hint="merge forms of the same set",
        )
    pts_a, facings_a = slot_poses(a, registry)
    pts_b, facings_b = slot_poses(b, registry)
    na, nb = len(a.members), len(b.members)
    points = np.vstack([pts_a[:na], pts_b[:nb]])
    facings = [*facings_a[:na], *facings_b[:nb]]
    return custom_form(form_id, (*a.members, *b.members), points, facings, set_id=a.set)


def morph_targets(a: Form, b: Form, registry: FormFnRegistry = DEFAULT_FORM_FNS) -> Points:
    """Returns where each member of `a` (in a's order) stands in `b`; members must match.

    >>> morph_targets(line_form("a", ["A", "B"], (0, 0), (1, 0)),
    ...     line_form("b", ["B", "A"], (0, 5), (1, 5))).tolist()
    [[1.0, 5.0], [0.0, 5.0]]
    """
    if set(a.members) != set(b.members):
        raise SchemaError(
            f"forms {a.id} and {b.id} have different members",
            hint="morph between forms holding the same spots",
            context={"only_a": sorted(set(a.members) - set(b.members))},
        )
    pts = slots(b, registry)
    index = {member: i for i, member in enumerate(b.members)}
    return np.array([pts[index[m]] for m in a.members], dtype=float).reshape(-1, 2)


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


# Any: recipes are JSON objects whose keys depend on the kind.
def _mapped_block(geometry: dict[str, Any], matrix: Matrix) -> dict[str, Any]:
    origin = transform_points(np.asarray([geometry["origin"]], dtype=float), matrix)[0]
    scale = float(np.sqrt(np.linalg.det(matrix[:2, :2])))
    return {
        **geometry,
        "origin": [float(origin[0]), float(origin[1])],
        "interval": float(geometry["interval"]) * scale,
        "facing": transform_facing(float(geometry.get("facing", 0.0)), matrix),
    }


# Any: recipes are JSON objects whose keys depend on the kind.
def _mapped_geometry(form: Form, matrix: Matrix) -> dict[str, Any]:
    g = form.geometry
    if form.kind in CURVE_KINDS:
        curve = transform_curve(curve_from_construction(g["curve"]), matrix)
        return {**g, "curve": curve.construction()}
    if form.kind == "block" and "transform" not in g and _proper_similarity(matrix):
        return _mapped_block(g, matrix)
    if form.kind == "custom":
        pts = transform_points(np.asarray(g["points"], dtype=float).reshape(-1, 2), matrix)
        facings = g.get("facings")
        mapped = None if facings is None else [transform_facing(float(f), matrix) for f in facings]
        return {**g, "points": pts.tolist(), "facings": mapped}
    composed = matrix @ np.asarray(g.get("transform", np.eye(3)), dtype=float)
    return {**g, "transform": composed.tolist()}


def transform_form(form: Form, matrix: Matrix, force: bool = False) -> Form:
    """Maps a form through a 3x3 affine matrix; slots and facings map exactly.

    >>> from showpy.geo.affine import translation_matrix
    >>> moved = transform_form(line_form("f", ["A", "B"], (0, 0), (2, 0)),
    ...     translation_matrix(0, 3))
    >>> slots(moved).tolist()
    [[0.0, 3.0], [2.0, 3.0]]
    """
    check_unlocked(form, force)
    return form.replace(
        geometry=_mapped_geometry(form, matrix), facing=_mapped_facing(form.facing, matrix)
    )


def _hinge_point(
    form: Form, hinge: Literal["start", "end"] | int, registry: FormFnRegistry
) -> Point:
    pts = slots(form, registry)
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
    force: bool = False,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
) -> Form:
    """Rotates a form counter-clockwise by `deg` about its first slot, last slot or a slot.

    >>> gated = gate(line_form("f", ["A", "B"], (0, 0), (2, 0)), "start", 90.0)
    >>> slots(gated).round(6).tolist()
    [[0.0, 0.0], [0.0, 2.0]]
    """
    return transform_form(form, rotation_matrix(deg, _hinge_point(form, hinge, registry)), force)


def pinwheel(forms: Sequence[Form], center: Point, deg: Degrees, force: bool = False) -> list[Form]:
    """Rotates every form counter-clockwise by `deg` about one center, like spokes.

    >>> spokes = [line_form("a", ["A"], (1, 0), (2, 0)), line_form("b", ["B"], (-1, 0), (-2, 0))]
    >>> [(slots(f).round(6) + 0.0).tolist() for f in pinwheel(spokes, (0, 0), 90.0)]
    [[[0.0, 1.5]], [[0.0, -1.5]]]
    """
    matrix = rotation_matrix(deg, center)
    return [transform_form(form, matrix, force) for form in forms]


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
