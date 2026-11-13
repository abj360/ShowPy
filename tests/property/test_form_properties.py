#!/usr/bin/env python3

"""
test_form_properties.py --- checks even spacing, rotation identities and split/merge invariants
"""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.choreo.form_ops import gate, merge_forms, split_form, transform_form
from showpy.choreo.forms import arc_form, circle_form, line_form, placements, slots
from showpy.choreo.models import Form
from showpy.core.types import Points
from showpy.core.util import approx_equal
from showpy.geo.affine import rotation_matrix

TOL = 1e-6
coords = st.floats(min_value=-20.0, max_value=20.0)
counts = st.integers(min_value=2, max_value=12)
radii = st.floats(min_value=0.5, max_value=15.0)
angles = st.floats(min_value=-720.0, max_value=720.0)
sweeps = st.floats(min_value=10.0, max_value=350.0)


@st.composite
def curve_forms(draw: st.DrawFn) -> Form:
    n = draw(counts)
    members = [f"S{i}" for i in range(n)]
    kind = draw(st.sampled_from(["line", "arc", "circle"]))
    center = (draw(coords), draw(coords))
    if kind == "line":
        end = (center[0] + draw(radii), center[1] + draw(coords))
        return line_form("f", members, center, end, set_id="1")
    if kind == "arc":
        return arc_form("f", members, center, draw(radii), draw(angles), draw(sweeps), set_id="1")
    return circle_form("f", members, center, draw(radii), draw(angles), set_id="1")


def _chords(pts: Points) -> Points:
    return np.linalg.norm(np.diff(pts, axis=0), axis=1)


@settings(max_examples=60, deadline=None)
@given(curve_forms())
def test_count_spacing_is_even(form: Form) -> None:
    chords = _chords(slots(form))
    assert approx_equal(chords, np.full(len(chords), chords[0]), TOL)


@settings(max_examples=60, deadline=None)
@given(curve_forms(), angles)
def test_gate_then_ungate_is_identity(form: Form, deg: float) -> None:
    back = gate(gate(form, "start", deg), "start", -deg)
    assert approx_equal(slots(back), slots(form), TOL)


@settings(max_examples=60, deadline=None)
@given(curve_forms(), angles, coords, coords)
def test_rotation_moves_every_slot_rigidly(form: Form, deg: float, x: float, y: float) -> None:
    turned = slots(transform_form(form, rotation_matrix(deg, (x, y))))
    original = slots(form)
    d_before = np.linalg.norm(original[:, None] - original[None], axis=2)
    d_after = np.linalg.norm(turned[:, None] - turned[None], axis=2)
    assert approx_equal(d_after, d_before, TOL)


@settings(max_examples=60, deadline=None)
@given(curve_forms(), st.data())
def test_split_then_merge_keeps_every_placement(form: Form, data: st.DataObject) -> None:
    at = data.draw(st.integers(min_value=1, max_value=len(form.members) - 1))
    first, second = split_form(form, at)
    merged = merge_forms(first, second, "m")
    assert merged.members == form.members
    before = [(p.x, p.y) for p in placements(form)]
    after = [(p.x, p.y) for p in placements(merged)]
    assert approx_equal(after, before, TOL)
