"""
forms.py --- form constructors plus slot and placement generation from stored recipes

A form stores how to rebuild its shape, never the dots. `Form.recipe` is a typed recipe tagged
by `kind` (`e8s.choreo.recipe_models` and `TextRecipe`):

    line, arc, curve, circle, wedge, polyline: the curve the slots are spaced along
    svg:       one curve per subpath of the fitted outline; proportional spacing shares slots
               among the subpaths by length, count and interval spacing walk them in order
    block:     origin, rows, cols, interval, facing, anchor
    fill:      outline area, interval, pattern, angle_deg
    scatter:   region, min_gap, seed
    custom:    points and optional facings
    generated: registered generator name and its JSON parameters
    text:      text, font URIs, layout and the spacing that last set the count; `n` is the
               stored count, always present; only edit_text_count changes either

Every recipe holds `n`, the slot count; without it a form has one slot per member (interval
spacing places as many as fit, a block has rows x cols, a custom form one per point, a fill
every lattice point), and `transform`, a 3x3 affine matrix applied to the generated slots,
which keeps gated or mirrored forms editable. Members fill slots in order: member i stands on
slot i; slots past the last member are open for `place` to fill, except in a text form, which
has exactly one member per slot.

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
    text_form(): builds a form that writes text on the field, one member per slot
    edit_params(): returns a generated form with changed generator parameters
    slots(): returns a form's slot positions
    slot_poses(): returns every slot's position and facing
    placements(): regenerates placements for a form's members
"""

from collections.abc import Sequence
from typing import Any

import shapely

from e8s.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from e8s.choreo.form_layout import layout, member_facings, slot_count
from e8s.choreo.form_models import FacingSpec, Form, Recipe, SpacingSpec, TextRecipe
from e8s.choreo.form_text import require_member_count, require_text_members, stored_count
from e8s.choreo.models import Placement
from e8s.choreo.recipe_models import (
    ArcRecipe,
    BlockRecipe,
    CircleRecipe,
    CurveRecipe,
    CustomRecipe,
    FillRecipe,
    GeneratedRecipe,
    LineRecipe,
    PolylineRecipe,
    ScatterRecipe,
    SvgRecipe,
    WedgeRecipe,
)
from e8s.choreo.text_count import count_recipe
from e8s.core.errors import LockedError, SchemaError, SpacingError
from e8s.core.types import Box2, Degrees, Meters, Point, Points, Region
from e8s.core.util import as_points
from e8s.geo.curve_base import Curve
from e8s.geo.fonts import FontRegistry, bundled_fonts
from e8s.geo.models import DEFAULT_LINE_SPACING, TextAlign, TextAnchor
from e8s.geo.regions import region
from e8s.geo.shapes import arc, circle, curve_through, line, polyline, svg_outline, wedge
from e8s.geo.spacing import BlockAnchor, FillPattern

PROPORTIONAL = SpacingSpec(mode="proportional")
OUTWARD = FacingSpec(mode="outward")


def check_unlocked(form: Form, *, force: bool) -> None:
    """Raises LockedError when the form is locked and the caller did not pass force=True.

    >>> custom = Form(id="f", set="1", recipe={"kind": "custom", "points": []}, members=())
    >>> check_unlocked(custom, force=False)
    """
    if form.locked and not force:
        raise LockedError(
            f"form {form.id} is locked",
            hint="unlock it first, or pass force=True as the designer",
            context={"form": form.id, "set": form.set},
        )


def _form(
    form_id: str,
    recipe: Recipe,
    members: Sequence[str | None],
    spacing: SpacingSpec | None,
    facing: FacingSpec | None,
    set_id: str,
) -> Form:
    return Form._validated(
        {
            "id": form_id,
            "set": set_id,
            "recipe": recipe,
            "members": tuple(members),
            "spacing": spacing or SpacingSpec(),
            "facing": facing or FacingSpec(),
        }
    )


def line_form(
    form_id: str,
    members: Sequence[str],
    a: Point,
    b: Point,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds a straight-line form from `a` to `b`.

    >>> slots(line_form("f", ["A", "B", "C"], (0, 0), (4, 0))).tolist()
    [[0.0, 0.0], [2.0, 0.0], [4.0, 0.0]]
    """
    recipe = LineRecipe(curve=line(a, b), n=n)
    return _form(form_id, recipe, members, spacing, facing, set_id)


def arc_form(
    form_id: str,
    members: Sequence[str],
    center: Point,
    radius: Meters,
    start_deg: Degrees,
    sweep_deg: Degrees,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds an arc form; angles count counter-clockwise from +x.

    >>> slots(arc_form("f", ["A", "B"], (0, 0), 2.0, 0.0, 90.0)).round(6).tolist()
    [[2.0, 0.0], [0.0, 2.0]]
    """
    recipe = ArcRecipe(curve=arc(center, radius, start_deg, sweep_deg), n=n)
    return _form(form_id, recipe, members, spacing, facing, set_id)


def curve_form(
    form_id: str,
    members: Sequence[str],
    *pts: Point,
    closed: bool = False,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds a smooth curve form passing through every point.

    >>> slots(curve_form("f", ["A", "B"], (0, 0), (1, 1), (2, 0)))[-1].round(6).tolist()
    [2.0, 0.0]
    """
    recipe = CurveRecipe(curve=curve_through(*pts, closed=closed), n=n)
    return _form(form_id, recipe, members, spacing, facing, set_id)


def circle_form(
    form_id: str,
    members: Sequence[str],
    center: Point,
    radius: Meters,
    start_deg: Degrees = 0.0,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds a counter-clockwise circle form; members face outward unless told otherwise.

    >>> [p.facing for p in placements(circle_form("f", ["A", "B"], (0, 0), 1.0))]
    [90.0, 270.0]
    """
    recipe = CircleRecipe(curve=circle(center, radius, start_deg), n=n)
    return _form(form_id, recipe, members, spacing, facing or OUTWARD, set_id)


def block_form(
    form_id: str,
    members: Sequence[str],
    origin: Point,
    rows: int,
    cols: int,
    interval: Meters,
    direction_deg: Degrees = 0.0,
    anchor: BlockAnchor = "center",
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
) -> Form:
    """Builds a rows x cols block, front row first; members face `direction_deg` by default.

    >>> slots(block_form("f", [], (0, 0), 1, 2, 1.0, anchor="front-left")).tolist()
    [[0.0, 0.0], [1.0, 0.0]]
    """
    recipe = BlockRecipe._validated(
        {
            "origin": (float(origin[0]), float(origin[1])),
            "rows": rows,
            "cols": cols,
            "interval": interval,
            "facing": direction_deg,
            "anchor": anchor,
            "n": rows * cols,
        }
    )
    default = FacingSpec(mode="fixed", value=float(direction_deg))
    return _form(form_id, recipe, members, None, facing or default, set_id)


def wedge_form(
    form_id: str,
    members: Sequence[str],
    apex: Point,
    length: Meters,
    spread_deg: Degrees,
    direction_deg: Degrees = 0.0,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds a V whose apex leads toward `direction_deg`; members face it by default.

    >>> slots(wedge_form("f", ["A", "B", "C"], (0, 0), 2.0, 90.0))[1].round(6).tolist()
    [0.0, 0.0]
    """
    recipe = WedgeRecipe(curve=wedge(apex, length, spread_deg, direction_deg), n=n)
    default = FacingSpec(mode="fixed", value=float(direction_deg))
    return _form(form_id, recipe, members, spacing, facing or default, set_id)


def polyline_form(
    form_id: str,
    members: Sequence[str],
    *pts: Point,
    closed: bool = False,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds a form with straight sides through the corners `pts`.

    >>> slots(polyline_form("f", ["A", "B", "C"], (0, 0), (2, 0), (2, 2))).tolist()
    [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]
    """
    recipe = PolylineRecipe(curve=polyline(*pts, closed=closed), n=n)
    return _form(form_id, recipe, members, spacing, facing, set_id)


def svg_form(
    form_id: str,
    members: Sequence[str],
    svg: str,
    fit: Box2,
    *,
    keep_aspect: bool = True,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Builds a form on an SVG outline fitted into `fit`; spots share subpaths by length.

    Each subpath is stored as its own curve. `count` and `interval` spacing walk the subpaths
    one after another, each starting where the last ended, so the jump between subpaths adds
    no length.

    >>> box = Box2(min=(0, 0), max=(4, 4))
    >>> len(slots(svg_form("f", [], "M0 0 L1 0 M0 1 L3 1", box, n=4)))
    4
    """
    recipe = SvgRecipe(curves=tuple(svg_outline(svg, fit, keep_aspect=keep_aspect)), n=n)
    return _form(form_id, recipe, members, spacing or PROPORTIONAL, facing, set_id)


def _region(outline: Region | shapely.Polygon | Curve | object) -> Region:
    if isinstance(outline, Region):
        return outline
    polygon = outline if isinstance(outline, shapely.Polygon) else region(outline)
    return Region.from_shapely(polygon)


def fill_form(
    form_id: str,
    members: Sequence[str],
    outline: Region | shapely.Polygon | Curve | object,
    interval: Meters,
    pattern: FillPattern = "grid",
    angle_deg: Degrees = 0.0,
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Fills an area with a lattice; with members or n, keeps the slots nearest its center.

    >>> len(slots(fill_form("f", [], [(0, 0), (2, 0), (2, 2), (0, 2)], 1.0)))
    9
    """
    recipe = FillRecipe._validated(
        {
            "outline": _region(outline),
            "interval": interval,
            "pattern": pattern,
            "angle_deg": angle_deg,
            "n": n,
        }
    )
    return _form(form_id, recipe, members, None, facing, set_id)


def scatter_form(
    form_id: str,
    members: Sequence[str],
    area: Region | shapely.Polygon | Curve | object,
    min_gap: Meters,
    seed: int = 0,
    facing: FacingSpec | None = None,
    *,
    set_id: str = "",
    n: int | None = None,
) -> Form:
    """Scatters one slot per member (or n) organically, at least `min_gap` apart.

    >>> len(slots(scatter_form("f", ["A", "B", "C"], [(0, 0), (6, 0), (6, 6), (0, 6)], 1.0)))
    3
    """
    recipe = ScatterRecipe(region=_region(area), min_gap=min_gap, seed=seed, n=n)
    return _form(form_id, recipe, members, None, facing, set_id)


def custom_form(
    form_id: str,
    members: Sequence[str | None],
    points: object,
    facings: Sequence[Degrees] | None = None,
    *,
    set_id: str = "",
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
    recipe = CustomRecipe._validated(
        {
            "points": [(float(x), float(y)) for x, y in pts],
            "facings": None if facings is None else [float(f) for f in facings],
            "n": len(pts),
        }
    )
    return _form(form_id, recipe, members, None, None, set_id)


# Any: generator parameters are JSON values defined by each generator.
def generated_form(
    form_id: str,
    members: Sequence[str],
    fn: str,
    spacing: SpacingSpec | None = None,
    facing: FacingSpec | None = None,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    set_id: str = "",
    n: int | None = None,
    **params: Any,
) -> Form:
    """Builds a form from a registered generator; its recipe keeps the parameters editable.

    The generator decides spacing, so `spacing` is stored but has no effect.

    >>> len(slots(generated_form("f", ["A", "B", "C"], "spiral", turns=1.5)))
    3
    """
    registry.get(fn)
    recipe = GeneratedRecipe(fn=fn, params=params, n=n)
    form = _form(form_id, recipe, members, spacing, facing, set_id)
    if slot_count(form) is not None:
        layout(form, registry)
    return form


def text_form(
    form_id: str,
    members: Sequence[str],
    text: str,
    *,
    fonts: Sequence[str],
    height: Meters,
    at: Point,
    rotation: Degrees = 0.0,
    align: TextAlign = "left",
    anchor: TextAnchor = "baseline",
    letter_spacing: float = 0.0,
    line_spacing: float = DEFAULT_LINE_SPACING,
    facing: FacingSpec | None = None,
    set_id: str = "",
    n: int | None = None,
    spacing: Meters | None = None,
    font_registry: FontRegistry | None = None,
) -> Form:
    r"""Builds a form that writes `text` on the field with exactly `n` spots, one per member.

    Fonts are stored as URIs, so the recipe names the exact font bytes. Member i stands on
    slot i in reading order; a text form needs exactly `n` members. Give exactly one of `n`
    and `spacing`; a spacing derives `n` from the stroke lengths and is stored beside it.

    Args:
        form_id: ID of the new form.
        members: Spots in slot order; there must be exactly `n`.
        text: Characters to write, `\n` between lines.
        fonts: Font names or URIs, first choice first.
        height: Cap height in meters.
        at: Layout point on the stage plane.
        rotation: Degrees counter-clockwise about `at`.
        align: Where each line sits horizontally on `at`.
        anchor: Which line of the text block sits vertically on `at`.
        letter_spacing: Extra gap between glyphs, in ems of the first font.
        line_spacing: Baseline-to-baseline distance, in ems of the first font.
        facing: Facing spec, or None for audience.
        set_id: Set the form belongs to.
        n: Number of spots; at least the text's stroke minimum.
        spacing: Meters between spots along the strokes, instead of `n`.
        font_registry: Registry that resolves the fonts; None means `bundled_fonts()`.

    Raises:
        SchemaError: `N_OR_SPACING` unless exactly one of `n` and `spacing` is given.
        SpacingError: `TOO_FEW_SPOTS`, `TOO_MANY_SPOTS` or `MEMBER_COUNT_MISMATCH`.
        MissingFontError: A font is not in the registry.

    >>> go = text_form("go", [f"S{i}" for i in range(24)], "GO", fonts=["hershey-roman"],
    ...     height=4.0, at=(0, 10), n=24)
    >>> len(slots(go))
    24
    """
    fonts_registry = bundled_fonts() if font_registry is None else font_registry
    recipe = TextRecipe(
        text=text,
        fonts=tuple(fonts_registry.resolve(font) for font in fonts),
        height=height,
        at=at,
        rotation=rotation,
        align=align,
        anchor=anchor,
        letter_spacing=letter_spacing,
        line_spacing=line_spacing,
    )
    counted = count_recipe(recipe, n, spacing, font_registry)
    require_member_count(form_id, stored_count(form_id, counted), len(members))
    form = _form(form_id, counted, members, None, facing, set_id)
    layout(form, font_registry=font_registry)
    return form


# Any: generator parameters are JSON values defined by each generator.
def edit_params(
    form: Form, *, force: bool = False, registry: FormFnRegistry = DEFAULT_FORM_FNS, **params: Any
) -> Form:
    """Returns a generated form with some generator parameters changed.

    >>> f = generated_form("f", ["A", "B"], "spiral", turns=1.0)
    >>> edit_params(f, turns=3.0).recipe.params["turns"]
    3.0
    """
    check_unlocked(form, force=force)
    recipe = form.recipe
    if not isinstance(recipe, GeneratedRecipe):
        raise SchemaError(
            f"form {form.id} is a {form.kind} form, not generated",
            hint="use edit() for built-in shapes",
            context={"form": form.id, "kind": form.kind},
        )
    edited = form.replace(recipe=recipe.replace(params={**recipe.params, **params}))
    if slot_count(edited) is not None:
        layout(edited, registry)
    return edited


def slots(
    form: Form,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
) -> Points:
    """Returns slot positions (N, 2) in slot order, before members are assigned.

    `font_registry` holds a text form's fonts; None means `bundled_fonts()`.

    >>> slots(line_form("f", [], (0, 0), (2, 0), n=2)).tolist()
    [[0.0, 0.0], [2.0, 0.0]]
    """
    return layout(form, registry, font_registry=font_registry).points


def _stored_facings(form: Form) -> list[Degrees] | None:
    if not isinstance(form.recipe, CustomRecipe) or form.recipe.facings is None:
        return None
    return list(form.recipe.facings)


def slot_poses(
    form: Form,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
) -> tuple[Points, list[Degrees]]:
    """Returns every slot's position and facing, members or not.

    `font_registry` holds a text form's fonts; None means `bundled_fonts()`.

    >>> pts, facings = slot_poses(circle_form("f", [], (0, 0), 1.0, n=2))
    >>> facings
    [90.0, 270.0]
    """
    lay = layout(form, registry, font_registry=font_registry)
    return lay.points, _stored_facings(form) or member_facings(form.facing, lay)


def placements(
    form: Form,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
) -> list[Placement]:
    """Regenerates one placement per spot from the form's recipe, spacing and facing.

    A spot's `order` is the slot it stands on; open slots get no placement.

    `font_registry` holds a text form's fonts; None means `bundled_fonts()`. A text form whose
    members no longer fill its slots raises `MEMBER_COUNT_MISMATCH` before any layout.

    >>> [(p.entity, p.x, p.order) for p in placements(line_form("f", ["A", "B"], (0, 0), (2, 0)))]
    [('A', 0.0, 0), ('B', 2.0, 1)]
    """
    require_text_members(form)
    pts, facings = slot_poses(form, registry, font_registry=font_registry)
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
        if member is not None
    ]
