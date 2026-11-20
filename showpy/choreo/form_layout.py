"""
form_layout.py --- turns a form's stored recipe into slot points, travel directions and facings

The recipe contract is documented in `showpy.choreo.forms`. Curve kinds are spaced by arc
length so exact tangents are known at every slot; other kinds use the chord through
neighbouring slots as their travel direction.

Contains:
    CURVE_KINDS: names the form kinds whose recipe holds a curve
    FormLayout: holds slot points plus the directions facings are computed from
    slot_count(): resolves how many slots a form has, when the recipe decides it
    recipe_value(): reads a required recipe key, raising SchemaError when it is missing
    curve_stations(): returns the arc lengths of a curve form's slots
    layout(): builds a form's slot layout from its recipe
    member_facings(): computes one facing per slot from a FacingSpec
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely

from showpy.choreo.form_fns import DEFAULT_FORM_FNS, FormFnRegistry
from showpy.choreo.form_models import FacingSpec, Form, SpacingSpec
from showpy.choreo.form_text import text_recipe, text_slots
from showpy.core.errors import SchemaError, SpacingError
from showpy.core.types import Degrees, Point, Points, Region
from showpy.core.units import normalize_angle, vector_facing
from showpy.core.util import as_points
from showpy.geo.affine import transform_points
from showpy.geo.curve_base import FloatArray
from showpy.geo.curves import (
    AnyCurve,
    ArcCurve,
    CircleCurve,
    CompositeCurve,
    curve_from_construction,
)
from showpy.geo.fonts import FontRegistry
from showpy.geo.spacing import block, fill, scatter, space_proportional

CURVE_KINDS = frozenset({"line", "arc", "curve", "circle", "wedge", "polyline", "svg"})
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

    >>> slot_count(Form(id="f", set="1", kind="custom", geometry={}, members=("A", "B")))
    2
    """
    if form.geometry.get("n") is not None:
        return int(form.geometry["n"])
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


# Any: recipe values are JSON whose type depends on the key.
def recipe_value(form: Form, key: str) -> Any:
    """Reads a required recipe key, raising SchemaError when it is missing.

    >>> recipe_value(Form(id="f", set="1", kind="custom", geometry={"points": []},
    ...     members=()), "points")
    []
    """
    if key not in form.geometry:
        raise SchemaError(
            f"form {form.id} ({form.kind}) recipe lacks {key!r}",
            hint=f"rebuild the form with its constructor; {form.kind} recipes need {key!r}",
            context={"form": form.id, "keys": sorted(form.geometry)},
        )
    return form.geometry[key]


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

    >>> from showpy.geo.shapes import line
    >>> curve_stations(line((0, 0), (4, 0)), SpacingSpec(), 3).tolist()
    [0.0, 2.0, 4.0]
    """
    length = curve.length()
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
    full_loop = curve.closed and math.isclose(stop - start, length)
    return _even_stations(start, stop, n, full_loop=full_loop, gaps=spacing.gaps)


def _proportional_layout(curve: CompositeCurve, n: int) -> FormLayout:
    counts = [len(pts) for pts in space_proportional(curve.parts, n)]
    points, tangents = [], []
    for part, count in zip(curve.parts, counts, strict=True):
        stations = _even_stations(0.0, part.length(), count, full_loop=part.closed, gaps=())
        points.append(part.point_at(stations))
        tangents.append(part.tangent_at(stations))
    return FormLayout(points=np.vstack(points), tangents=np.vstack(tangents))


def _curve_layout(form: Form) -> FormLayout:
    curve = curve_from_construction(recipe_value(form, "curve"))
    if form.spacing.mode == "proportional" and isinstance(curve, CompositeCurve):
        return _proportional_layout(curve, _required_count(form))
    stations = curve_stations(curve, form.spacing, slot_count(form))
    center = curve.center if isinstance(curve, ArcCurve | CircleCurve) else None
    return FormLayout(curve.point_at(stations), curve.tangent_at(stations), center)


def _region(form: Form, key: str) -> shapely.Polygon:
    return Region.model_validate(recipe_value(form, key)).to_shapely()


def _block_points(form: Form, _registry: FormFnRegistry) -> Points:
    g = form.geometry
    origin = as_points(recipe_value(form, "origin"))[0]
    return block(
        (float(origin[0]), float(origin[1])),
        int(recipe_value(form, "rows")),
        int(recipe_value(form, "cols")),
        float(recipe_value(form, "interval")),
        float(g.get("facing", 0.0)),
        g.get("anchor", "center"),
    )


def _fill_points(form: Form, _registry: FormFnRegistry) -> Points:
    g = form.geometry
    interval = float(recipe_value(form, "interval"))
    pattern, angle = g.get("pattern", "grid"), float(g.get("angle_deg", 0.0))
    return fill(_region(form, "outline"), interval, pattern, angle, n=slot_count(form))


def _scatter_points(form: Form, _registry: FormFnRegistry) -> Points:
    min_gap = float(recipe_value(form, "min_gap"))
    seed = int(form.geometry.get("seed", 0))
    return scatter(_region(form, "region"), _required_count(form), min_gap, seed)


def _custom_points(form: Form, _registry: FormFnRegistry) -> Points:
    return as_points(recipe_value(form, "points"))


def _generated_points(form: Form, registry: FormFnRegistry) -> Points:
    n = _required_count(form)
    fn = registry.get(recipe_value(form, "fn"))
    pts = np.asarray(fn(form.geometry.get("params", {}), n), dtype=float)
    if pts.shape != (n, 2) or not np.all(np.isfinite(pts)):
        raise SchemaError(
            f"generator {form.geometry['fn']!r} returned shape {pts.shape} for n={n}",
            hint="the generator must return n finite (x, y) rows",
            context={"form": form.id, "fn": form.geometry["fn"]},
        )
    return pts


RAW_POINTS: dict[str, Callable[[Form, FormFnRegistry], Points]] = {
    "block": _block_points,
    "fill": _fill_points,
    "scatter": _scatter_points,
    "custom": _custom_points,
    "generated": _generated_points,
}


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
    return FormLayout(points=pts, tangents=unit)


def _text_layout(form: Form, font_registry: FontRegistry | None) -> FormLayout:
    stored = form.geometry.get("transform")
    transform = None if stored is None else np.asarray(stored, dtype=float)
    points, tangents = text_slots(
        text_recipe(form.geometry),
        _required_count(form),
        font_registry=font_registry,
        transform=transform,
    )
    lay = FormLayout(points=points, tangents=tangents)
    return lay if transform is None else _transformed(lay, transform)


def layout(
    form: Form,
    registry: FormFnRegistry = DEFAULT_FORM_FNS,
    *,
    font_registry: FontRegistry | None = None,
) -> FormLayout:
    """Builds a form's slot layout from its recipe, applying any stored `transform`.

    A text form's tangents go through the transform's linear part and are re-normalized to
    unit length, so `along` facings turn with the text. Other kinds ignore `font_registry`.

    >>> from showpy.geo.shapes import line
    >>> f = Form(id="f", set="1", kind="line", members=("A", "B"),
    ...     geometry={"curve": line((0, 0), (2, 0)).construction()})
    >>> layout(f).points.tolist()
    [[0.0, 0.0], [2.0, 0.0]]
    """
    if form.kind in CURVE_KINDS:
        return _curve_layout(form)
    if form.kind == "text":
        return _text_layout(form, font_registry)
    pts = RAW_POINTS[form.kind](form, registry)
    transform = form.geometry.get("transform")
    if transform is not None:
        pts = transform_points(pts, np.asarray(transform, dtype=float))
    return FormLayout(points=pts, tangents=_chord_tangents(pts))


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
