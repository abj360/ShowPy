#!/usr/bin/env python3

"""
forms.py --- strategies that build every form kind and place it in a set

Contains:
    FORM_KINDS: every form kind the strategies build
    FormDraft: holds the ID, members, center, set and facing shared by every form builder
    form_facings(): draws a FacingSpec in any of its modes
    forms_of(): draws a form of one kind with exactly one slot per member
    with_form(): builds a form on some spots of a set, places it and stores it on the show
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from hypothesis import strategies as st

from showpy.choreo.form_models import FacingSpec, Form, FormKind, SpacingSpec
from showpy.choreo.form_place import place
from showpy.choreo.forms import (
    arc_form,
    block_form,
    circle_form,
    curve_form,
    custom_form,
    fill_form,
    generated_form,
    line_form,
    polyline_form,
    scatter_form,
    svg_form,
    text_form,
    wedge_form,
)
from showpy.choreo.match_ops import with_set
from showpy.core.types import Box2, Point
from showpy.geo.shapes import rect
from showpy.geo.spacing import FillPattern
from showpy.sheet.models import Sheet
from showpy.show.changes import apply_change
from showpy.show.models import FormOp, Show
from tests.strategies.sheet import headings, points_on

FORM_MARGIN_M = 4.0
MIN_SIZE_M = 1.0
MAX_SIZE_M = 3.0
FILL_INTERVAL_M = 0.5
SCATTER_GAP_M = 0.4
AREA_SIDE_M = 6.0
MAX_SEED = 2**16
FILL_PATTERNS: tuple[FillPattern, ...] = ("grid", "offset", "hex")
SVG_TRIANGLE = "M 0 0 L 4 0 L 2 3 Z"
FORM_FNS = ("rose", "sine_wave", "spiral")
MIN_MEMBERS = 2
FORM_KINDS: tuple[FormKind, ...] = (
    "line", "arc", "curve", "circle", "block", "wedge", "polyline", "svg", "fill", "scatter",
    "custom", "generated", "text",
)  # fmt: skip

sizes = st.floats(min_value=MIN_SIZE_M, max_value=MAX_SIZE_M, allow_nan=False)
spacings = st.sampled_from((SpacingSpec(), SpacingSpec(mode="proportional")))


@dataclass(frozen=True)
class FormDraft:
    """Holds what every form builder needs before it draws its own geometry.

    Attributes:
        form_id: ID of the new form.
        members: Spots in slot order.
        center: Point the form is built around.
        set_id: Set the form belongs to.
        facing: Facing spec, or None for the kind's default.
    """

    form_id: str
    members: list[str]
    center: Point
    set_id: str
    facing: FacingSpec | None


type FormBuilder = Callable[[st.DrawFn, FormDraft], Form]


@st.composite
def form_facings(draw: st.DrawFn, sheet: Sheet) -> FacingSpec:
    """Draws a FacingSpec in any mode, with a value where the mode needs or allows one."""
    mode = draw(
        st.sampled_from(("fixed", "audience", "toward_point", "along", "normal", "outward"))
    )
    if mode == "fixed":
        return FacingSpec(mode="fixed", value=draw(headings()))
    if mode == "toward_point":
        return FacingSpec(mode="toward_point", value=draw(points_on(sheet)))
    return FacingSpec(mode=mode)


def _offset(c: Point, dx: float, dy: float) -> Point:
    return (c[0] + dx, c[1] + dy)


def _line(draw: st.DrawFn, d: FormDraft) -> Form:
    half = draw(sizes)
    a, b = _offset(d.center, -half, 0), _offset(d.center, half, 0)
    return line_form(d.form_id, d.members, a, b, draw(spacings), d.facing, set_id=d.set_id)


def _arc(draw: st.DrawFn, d: FormDraft) -> Form:
    sweep = draw(st.sampled_from((-270.0, -90.0, 90.0, 180.0)))
    radius, start = draw(sizes), draw(headings())
    return arc_form(
        d.form_id, d.members, d.center, radius, start, sweep, draw(spacings), d.facing,
        set_id=d.set_id,
    )  # fmt: skip


def _curve(draw: st.DrawFn, d: FormDraft) -> Form:
    r = draw(sizes)
    pts = (_offset(d.center, -r, 0), _offset(d.center, 0, r), _offset(d.center, r, 0))
    closed = draw(st.booleans())
    return curve_form(d.form_id, d.members, *pts, closed=closed, facing=d.facing, set_id=d.set_id)


def _circle(draw: st.DrawFn, d: FormDraft) -> Form:
    radius, start = draw(sizes), draw(headings())
    return circle_form(
        d.form_id, d.members, d.center, radius, start, None, d.facing, set_id=d.set_id
    )


def _block(draw: st.DrawFn, d: FormDraft) -> Form:
    n = len(d.members)
    rows = draw(st.sampled_from([k for k in range(1, n + 1) if n % k == 0]))
    return block_form(
        d.form_id, d.members, d.center, rows, n // rows, FILL_INTERVAL_M, draw(headings()),
        facing=d.facing, set_id=d.set_id,
    )  # fmt: skip


def _wedge(draw: st.DrawFn, d: FormDraft) -> Form:
    spread = draw(st.sampled_from((45.0, 90.0, 120.0)))
    length, heading = draw(sizes), draw(headings())
    return wedge_form(
        d.form_id, d.members, d.center, length, spread, heading, facing=d.facing, set_id=d.set_id
    )


def _polyline(draw: st.DrawFn, d: FormDraft) -> Form:
    r = draw(sizes)
    pts = (_offset(d.center, -r, -r), _offset(d.center, r, -r), _offset(d.center, r, r))
    closed = draw(st.booleans())
    return polyline_form(
        d.form_id, d.members, *pts, closed=closed, facing=d.facing, set_id=d.set_id
    )


def _svg(draw: st.DrawFn, d: FormDraft) -> Form:
    r = draw(sizes)
    fit = Box2(min=_offset(d.center, -r, -r), max=_offset(d.center, r, r))
    keep = draw(st.booleans())
    return svg_form(
        d.form_id, d.members, SVG_TRIANGLE, fit, keep_aspect=keep, facing=d.facing, set_id=d.set_id
    )


def _fill(draw: st.DrawFn, d: FormDraft) -> Form:
    pattern = draw(st.sampled_from(FILL_PATTERNS))
    area = rect(d.center, AREA_SIDE_M, AREA_SIDE_M)
    return fill_form(
        d.form_id, d.members, area, FILL_INTERVAL_M, pattern, draw(headings()), d.facing,
        set_id=d.set_id,
    )  # fmt: skip


def _scatter(draw: st.DrawFn, d: FormDraft) -> Form:
    area = rect(d.center, AREA_SIDE_M, AREA_SIDE_M)
    seed = draw(st.integers(0, MAX_SEED))
    return scatter_form(d.form_id, d.members, area, SCATTER_GAP_M, seed, d.facing, set_id=d.set_id)


def _custom(draw: st.DrawFn, d: FormDraft) -> Form:
    n = len(d.members)
    points = [_offset(d.center, FILL_INTERVAL_M * i, 0) for i in range(n)]
    facings = draw(st.none() | st.lists(headings(), min_size=n, max_size=n))
    return custom_form(d.form_id, d.members, points, facings, set_id=d.set_id)


def _generated(draw: st.DrawFn, d: FormDraft) -> Form:
    fn = draw(st.sampled_from(FORM_FNS))
    anchor = [d.center[0], d.center[1]]
    if fn == "sine_wave":
        return generated_form(
            d.form_id, d.members, fn, facing=d.facing, set_id=d.set_id, start=anchor
        )
    return generated_form(d.form_id, d.members, fn, facing=d.facing, set_id=d.set_id, center=anchor)


def _text(draw: st.DrawFn, d: FormDraft) -> Form:
    return text_form(
        d.form_id, d.members, "-", fonts=["simplex"], height=draw(sizes), at=d.center,
        facing=d.facing, set_id=d.set_id, n=len(d.members),
    )  # fmt: skip


BUILDERS: dict[FormKind, FormBuilder] = {
    "line": _line, "arc": _arc, "curve": _curve, "circle": _circle, "block": _block,
    "wedge": _wedge, "polyline": _polyline, "svg": _svg, "fill": _fill, "scatter": _scatter,
    "custom": _custom, "generated": _generated, "text": _text,
}  # fmt: skip


@st.composite
def forms_of(
    draw: st.DrawFn, kind: FormKind, form_id: str, members: Sequence[str], sheet: Sheet, set_id: str
) -> Form:
    """Draws a `kind` form for `members` in set `set_id`, centered on the sheet.

    Args:
        draw: Hypothesis draw function.
        kind: Form kind to build.
        form_id: ID of the new form.
        members: Spots in slot order; the form has exactly one slot per member.
        sheet: Sheet the form stands on.
        set_id: Set the form belongs to.
    """
    center = draw(points_on(sheet, FORM_MARGIN_M))
    facing = draw(st.none() | form_facings(sheet))
    return BUILDERS[kind](draw, FormDraft(form_id, list(members), center, set_id, facing))


@st.composite
def with_form(draw: st.DrawFn, show: Show, set_id: str, form_id: str) -> Show:
    """Builds a form of a drawn kind on some spots of set `set_id`, places it and stores it.

    Returns `show` unchanged when the cast is too small for a form.
    """
    ids = show.cast.ids()
    if len(ids) < MIN_MEMBERS:
        return show
    members = draw(st.lists(st.sampled_from(ids), min_size=MIN_MEMBERS, unique=True))
    kind = draw(st.sampled_from(FORM_KINDS))
    form = draw(forms_of(kind, form_id, members, show.sheet, set_id))
    placed_set, (filled,) = place(show, set_id, [form])
    return apply_change(with_set(show, placed_set), [FormOp(form=filled)])
