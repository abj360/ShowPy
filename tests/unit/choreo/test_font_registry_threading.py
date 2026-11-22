#!/usr/bin/env python3

"""
test_font_registry_threading.py --- tests that a user font reaches every call that rebuilds a form

Each test registers the font only in the caller's registry, so a call that drops
`font_registry` raises MissingFontError and a call that threads it works.

Contains:
    test_match_takes_the_font_registry: matches spots into a user-font text form's slots
    test_cost_matrix_takes_the_font_registry: costs spots against a user-font text form's slots
    test_match_by_order_takes_the_font_registry: matches by order onto a user-font text form
    test_build_problem_takes_the_font_registry: builds a problem from a user-font text form
    test_reorder_takes_the_font_registry: reorders a user-font text form by position
    test_gate_takes_the_font_registry: rotates a user-font text form about a slot
    test_split_form_takes_the_font_registry: splits a user-font text form into custom parts
    test_merge_forms_takes_the_font_registry: merges user-font text forms into a custom form
    test_morph_targets_takes_the_font_registry: reads where members stand in a user-font form
    test_place_takes_the_font_registry: places a set from a user-font text form
    test_rebuild_takes_the_font_registry: regenerates a user-font text form's placements
    test_apply_match_takes_the_font_registry: writes a match onto a user-font text form
    test_swap_needs_no_font_registry: swaps spots of a user-font text form without its font
"""

import numpy as np
import pytest

from showpy.choreo.form_models import Form
from showpy.choreo.form_ops import gate, merge_forms, morph_targets, reorder, split_form
from showpy.choreo.form_place import place, rebuild
from showpy.choreo.forms import slot_poses, text_form
from showpy.choreo.match import cost_matrix, match, match_by_order
from showpy.choreo.match_cost import build_problem
from showpy.choreo.match_ops import apply_match, swap, with_set
from showpy.core.errors import MissingFontError
from showpy.core.ids import FormId, SpotId
from showpy.geo.fonts import FontRegistry, bundled_fonts, register_font
from showpy.show.models import Show
from tests.fixtures.fonts import tiny_bytes
from tests.fixtures.shows import two_set_show

TURN = 90.0
MEMBERS = ["T1", "T2", "T3"]


def _tiny(form_id: str = "t", members: list[str] | None = None, set_id: str = "") -> Form:
    registry, uri = _registry_and_uri()
    return text_form(form_id, members or MEMBERS, "I", fonts=[uri], height=2.0, at=(1, 1), n=3,
                     set_id=set_id, font_registry=registry)  # fmt: skip


def _registry_and_uri() -> tuple[FontRegistry, str]:
    return register_font(bundled_fonts(), tiny_bytes())


def _registry() -> FontRegistry:
    return _registry_and_uri()[0]


def _stored_show() -> Show:
    form = _tiny(set_id="2")
    placed, (filled,) = place(two_set_show(), "2", [form], font_registry=_registry())
    return with_set(two_set_show(), placed).replace(forms={"t": filled})


def test_match_takes_the_font_registry() -> None:
    form = _tiny()
    result = match(two_set_show().set("1"), [form], entities=MEMBERS, font_registry=_registry())
    assert sorted(result.slots.values()) == [(FormId("t"), i) for i in range(3)]
    with pytest.raises(MissingFontError):
        match(two_set_show().set("1"), [form], entities=MEMBERS)


def test_cost_matrix_takes_the_font_registry() -> None:
    form = _tiny()
    cost = cost_matrix(two_set_show().set("1"), [form], entities=MEMBERS, font_registry=_registry())
    assert cost.shape == (3, 3)
    with pytest.raises(MissingFontError):
        cost_matrix(two_set_show().set("1"), [form], entities=MEMBERS)


def test_match_by_order_takes_the_font_registry() -> None:
    form = _tiny()
    result = match_by_order(MEMBERS, form, font_registry=_registry())
    assert result.slots[SpotId("T3")] == (FormId("t"), 2)
    with pytest.raises(MissingFontError):
        match_by_order(MEMBERS, form)


def test_build_problem_takes_the_font_registry() -> None:
    form = _tiny()
    problem = build_problem(
        two_set_show().set("1"), [form], MEMBERS, None, None, font_registry=_registry()
    )
    assert problem.slots.shape == (3, 2)
    with pytest.raises(MissingFontError):
        build_problem(two_set_show().set("1"), [form], MEMBERS, None, None)


def test_reorder_takes_the_font_registry() -> None:
    form = _tiny()
    reordered = reorder(form, "by_x", font_registry=_registry())
    assert sorted(reordered.members, key=str) == sorted(form.members, key=str)
    with pytest.raises(MissingFontError):
        reorder(form, "by_x")
    with pytest.raises(MissingFontError):
        reorder(form, "nearest_prev", two_set_show().set("1"))


def test_gate_takes_the_font_registry() -> None:
    form = _tiny()
    gated = gate(form, "start", TURN, font_registry=_registry())
    assert gated.members == form.members
    with pytest.raises(MissingFontError):
        gate(form, "start", TURN)


def test_split_form_takes_the_font_registry() -> None:
    form = _tiny()
    first, second = split_form(form, 1, font_registry=_registry())
    assert (first.members, second.members) == (("T1",), ("T2", "T3"))
    with pytest.raises(MissingFontError):
        split_form(form, 1)


def test_merge_forms_takes_the_font_registry() -> None:
    a, b = _tiny("a", ["T1", "T2", "T3"]), _tiny("b", ["T4", "T5", "T6"])
    merged = merge_forms(a, b, "ab", font_registry=_registry())
    assert len(merged.members) == 6
    with pytest.raises(MissingFontError):
        merge_forms(a, b, "ab")


def test_morph_targets_takes_the_font_registry() -> None:
    a, b = _tiny("a"), _tiny("b", ["T3", "T2", "T1"])
    registry = _registry()
    moved = morph_targets(a, b, font_registry=registry)
    expected = slot_poses(b, font_registry=registry)[0][[2, 1, 0]]
    assert np.array_equal(moved, expected)
    with pytest.raises(MissingFontError):
        morph_targets(a, b)


def test_place_takes_the_font_registry() -> None:
    form = _tiny()
    placed, (filled,) = place(two_set_show(), "2", [form], font_registry=_registry())
    assert [p.form for p in placed.placements if p.entity in MEMBERS] == ["t"] * 3
    assert filled.members == form.members
    with pytest.raises(MissingFontError):
        place(two_set_show(), "2", [form])


def test_rebuild_takes_the_font_registry() -> None:
    show = two_set_show().replace(forms={"t": _tiny(set_id="2")})
    rebuilt = rebuild(show, "t", font_registry=_registry())
    assert rebuilt.set("2").get("T1").form == "t"
    with pytest.raises(MissingFontError):
        rebuild(show, "t")


def test_apply_match_takes_the_font_registry() -> None:
    show = _stored_show()
    registry = _registry()
    result = match(
        show.set("1"), [show.forms[FormId("t")]], entities=MEMBERS, font_registry=registry
    )
    applied = apply_match(show, ("1", "2"), result, font_registry=registry)
    assert applied.set("2").get("T1").form == "t"
    with pytest.raises(MissingFontError):
        apply_match(show, ("1", "2"), result)


def test_swap_needs_no_font_registry() -> None:
    show = _stored_show()
    swapped = swap(show, "2", "T1", "T2")
    assert swapped.forms[FormId("t")].members[:2] == ("T2", "T1")
