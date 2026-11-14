"""
forms.py --- form constructors plus slot and placement generation from stored recipes

A form stores how to rebuild its shape, never the dots. `Form.geometry` is a JSON recipe
whose keys depend on `Form.kind`:

    line, arc, curve, circle, wedge, polyline, svg:
        {"curve": <curve construction() dict>}; rebuilt by curve_from_construction()
    block:     {"origin", "rows", "cols", "interval", "facing", "anchor"}
    fill:      {"outline": <Region dict>, "interval", "pattern", "angle_deg"}
    scatter:   {"region": <Region dict>, "min_gap", "seed"}
    custom:    {"points": [[x, y], ...], "facings": [deg, ...] | null}
    generated: {"fn": <registered name>, "params": {...}}

Every recipe may also hold `"n"`, the slot count; without it a form has one slot per member
(interval spacing places as many as fit, a block has rows x cols, a custom form one per
point, a fill every lattice point). Non-curve recipes may hold `"transform"`, a 3x3 affine
matrix applied to the generated points, which keeps gated or mirrored forms editable.
Members fill slots in order: member i stands on slot i; slots past the last member are
open for `place` to fill.

Contains:
    check_unlocked(): raises LockedError when a form is locked and force is not given
    line_form(): builds a straight-line form
    arc_form(): builds an arc form
    curve_form(): builds a smooth curve form through points
    circle_form(): builds a circle form, facing outward by default
    block_form(): builds a rectangular block form
    wedge_form(): builds a V-shaped wedge form
    polyline_form(): builds a form with straight sides and corners
    svg_form(): builds a form on a picture outline
    fill_form(): builds a form that fills an area with a lattice
    scatter_form(): builds an organic scatter form
    custom_form(): builds a form of frozen hand-placed spots
    generated_form(): builds a form from a registered shape generator
    edit_params(): returns a generated form with changed generator parameters
    slots(): returns a form's slot positions
    slot_poses(): returns every slot's position and facing
    placements(): regenerates placements for a form's members
"""

from collections.abc import Sequence
from typing import Any

import shapely

from showpy.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from showpy.choreo.form_layout import layout, member_facings, slot_count
from showpy.choreo.models import FacingSpec, Form, FormKind, Placement, SpacingSpec
from showpy.core.errors import LockedError, SchemaError, SpacingError
from showpy.core.ids import FormId, SetId, SpotId
from showpy.core.types import Box2, Degrees, Meters, Point, Points, Region
from showpy.core.util import as_points
from showpy.geo.curve_base import Curve
from showpy.geo.curves import AnyCurve, CompositeCurve
from showpy.geo.regions import region
from showpy.geo.shapes import arc, circle, curve_through, line, polyline, svg_outline, wedge
from showpy.geo.spacing import BlockAnchor, FillPattern

PROPORTIONAL = SpacingSpec(mode="proportional")
OUTWARD = FacingSpec(mode="outward")


def check_unlocked(form: Form, force: bool) -> None:
    """Raises LockedError when the form is locked and the caller did not pass force=True.

    >>> check_unlocked(Form(id="f", set="1", kind="custom", geometry={}, members=()), False)
    """
    if form.locked and not force:
        raise LockedError(
            f"form {form.id} is locked",
            hint="unlock it first, or pass force=True as the designer",
            context={"form": form.id, "set": form.set},
        )


# Any: recipes are JSON objects whose keys depend on the kind.
def _form(
    form_id: FormId,
    kind: FormKind,
    geometry: dict[str, Any],
    members: Sequence[SpotId],
    spacing: SpacingSpec | None,
    facing: FacingSpec | None,
    set_id: SetId,
    n: int | None,
) -> Form:
    recipe = geometry if n is None else {**geometry, "n": n}
    return Form(
        id=form_id,
        set=set_id,
        kind=kind,
        geometry=recipe,
        members=tuple(members),
        spacing=spacing or SpacingSpec(),
        facing=facing or FacingSpec(),
    )


def _curve_recipe(curve: AnyCurve) -> dict[str, Any]:
    return {"curve": curve.construction()}


def line_form(
    form_id: FormId,
    members: Sequence[SpotId],
    a: Point,
    b: Point,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds a straight-line form from `a` to `b`.

    >>> slots(line_form("f", ["A", "B", "C"], (0, 0), (4, 0))).tolist()
    [[0.0, 0.0], [2.0, 0.0], [4.0, 0.0]]
    """
    recipe = _curve_recipe(line(a, b))
    return _form(form_id, "line", recipe, members, spacing, facing, set_id, n)


def arc_form(
    form_id: FormId,
    members: Sequence[SpotId],
    center: Point,
    radius: Meters,
    start_deg: Degrees,
    sweep_deg: Degrees,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds an arc form; angles count counter-clockwise from +x.

    >>> slots(arc_form("f", ["A", "B"], (0, 0), 2.0, 0.0, 90.0)).round(6).tolist()
    [[2.0, 0.0], [0.0, 2.0]]
    """
    recipe = _curve_recipe(arc(center, radius, start_deg, sweep_deg))
    return _form(form_id, "arc", recipe, members, spacing, facing, set_id, n)


def curve_form(
    form_id: FormId,
    members: Sequence[SpotId],
    *pts: Point,
    closed: bool = False,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds a smooth curve form passing through every point.

    >>> slots(curve_form("f", ["A", "B"], (0, 0), (1, 1), (2, 0)))[-1].round(6).tolist()
    [2.0, 0.0]
    """
    recipe = _curve_recipe(curve_through(*pts, closed=closed))
    return _form(form_id, "curve", recipe, members, spacing, facing, set_id, n)


def circle_form(
    form_id: FormId,
    members: Sequence[SpotId],
    center: Point,
    radius: Meters,
    start_deg: Degrees = 0.0,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds a counter-clockwise circle form; members face outward unless told otherwise.

    >>> [p.facing for p in placements(circle_form("f", ["A", "B"], (0, 0), 1.0))]
    [90.0, 270.0]
    """
    recipe = _curve_recipe(circle(center, radius, start_deg))
    return _form(form_id, "circle", recipe, members, spacing, facing or OUTWARD, set_id, n)


def block_form(
    form_id: FormId,
    members: Sequence[SpotId],
    origin: Point,
    rows: int,
    cols: int,
    interval: Meters,
    direction_deg: Degrees = 0.0,
    anchor: BlockAnchor = "center",
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
) -> Form:
    """Builds a rows x cols block, front row first; members face `direction_deg` by default.

    >>> slots(block_form("f", [], (0, 0), 1, 2, 1.0, anchor="front-left")).tolist()
    [[0.0, 0.0], [1.0, 0.0]]
    """
    recipe = {
        "origin": [float(origin[0]), float(origin[1])],
        "rows": rows,
        "cols": cols,
        "interval": interval,
        "facing": direction_deg,
        "anchor": anchor,
        "n": rows * cols,
    }
    default = FacingSpec(mode="fixed", value=float(direction_deg))
    return _form(form_id, "block", recipe, members, None, facing or default, set_id, None)


def wedge_form(
    form_id: FormId,
    members: Sequence[SpotId],
    apex: Point,
    length: Meters,
    spread_deg: Degrees,
    direction_deg: Degrees = 0.0,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds a V whose apex leads toward `direction_deg`; members face it by default.

    >>> slots(wedge_form("f", ["A", "B", "C"], (0, 0), 2.0, 90.0))[1].round(6).tolist()
    [0.0, 0.0]
    """
    recipe = _curve_recipe(wedge(apex, length, spread_deg, direction_deg))
    default = FacingSpec(mode="fixed", value=float(direction_deg))
    return _form(form_id, "wedge", recipe, members, spacing, facing or default, set_id, n)


def polyline_form(
    form_id: FormId,
    members: Sequence[SpotId],
    *pts: Point,
    closed: bool = False,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds a form with straight sides through the corners `pts`.

    >>> slots(polyline_form("f", ["A", "B", "C"], (0, 0), (2, 0), (2, 2))).tolist()
    [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]
    """
    recipe = _curve_recipe(polyline(*pts, closed=closed))
    return _form(form_id, "polyline", recipe, members, spacing, facing, set_id, n)


def svg_form(
    form_id: FormId,
    members: Sequence[SpotId],
    svg: str,
    fit: Box2,
    keep_aspect: bool = True,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Builds a form on an SVG outline fitted into `fit`; spots share subpaths by length.

    >>> box = Box2(min=(0, 0), max=(4, 4))
    >>> len(slots(svg_form("f", [], "M0 0 L1 0 M0 1 L3 1", box, n=4)))
    4
    """
    curves = svg_outline(svg, fit, keep_aspect=keep_aspect)
    curve: AnyCurve = curves[0] if len(curves) == 1 else CompositeCurve(parts=tuple(curves))
    recipe = _curve_recipe(curve)
    return _form(form_id, "svg", recipe, members, spacing or PROPORTIONAL, facing, set_id, n)


def _region_recipe(outline: Region | shapely.Polygon | Curve | object) -> dict[str, Any]:
    if isinstance(outline, Region):
        return outline.model_dump(mode="json")
    polygon = outline if isinstance(outline, shapely.Polygon) else region(outline)
    return Region.from_shapely(polygon).model_dump(mode="json")


def fill_form(
    form_id: FormId,
    members: Sequence[SpotId],
    outline: Region | shapely.Polygon | Curve | object,
    interval: Meters,
    pattern: FillPattern = "grid",
    angle_deg: Degrees = 0.0,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Fills an area with a lattice; with members or n, keeps the slots nearest its center.

    >>> len(slots(fill_form("f", [], [(0, 0), (2, 0), (2, 2), (0, 2)], 1.0)))
    9
    """
    recipe = {
        "outline": _region_recipe(outline),
        "interval": interval,
        "pattern": pattern,
        "angle_deg": angle_deg,
    }
    return _form(form_id, "fill", recipe, members, None, facing, set_id, n)


def scatter_form(
    form_id: FormId,
    members: Sequence[SpotId],
    area: Region | shapely.Polygon | Curve | object,
    min_gap: Meters,
    seed: int = 0,
    facing: FacingSpec | None = None,
    *,
    set_id: SetId = "",
    n: int | None = None,
) -> Form:
    """Scatters one slot per member (or n) organically, at least `min_gap` apart.

    >>> len(slots(scatter_form("f", ["A", "B", "C"], [(0, 0), (6, 0), (6, 6), (0, 6)], 1.0)))
    3
    """
    recipe = {"region": _region_recipe(area), "min_gap": min_gap, "seed": seed}
    return _form(form_id, "scatter", recipe, members, None, facing, set_id, n)


def custom_form(
    form_id: FormId,
    members: Sequence[SpotId],
    points: object,
    facings: Sequence[Degrees] | None = None,
    *,
    set_id: SetId = "",
) -> Form:
    """Builds a form of frozen hand-placed spots; stored facings win over the FacingSpec.

    >>> [p.facing for p in placements(custom_form("f", ["A"], [(1, 2)], facings=[45.0]))]
    [45.0]
    """
    pts = as_points(points)
    if facings is not None and len(facings) != len(pts):
        raise SchemaError(
            f"custom form {form_id} has {len(pts)} points but {len(facings)} facings",
            hint="give one facing per point, or none",
            context={"form": form_id},
        )
    recipe = {
        "points": [[float(x), float(y)] for x, y in pts],
        "facings": None if facings is None else [float(f) for f in facings],
        "n": len(pts),
    }
    return _form(form_id, "custom", recipe, members, None, None, set_id, None)


# Any: generator parameters are JSON values defined by each generator.
def generated_form(
    form_id: FormId,
    members: Sequence[SpotId],
    fn: str,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    set_id: SetId = "",
    n: int | None = None,
    **params: Any,
) -> Form:
    """Builds a form from a registered generator; its recipe keeps the parameters editable.

    The generator decides spacing, so `spacing` is stored but has no effect.

    >>> len(slots(generated_form("f", ["A", "B", "C"], "spiral", turns=1.5)))
    3
    """
    registry.get(fn)
    recipe = {"fn": fn, "params": dict(params)}
    form = _form(form_id, "generated", recipe, members, spacing, facing, set_id, n)
    if slot_count(form) is not None:
        layout(form, registry)
    return form


# Any: generator parameters are JSON values defined by each generator.
def edit_params(
    form: Form, force: bool = False, registry: FormFnRegistry = DEFAULT_FORM_FNS, **params: Any
) -> Form:
    """Returns a generated form with some generator parameters changed.

    >>> f = generated_form("f", ["A", "B"], "spiral", turns=1.0)
    >>> edit_params(f, turns=3.0).geometry["params"]["turns"]
    3.0
    """
    check_unlocked(form, force)
    if form.kind != "generated":
        raise SchemaError(
            f"form {form.id} is a {form.kind} form, not generated",
            hint="use edit() for built-in shapes",
            context={"form": form.id, "kind": form.kind},
        )
    merged = {**form.geometry.get("params", {}), **params}
    edited = form.model_copy(update={"geometry": {**form.geometry, "params": merged}})
    if slot_count(edited) is not None:
        layout(edited, registry)
    return edited


def slots(form: Form, registry: FormFnRegistry = DEFAULT_FORM_FNS) -> Points:
    """Returns slot positions (N, 2) in slot order, before members are assigned.

    >>> slots(line_form("f", [], (0, 0), (2, 0), n=2)).tolist()
    [[0.0, 0.0], [2.0, 0.0]]
    """
    return layout(form, registry).points


def _stored_facings(form: Form) -> list[Degrees] | None:
    if form.kind != "custom" or form.geometry.get("facings") is None:
        return None
    return [float(f) for f in form.geometry["facings"]]


def slot_poses(
    form: Form, registry: FormFnRegistry = DEFAULT_FORM_FNS
) -> tuple[Points, list[Degrees]]:
    """Returns every slot's position and facing, members or not.

    >>> pts, facings = slot_poses(circle_form("f", [], (0, 0), 1.0, n=2))
    >>> facings
    [90.0, 270.0]
    """
    lay = layout(form, registry)
    return lay.points, _stored_facings(form) or member_facings(form.facing, lay)


def placements(form: Form, registry: FormFnRegistry = DEFAULT_FORM_FNS) -> list[Placement]:
    """Regenerates one placement per member from the form's recipe, spacing and facing.

    >>> [(p.entity, p.x, p.order) for p in placements(line_form("f", ["A", "B"], (0, 0), (2, 0)))]
    [('A', 0.0, 0), ('B', 2.0, 1)]
    """
    pts, facings = slot_poses(form, registry)
    if len(form.members) > len(pts):
        raise SpacingError(
            f"form {form.id} has {len(form.members)} members but {len(pts)} slots",
            hint=f"use at most {len(pts)} members or enlarge the shape",
            context={"form": form.id, "max_fit": len(pts)},
        )
    return [
        Placement(
            entity=member,
            x=float(pts[i, 0]),
            y=float(pts[i, 1]),
            facing=facings[i],
            form=form.id,
            order=i,
        )
        for i, member in enumerate(form.members)
    ]
