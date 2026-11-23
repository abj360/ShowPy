"""
form_layout.py --- turns a form's stored recipe into slot points, travel directions and facings

The recipe contract is documented in `e8s.choreo.forms`. Curve kinds are spaced by arc
length so exact tangents are known at every slot. An svg outline's subpaths share proportional
slots by length; count and interval spacing walk them one after another. Other kinds use the
chord through neighbouring slots as their travel direction. A stored `transform` maps the slots
of every kind, and the travel directions through its linear part.

Contains:
    FormLayout: holds slot points plus the directions facings are computed from
    slot_count(): resolves how many slots a form has, when the recipe decides it
    curve_stations(): returns the arc lengths of a curve form's slots
    layout(): builds a form's slot layout from its recipe
    member_facings(): computes one facing per slot from a FacingSpec
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from e8s.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from e8s.choreo.form_models import FacingSpec, Form, SpacingSpec, TextRecipe
from e8s.choreo.form_text import text_slots
from e8s.choreo.recipe_models import (
    BlockRecipe,
    CurveRecipeBase,
    CustomRecipe,
    FillRecipe,
    GeneratedRecipe,
    ScatterRecipe,
    SvgRecipe,
)
from e8s.core.errors import SchemaError, SpacingError
from e8s.core.types import Degrees, JsonValue, Point, Points
from e8s.core.units import normalize_angle, vector_facing
from e8s.core.util import as_points
from e8s.geo.affine import transform_points
from e8s.geo.curve_base import FloatArray
from e8s.geo.curves import (
    AnyCurve,
    ArcCurve,
    CircleCurve,
    CompositeCurve,
)
from e8s.geo.fonts import FontRegistry
from e8s.geo.spacing import block, fill, scatter, space_proportional

type PointRecipe = BlockRecipe | FillRecipe | ScatterRecipe | CustomRecipe | GeneratedRecipe

OFFSET_MODES = frozenset({"along", "normal", "outward"})
WINDOW_TOL = 1e-9
FIT_EPS = 1e-9
ZERO_DIRECTION = 1e-12
AUDIENCE: Degrees = 0.0


@dataclass(frozen=True)
class FormLayout:
    """Holds slot points plus the directions facings are computed from.

    Attributes:
        points: Slot positions (N, 2) in slot order.
        tangents: Travel direction at each slot; zero rows where it is undefined.
        center: Circle or arc center for outward facings; None uses the slot centroid.
    """

    points: Points
    tangents: Points
    center: Point | None = None


def slot_count(form: Form) -> int | None:
    """Resolves the slot count: the recipe's `n`, else the member count, else None.

    >>> slot_count(Form(id="f", set="1", recipe={"kind": "custom", "points": []},
    ...     members=("A", "B")))
    2
    """
    if form.recipe.n is not None:
        return form.recipe.n
    return len(form.members) or None


def _required_count(form: Form) -> int:
    count = slot_count(form)
    if count is None or count < 1:
        raise SpacingError(
            f"form {form.id} has no members and no slot count",
            hint="give members or pass n= to the form constructor",
            context={"form": form.id},
        )
    return count


def _extras(gaps: tuple[tuple[int, float], ...], slots: int) -> FloatArray:
    extra = np.zeros(max(slots, 0))
    for index, meters in gaps:
        if not 0 <= index < slots:
            raise SpacingError(
                f"gap index {index} out of range",
                hint=f"use gap indexes 0..{slots - 1}",
                context={"index": index, "slots": slots},
            )
        extra[index] += meters
    return extra


def _even_stations(
    start: float, stop: float, n: int, *, full_loop: bool, gaps: tuple[tuple[int, float], ...]
) -> FloatArray:
    if n == 1:
        return np.array([(start + stop) / 2])
    divisions = n if full_loop else n - 1
    extra = _extras(gaps, divisions)
    step = (stop - start - float(extra.sum())) / divisions
    if step < 0:
        raise SpacingError("gaps exceed the spacing window", hint="shrink the gaps")
    stations: FloatArray = start + np.concatenate([[0.0], np.cumsum(step + extra)[: n - 1]])
    return stations


def _interval_stations(
    start: float, stop: float, spacing: SpacingSpec, n: int | None
) -> FloatArray:
    interval = float(spacing.interval or 0.0)
    available = stop - start
    gap_total = sum(meters for _, meters in spacing.gaps)
    max_fit = math.floor((available - gap_total) / interval + FIT_EPS) + 1
    count = max_fit if n is None else n
    if count > max_fit or count < 1:
        raise SpacingError(
            f"{count} spots at {interval} m do not fit in {available:.2f} m",
            hint=f"use at most {max_fit} spots or a smaller interval",
            context={"max_fit": max_fit, "requested": count},
        )
    extra = _extras(spacing.gaps, count - 1)
    spare = available - (count - 1) * interval - float(extra.sum())
    shift = {"start": 0.0, "end": spare, "center": spare / 2}[spacing.align]
    stations: FloatArray = start + shift + np.concatenate([[0.0], np.cumsum(interval + extra)])
    return stations


def curve_stations(curve: AnyCurve, spacing: SpacingSpec, n: int | None) -> FloatArray:
    """Returns the arc lengths of a curve form's slots for the count or interval modes.

    >>> from e8s.geo.shapes import line
    >>> curve_stations(line((0, 0), (4, 0)), SpacingSpec(), 3).tolist()
    [0.0, 2.0, 4.0]
    """
    return _window_stations(curve.length(), spacing, n, closed=curve.closed)


def _window_stations(
    length: float, spacing: SpacingSpec, n: int | None, *, closed: bool
) -> FloatArray:
    start, stop = spacing.start, length if spacing.end is None else spacing.end
    if not 0 <= start < stop <= length + WINDOW_TOL:
        raise SpacingError(
            f"spacing window {start}..{stop} m is outside the {length:.2f} m shape",
            hint="use 0 <= start < end <= the shape length",
            context={"start": start, "end": stop, "length": length},
        )
    if spacing.mode == "interval":
        return _interval_stations(start, stop, spacing, n)
    if n is None or n < 1:
        raise SpacingError("count spacing needs a slot count", hint="give members or n=")
    full_loop = closed and math.isclose(stop - start, length)
    return _even_stations(start, stop, n, full_loop=full_loop, gaps=spacing.gaps)


def _proportional_layout(parts: Sequence[AnyCurve], n: int) -> FormLayout:
    counts = [len(pts) for pts in space_proportional(parts, n)]
    points, tangents = [], []
    for part, count in zip(parts, counts, strict=True):
        stations = _even_stations(0.0, part.length(), count, full_loop=part.closed, gaps=())
        points.append(part.point_at(stations))
        tangents.append(part.tangent_at(stations))
    return FormLayout(points=np.vstack(points), tangents=np.vstack(tangents))


def _curve_layout(form: Form, curve: AnyCurve) -> FormLayout:
    if form.spacing.mode == "proportional" and isinstance(curve, CompositeCurve):
        return _proportional_layout(curve.parts, _required_count(form))
    stations = curve_stations(curve, form.spacing, slot_count(form))
    center = curve.center if isinstance(curve, ArcCurve | CircleCurve) else None
    return FormLayout(curve.point_at(stations), curve.tangent_at(stations), center)


def _run_layout(form: Form, curves: Sequence[AnyCurve]) -> FormLayout:
    """Spaces slots along curves walked one after another, each starting where the last ended.

    The jump from one curve's end to the next curve's start adds no length, so a station at a
    joint lands on the start of the next curve.
    """
    offsets = np.concatenate([[0.0], np.cumsum([curve.length() for curve in curves])])
    total = float(offsets[-1])
    stations = np.clip(
        _window_stations(total, form.spacing, slot_count(form), closed=False), 0, total
    )
    index = np.clip(np.searchsorted(offsets, stations, side="right") - 1, 0, len(curves) - 1)
    points, tangents = np.empty((len(stations), 2)), np.empty((len(stations), 2))
    for i in np.unique(index):
        mask = index == i
        local = stations[mask] - offsets[i]
        points[mask], tangents[mask] = curves[i].point_at(local), curves[i].tangent_at(local)
    return FormLayout(points=points, tangents=tangents)


def _svg_layout(form: Form, recipe: SvgRecipe) -> FormLayout:
    if len(recipe.curves) == 1:
        return _curve_layout(form, recipe.curves[0])
    if form.spacing.mode == "proportional":
        return _proportional_layout(recipe.curves, _required_count(form))
    return _run_layout(form, recipe.curves)


def _block_points(recipe: BlockRecipe) -> Points:
    return block(
        recipe.origin, recipe.rows, recipe.cols, recipe.interval, recipe.facing, recipe.anchor
    )


def _fill_points(form: Form, recipe: FillRecipe) -> Points:
    outline = recipe.outline.to_shapely()
    return fill(outline, recipe.interval, recipe.pattern, recipe.angle_deg, n=slot_count(form))


def _scatter_points(form: Form, recipe: ScatterRecipe) -> Points:
    count = _required_count(form)
    return scatter(recipe.region.to_shapely(), count, recipe.min_gap, recipe.seed)


def _generated_points(form: Form, recipe: GeneratedRecipe, registry: FormFnRegistry) -> Points:
    n = _required_count(form)
    fn = registry.get(recipe.fn)
    # The params field is a JSON object, so its JSON dump is a dict.
    params = cast("dict[str, JsonValue]", recipe.to_json()["params"])
    pts = np.asarray(fn(params, n), dtype=float)
    if pts.shape != (n, 2) or not np.all(np.isfinite(pts)):
        raise SchemaError(
            f"generator {recipe.fn!r} returned shape {pts.shape} for n={n}",
            hint="the generator must return n finite (x, y) rows",
            context={"form": form.id, "fn": recipe.fn},
        )
    return pts


def _raw_points(form: Form, recipe: PointRecipe, registry: FormFnRegistry) -> Points:
    if isinstance(recipe, BlockRecipe):
        return _block_points(recipe)
    if isinstance(recipe, FillRecipe):
        return _fill_points(form, recipe)
    if isinstance(recipe, ScatterRecipe):
        return _scatter_points(form, recipe)
    if isinstance(recipe, CustomRecipe):
        return as_points(recipe.points)
    return _generated_points(form, recipe, registry)


def _chord_tangents(pts: Points) -> Points:
    if len(pts) < 2:
        return np.zeros_like(pts)
    tangents: Points = np.gradient(pts, axis=0)
    return tangents


def _transformed(lay: FormLayout, matrix: FloatArray) -> FormLayout:
    pts = transform_points(lay.points, matrix)
    turned = lay.tangents @ matrix[:2, :2].T
    lengths = np.linalg.norm(turned, axis=1, keepdims=True)
    unit = np.divide(turned, lengths, out=np.zeros_like(turned), where=lengths > ZERO_DIRECTION)
    if lay.center is None:
        return FormLayout(points=pts, tangents=unit)
    cx, cy = transform_points(np.asarray([lay.center], dtype=float), matrix)[0]
    return FormLayout(points=pts, tangents=unit, center=(float(cx), float(cy)))


def _text_layout(
    form: Form, recipe: TextRecipe, transform: FloatArray | None, font_registry: FontRegistry | None
) -> FormLayout:
    points, tangents = text_slots(
        recipe, _required_count(form), font_registry=font_registry, transform=transform
    )
    return FormLayout(points=points, tangents=tangents)


def layout(
    form: Form,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
) -> FormLayout:
    """Builds a form's slot layout from its recipe, applying any stored `transform`.

    Curve and text tangents go through the transform's linear part and are re-normalized to
    unit length, so `along` facings turn with the form; other kinds take the chord through
    their mapped slots. Kinds other than text ignore `font_registry`.

    >>> from e8s.geo.shapes import line
    >>> f = Form(id="f", set="1", members=("A", "B"),
    ...     recipe={"kind": "line", "curve": line((0, 0), (2, 0))})
    >>> layout(f).points.tolist()
    [[0.0, 0.0], [2.0, 0.0]]
    """
    recipe = form.recipe
    transform = None if recipe.transform is None else np.asarray(recipe.transform, dtype=float)
    if isinstance(recipe, CurveRecipeBase):
        lay = _curve_layout(form, recipe.curve)
    elif isinstance(recipe, SvgRecipe):
        lay = _svg_layout(form, recipe)
    elif isinstance(recipe, TextRecipe):
        lay = _text_layout(form, recipe, transform, font_registry)
    else:
        pts = _raw_points(form, recipe, registry)
        pts = pts if transform is None else transform_points(pts, transform)
        return FormLayout(points=pts, tangents=_chord_tangents(pts))
    return lay if transform is None else _transformed(lay, transform)


def _facing_or_audience(direction: Points) -> Degrees:
    x, y = float(direction[0]), float(direction[1])
    if math.hypot(x, y) < ZERO_DIRECTION:
        return AUDIENCE
    return vector_facing((x, y))


def _directions(spec: FacingSpec, lay: FormLayout) -> Points:
    if spec.mode == "toward_point":
        return np.asarray(spec.value, dtype=float) - lay.points
    if spec.mode == "along":
        return lay.tangents
    if spec.mode == "normal":
        return np.stack([-lay.tangents[:, 1], lay.tangents[:, 0]], axis=1)
    center = lay.points.mean(axis=0) if lay.center is None else np.asarray(lay.center)
    return lay.points - center


def member_facings(spec: FacingSpec, lay: FormLayout) -> list[Degrees]:
    """Computes one facing per slot; a zero direction (a spot on its own target) faces front.

    >>> lay = FormLayout(points=np.array([[1.0, 0.0]]), tangents=np.zeros((1, 2)),
    ...     center=(0.0, 0.0))
    >>> member_facings(FacingSpec(mode="outward"), lay)
    [90.0]
    """
    n = len(lay.points)
    if spec.mode == "audience":
        return [AUDIENCE] * n
    number = float(spec.value) if isinstance(spec.value, int | float) else 0.0
    if spec.mode == "fixed":
        return [normalize_angle(number)] * n
    offset = number if spec.mode in OFFSET_MODES else 0.0
    return [normalize_angle(_facing_or_audience(d) + offset) for d in _directions(spec, lay)]
