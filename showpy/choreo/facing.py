"""
facing.py --- facing tools for sets, plus timed facing overrides on a show

The face_* tools edit placements in one Set. Facing over a time range goes through
add_facing_override, which the PathEngine applies on top of paths (ADR 0001 item 14).

Contains:
    face_audience(): faces spots toward the audience (0°)
    face_direction(): gives spots a fixed facing
    face_point(): faces spots toward a point
    face_entity(): faces spots toward another spot's placement in the same set
    face_along(): faces form members along the form's curve
    face_normal(): faces form members perpendicular to the form's curve
    add_facing_override(): forces a facing on spots over a global count range
    turn(): interpolates a facing in a chosen direction
"""

import itertools
import math
from collections.abc import Sequence

from showpy.choreo.models import FacingOverride, Form, Set
from showpy.choreo.motion import turn
from showpy.core.errors import GeometryError, TimeRangeError, UnknownRefError
from showpy.core.ids import FormId, SetId, SpotId
from showpy.core.selection import Selection
from showpy.core.types import Degrees, Point
from showpy.core.units import normalize_angle, vector_facing
from showpy.core.util import close_matches
from showpy.geo.curves import AnyCurve, curve_from_construction
from showpy.show.models import Show

__all__ = [
    "add_facing_override",
    "face_along",
    "face_audience",
    "face_direction",
    "face_entity",
    "face_normal",
    "face_point",
    "turn",
]

AUDIENCE = 0.0
HALF_TURN = 180.0
QUARTER_TURN = 90.0
ZERO_TOL = 1e-9

type Sel = Sequence[SpotId] | Selection


def _set_facings(target: Set, facings: dict[SpotId, Degrees], force: bool) -> Set:
    updates = {e: {"facing": normalize_angle(f)} for e, f in facings.items()}
    return target.with_placements(updates, force=force)


def face_direction(target: Set, sel: Sel, deg: Degrees, force: bool = False) -> Set:
    """Gives spots a fixed facing; pinned spots raise LockedError unless forced.

    >>> from showpy.examples import two_set_show
    >>> face_direction(two_set_show().set("1"), ["T1"], 90.0).get("T1").facing
    90.0
    """
    return _set_facings(target, dict.fromkeys(sel, deg), force)


def face_audience(target: Set, sel: Sel, force: bool = False) -> Set:
    """Faces spots toward the audience.

    >>> from showpy.examples import two_set_show
    >>> face_audience(two_set_show().set("1"), ["T1"]).get("T1").facing
    0.0
    """
    return face_direction(target, sel, AUDIENCE, force)


def _toward(origin: Point, point: Point, fallback: Degrees) -> Degrees:
    dx, dy = point[0] - origin[0], point[1] - origin[1]
    return fallback if math.hypot(dx, dy) < ZERO_TOL else vector_facing((dx, dy))


def face_point(target: Set, sel: Sel, point: Point, force: bool = False) -> Set:
    """Faces spots toward a point; a spot on the point keeps its facing.

    >>> from showpy.examples import two_set_show
    >>> face_point(two_set_show().set("1"), ["T1"], (5.0, 5.0)).get("T1").facing
    90.0
    """
    facings = {e: _toward(target.get(e).point, point, target.get(e).facing) for e in sel}
    return _set_facings(target, facings, force)


def face_entity(target: Set, sel: Sel, entity: SpotId, force: bool = False) -> Set:
    """Faces spots toward another spot's placement in the same set.

    Over time, use a TOWARD_ENTITY path facing or override, which tracks the spot as it moves.

    >>> from showpy.examples import two_set_show
    >>> face_entity(two_set_show().set("1"), ["T1"], "T2").get("T1").facing
    90.0
    """
    return face_point(target, sel, target.get(entity).point, force)


def _form(show: Show, form_id: FormId) -> Form:
    if form_id not in show.forms:
        close = close_matches(form_id, show.forms)
        raise UnknownRefError(
            f"unknown form {form_id!r}",
            hint=f"did you mean {', '.join(close)}?" if close else "check the form id",
            context={"form": form_id},
        )
    return show.forms[form_id]


def _form_curve(form: Form) -> AnyCurve:
    recipe = form.geometry.get("curve")
    if not isinstance(recipe, dict):
        raise GeometryError(
            f"form {form.id} ({form.kind}) has no curve to face along",
            hint="use face_direction or face_point for blocks, fills and scatters",
            context={"form": form.id},
        )
    return curve_from_construction(recipe)


def _tangent_facings(
    show: Show, set_id: SetId, form_id: FormId, turn_by: Degrees
) -> dict[str, Degrees]:
    form = _form(show, form_id)
    curve = _form_curve(form)
    target = show.set(set_id)
    facings = {}
    for member in form.members:
        tangent = curve.tangent_at(curve.project(target.get(member).point))
        base = vector_facing((float(tangent[0]), float(tangent[1])))
        facings[member] = base + turn_by
    return facings


def face_along(
    show: Show, set_id: SetId, form_id: FormId, reverse: bool = False, force: bool = False
) -> Set:
    """Faces form members along the form's curve, in travel direction unless reversed.

    >>> from showpy.examples import two_set_show
    >>> from showpy.geo.shapes import line
    >>> show = two_set_show()
    >>> recipe = {"curve": line((0, 5), (6, 5)).construction()}
    >>> form = Form(id="f", set="1", kind="line", members=("T1",), geometry=recipe)
    >>> show = show.replace(forms={"f": form})
    >>> round(face_along(show, "1", "f").get("T1").facing)
    90
    """
    facings = _tangent_facings(show, set_id, form_id, HALF_TURN if reverse else 0.0)
    return _set_facings(show.set(set_id), facings, force)


def face_normal(
    show: Show, set_id: SetId, form_id: FormId, outward: bool = True, force: bool = False
) -> Set:
    """Faces form members perpendicular to the curve; outward is the right of travel.

    For a counter-clockwise circle the right of travel points away from the center.

    >>> from showpy.examples import two_set_show
    >>> from showpy.geo.shapes import line
    >>> show = two_set_show()
    >>> recipe = {"curve": line((0, 5), (6, 5)).construction()}
    >>> form = Form(id="f", set="1", kind="line", members=("T1",), geometry=recipe)
    >>> show = show.replace(forms={"f": form})
    >>> round(face_normal(show, "1", "f").get("T1").facing)
    0
    """
    turn_by = -QUARTER_TURN if outward else QUARTER_TURN
    facings = _tangent_facings(show, set_id, form_id, turn_by)
    return _set_facings(show.set(set_id), facings, force)


def add_facing_override(show: Show, override: FacingOverride) -> Show:
    """Adds a timed facing override; its range must lie within the show's counts.

    >>> from showpy.examples import two_set_show
    >>> from showpy.choreo.models import FacingMode
    >>> o = FacingOverride(entities="all", mode=FacingMode.AUDIENCE, start=0.0, end=4.0)
    >>> len(add_facing_override(two_set_show(), o).facing_overrides)
    1
    """
    total = sum(s.counts for s in itertools.islice(show.sets, 1, None))
    if override.start < 0 or override.end > total:
        raise TimeRangeError(
            f"override {override.start}–{override.end} is outside the show (0–{total})",
            hint=f"keep the range within 0–{total} counts",
            context={"start": override.start, "end": override.end, "total": total},
        )
    return show.replace(facing_overrides=(*show.facing_overrides, override))
