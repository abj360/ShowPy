#!/usr/bin/env python3

"""
test_facing.py --- tests set facing tools and timed facing overrides
"""

import pytest

from stage.choreo.facing import (
    add_facing_override,
    face_along,
    face_audience,
    face_direction,
    face_entity,
    face_normal,
    face_point,
    turn,
)
from stage.choreo.models import FacingMode, FacingOverride, Form
from stage.core.errors import GeometryError, LockedError, TimeRangeError, UnknownRefError
from stage.core.util import approx_equal
from stage.geo.shapes import circle, line
from stage.show.models import Show
from tests.fixtures.motion_shows import with_targets
from tests.fixtures.shows import two_set_show


def test_face_audience_and_direction() -> None:
    s = face_direction(two_set_show().set("1"), ["T1", "T2"], 90.0)
    assert s.get("T1").facing == 90.0
    assert face_audience(s, ["T1"]).get("T1").facing == 0.0
    assert s.get("T3").facing == 0.0


def test_face_point_and_entity() -> None:
    s = two_set_show().set("1")
    assert approx_equal(face_point(s, ["T1"], (0.0, 10.0)).get("T1").facing, 180.0)
    assert approx_equal(face_entity(s, ["T1", "T2"], "T4").get("T1").facing, 90.0)
    assert face_entity(s, ["T4"], "T4").get("T4").facing == 0.0


def test_face_tools_respect_pins() -> None:
    pinned = with_targets({"T1": {"pinned": True}}).set("2")
    with pytest.raises(LockedError):
        face_audience(pinned, ["T1"])
    assert face_direction(pinned, ["T1"], 45.0, force=True).get("T1").facing == 45.0


def _show_with_line_form() -> Show:
    recipe = {"curve": line((0.0, 5.0), (6.0, 5.0)).construction()}
    form = Form(id="f", set="1", kind="line", geometry=recipe, members=("T1", "T2", "T3", "T4"))
    return two_set_show().model_copy(update={"forms": {"f": form}})


def test_face_along_line_points_down_the_line() -> None:
    show = _show_with_line_form()
    s = face_along(show, "1", "f")
    assert approx_equal(s.get("T2").facing, 90.0)
    assert approx_equal(face_along(show, "1", "f", reverse=True).get("T2").facing, 270.0)


def test_face_normal_on_circle_outward_and_inward() -> None:
    show = two_set_show()
    ring = circle((3.0, 5.0), 3.0, start_deg=180.0)
    form = Form(
        id="c", set="1", kind="circle", geometry={"curve": ring.construction()}, members=("T1",)
    )
    show = show.model_copy(update={"forms": {"c": form}})
    assert approx_equal(face_normal(show, "1", "c").get("T1").facing, 270.0, tol=1e-6)
    assert approx_equal(face_normal(show, "1", "c", outward=False).get("T1").facing, 90.0, tol=1e-6)


def test_face_along_needs_a_curve_form() -> None:
    show = two_set_show()
    form = Form(id="b", set="1", kind="block", geometry={"origin": [0, 0]}, members=("T1",))
    show = show.model_copy(update={"forms": {"b": form}})
    with pytest.raises(GeometryError):
        face_along(show, "1", "b")
    with pytest.raises(UnknownRefError):
        face_along(show, "1", "zz")


def test_add_facing_override_validates_range() -> None:
    override = FacingOverride(entities=("T1",), mode=FacingMode.AUDIENCE, start=1.0, end=3.0)
    show = add_facing_override(two_set_show(), override)
    assert show.facing_overrides == (override,)
    late = FacingOverride(entities=("T1",), mode=FacingMode.AUDIENCE, start=1.0, end=30.0)
    with pytest.raises(TimeRangeError):
        add_facing_override(two_set_show(), late)


def test_turn_is_reexported() -> None:
    assert turn(0.0, 90.0, 0.5) == 45.0
