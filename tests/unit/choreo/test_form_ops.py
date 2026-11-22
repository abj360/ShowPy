#!/usr/bin/env python3

"""
test_form_ops.py --- tests form edits, reordering, split and merge, morphs and exact transforms

Contains:
    test_regression_edit_block_rows_updates_slot_count: keeps a block's slot count in step with its
        edited rows
    test_regression_edit_custom_points_updates_slot_count: keeps a custom form's slot count in step
        with its edited points
    test_regression_edit_n_none_clears_slot_count_on_curve_forms: clears a curve form's n when
        edit(n=None) asks for it
    test_regression_edit_rejects_more_members_than_slots: rejects members beyond the form's slots
        instead of accepting them
    test_regression_transform_form_skewed_circle_slots_map_exactly: places a skewed circle's slots
        exactly where the matrix maps them
    test_regression_transform_form_scales_windowed_spacing: scales a spacing window with a uniform
        scale of the form
    test_regression_transform_form_scales_interval_spacing: keeps the slot count of an
        interval-spaced line under a uniform scale
    test_regression_transform_form_maps_audience_facings: maps audience facings through the matrix
        like fixed ones
    test_regression_morph_targets_rejects_members_without_slots: raises a ShowPyError, not
        IndexError, when the target has fewer slots
    test_regression_reorder_by_x_rejects_members_without_slots: raises a ShowPyError, not
        ValueError, when members outnumber slots
    test_regression_edit_rejects_non_numeric_radius_with_show_py_error: raises a ShowPyError, not a
        pydantic error, for edit(radius="abc")
    test_edit_text_keeps_members_in_reading_order: regenerates the letters on the same members
    test_edit_text_too_long_for_the_count_names_the_minimum: names the minimum count for a long text
    test_edit_text_keeps_the_derived_count: keeps n, spacing and members of a spacing-built form
    test_edit_text_resolves_font_names: stores font URIs when edit gets font names
    test_edit_text_suggests_the_field_for_a_typo: names the near field for a misspelled key
    test_edit_locked_text_form_needs_force: refuses a locked text form without force
    test_regression_edit_text_form_rejects_count_changes: sends n and spacing edits to
        edit_text_count
    test_regression_edit_text_form_accepts_text_n_free_edits: edits text, layout and transform
        fields without a raw KeyError
    test_regression_edit_text_form_rejects_a_member_count_change: keeps a text form from gaining
        open slots
    test_edit_text_form_allows_reordering_members: swaps members at the same count
    test_edit_text_with_a_user_font_takes_the_registry: resolves user fonts through the registry
    test_regression_edit_text_too_few_spots_hint_names_edit_text_count: says how many spots to
        add with edit_text_count when new text needs more than the form has
    test_regression_edit_text_sparse_warning_names_edit_text_count: points the legibility
        warning at edit_text_count, since edit cannot change n
"""

from typing import Any

import numpy as np
import pytest

from showpy.choreo.form_layout import slot_count
from showpy.choreo.form_models import FacingSpec, Form, SpacingSpec
from showpy.choreo.form_ops import (
    edit,
    form_templates,
    gate,
    merge_forms,
    morph_targets,
    pinwheel,
    reorder,
    split_form,
    transform_form,
)
from showpy.choreo.form_text import text_recipe, text_strokes
from showpy.choreo.forms import (
    arc_form,
    block_form,
    circle_form,
    custom_form,
    fill_form,
    generated_form,
    line_form,
    placements,
    scatter_form,
    slot_poses,
    slots,
    text_form,
)
from showpy.choreo.models import Placement, Set
from showpy.choreo.text_count import spacing_count
from showpy.core.errors import (
    LegibilityWarning,
    LockedError,
    MissingFontError,
    SchemaError,
    ShowPyError,
    SpacingError,
    UnknownRefError,
)
from showpy.core.ids import SetId, SpotId
from showpy.core.types import Points
from showpy.core.util import approx_equal
from showpy.geo.affine import (
    Matrix,
    mirror_matrix,
    rotation_matrix,
    scale_matrix,
    skew_matrix,
    transform_facing,
    transform_points,
)
from showpy.geo.fonts import bundled_fonts, register_font
from tests.fixtures.fonts import tiny_bytes

TOL = 1e-6
MEMBERS = ("A", "B", "C", "D")
SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4)]


def _locked(form: Form) -> Form:
    return form.model_copy(update={"locked": True})


def _prev(**points: tuple[float, float]) -> Set:
    rows = tuple(Placement(entity=SpotId(e), x=x, y=y) for e, (x, y) in points.items())
    return Set(id=SetId("0"), counts=0.0, placements=rows)


def test_edit_changes_construction_and_form_fields() -> None:
    form = circle_form("c", MEMBERS, (0, 5), 2.0)
    bigger = edit(form, radius=3.0, members=("D", "C", "B", "A"))
    assert approx_equal(np.linalg.norm(slots(bigger) - [0, 5], axis=1), [3.0] * 4)
    assert bigger.members == ("D", "C", "B", "A")
    assert form.recipe.model_dump(mode="json")["curve"]["radius"] == 2.0


def test_edit_block_recipe_and_slot_count() -> None:
    form = block_form("b", [], (0, 5), 2, 2, 1.0)
    assert len(slots(edit(form, rows=3, n=6))) == 6


def test_edit_rejects_unknown_fields_and_bad_shapes() -> None:
    form = circle_form("c", MEMBERS, (0, 5), 2.0)
    with pytest.raises(SchemaError, match="radius"):
        edit(form, radiu=3.0)
    with pytest.raises(Exception, match="radius"):
        edit(form, radius=-1.0)


def test_edit_respects_lock() -> None:
    form = _locked(line_form("f", MEMBERS, (0, 0), (3, 0)))
    with pytest.raises(LockedError):
        edit(form, b=(6.0, 0.0))
    assert slots(edit(form, force=True, b=(6.0, 0.0)))[-1].tolist() == [6.0, 0.0]


def test_reorder_reverse_and_explicit() -> None:
    form = line_form("f", MEMBERS, (0, 0), (3, 0))
    assert reorder(form, "reverse").members == ("D", "C", "B", "A")
    assert reorder(form, ["B", "A", "D", "C"]).members == ("B", "A", "D", "C")
    with pytest.raises(SchemaError):
        reorder(form, ["A", "B"])
    with pytest.raises(LockedError):
        reorder(_locked(form), "reverse")


def test_reorder_by_x_uses_previous_positions() -> None:
    form = line_form("f", ("A", "B", "C"), (4, 5), (0, 5))
    prev = _prev(A=(9.0, 0.0), B=(1.0, 0.0), C=(5.0, 0.0))
    reordered = reorder(form, "by_x", prev)
    xs: dict[str, float] = {p.entity: p.x for p in placements(reordered)}
    assert xs["B"] < xs["C"] < xs["A"]


def test_reorder_by_y_without_previous_set_uses_ids() -> None:
    form = line_form("f", ("C", "A", "B"), (0, 4), (0, 0))
    reordered = reorder(form, "by_y")
    ys: dict[str, float] = {p.entity: p.y for p in placements(reordered)}
    assert ys["A"] < ys["B"] < ys["C"]


def test_reorder_nearest_prev_minimizes_moves() -> None:
    form = line_form("f", ("A", "B"), (0, 1), (10, 1))
    prev = _prev(A=(10.0, 0.0), B=(0.0, 0.0))
    assert reorder(form, "nearest_prev", prev).members == ("B", "A")
    with pytest.raises(SchemaError):
        reorder(form, "nearest_prev")
    with pytest.raises(UnknownRefError):
        reorder(line_form("f", ("A", "Z"), (0, 1), (1, 1)), "nearest_prev", prev)


def test_split_keeps_every_position_and_facing() -> None:
    for form in (
        line_form("f", MEMBERS, (0, 0), (3, 0), facing=FacingSpec(mode="along")),
        circle_form("c", list("ABCDEF"), (0, 5), 3.0),
        arc_form("a", MEMBERS, (0, 0), 3.0, 0.0, 180.0, facing=FacingSpec(mode="normal")),
        block_form("b", MEMBERS, (0, 5), 2, 2, 1.0),
    ):
        before = {p.entity: p for p in placements(form)}
        first, second = split_form(form, 2)
        after = {p.entity: p for p in placements(first) + placements(second)}
        for entity, p in before.items():
            assert approx_equal(after[entity].point, p.point, abs_tol=TOL)
            assert approx_equal(after[entity].facing, p.facing, abs_tol=TOL)


def test_split_line_stays_a_curve_form_and_names_parts() -> None:
    first, second = split_form(line_form("f", MEMBERS, (0, 0), (3, 0)), "C", ids=("x", "y"))
    assert (first.id, second.id) == ("x", "y")
    assert first.kind == "line"
    assert second.members == ("C", "D")


def test_split_rejects_bad_index() -> None:
    with pytest.raises(SchemaError):
        split_form(line_form("f", MEMBERS, (0, 0), (3, 0)), 0)


def test_merge_keeps_positions() -> None:
    a = line_form("a", ("A", "B"), (0, 0), (1, 0), set_id="1")
    b = circle_form("b", ("C", "D"), (5, 5), 1.0, set_id="1")
    merged = merge_forms(a, b, "ab")
    assert merged.members == ("A", "B", "C", "D")
    before = [p.point for p in placements(a) + placements(b)]
    assert approx_equal([p.point for p in placements(merged)], before, abs_tol=TOL)
    assert [p.facing for p in placements(merged)][2:] == [p.facing for p in placements(b)]
    with pytest.raises(SchemaError):
        merge_forms(a, b.model_copy(update={"set": "2"}), "ab")


def test_morph_targets_follow_members() -> None:
    a = line_form("a", ("A", "B"), (0, 0), (1, 0))
    b = circle_form("b", ("B", "A"), (0, 5), 1.0)
    targets = morph_targets(a, b)
    assert approx_equal(targets, [[-1, 5], [1, 5]], abs_tol=TOL)
    with pytest.raises(SchemaError):
        morph_targets(a, line_form("c", ("A", "Z"), (0, 0), (1, 0)))


def _expected(form: Form, matrix: Matrix) -> tuple[Points, list[float]]:
    pts, facings = slot_poses(form)
    return transform_points(pts, matrix), [transform_facing(f, matrix) for f in facings]


@pytest.mark.parametrize(
    "matrix",
    [
        rotation_matrix(37.0, (1.0, 2.0)),
        mirror_matrix((0.0, 0.0), (1.0, 3.0)),
        scale_matrix(2.0, 2.0, (0.0, 5.0)),
    ],
)
def test_transform_maps_slots_and_facings_exactly(matrix: Matrix) -> None:
    forms = [
        line_form("l", MEMBERS, (0, 0), (3, 1), facing=FacingSpec(mode="normal")),
        arc_form("a", MEMBERS, (0, 0), 3.0, 10.0, 120.0, facing=FacingSpec(mode="along")),
        circle_form("c", MEMBERS, (0, 5), 2.0),
        block_form("b", MEMBERS, (0, 5), 2, 2, 1.0, direction_deg=20.0),
        fill_form("f", [], SQUARE, 1.0, facing=FacingSpec(mode="fixed", value=10.0)),
        custom_form("u", ("A", "B"), [(1, 2), (3, 4)], facings=[10.0, 200.0]),
        generated_form(
            "g", MEMBERS, "spiral", facing=FacingSpec(mode="toward_point", value=(0, 0))
        ),
    ]
    for form in forms:
        pts, facings = slot_poses(transform_form(form, matrix))
        want_pts, want_facings = _expected(form, matrix)
        assert approx_equal(pts, want_pts, abs_tol=TOL), form.kind
        diffs = (np.asarray(facings) - np.asarray(want_facings) + 180.0) % 360.0 - 180.0
        assert approx_equal(diffs, np.zeros(len(diffs)), abs_tol=TOL), form.kind


def test_transform_of_scatter_keeps_points_exact() -> None:
    form = scatter_form("s", MEMBERS, SQUARE, 1.0, seed=2)
    matrix = rotation_matrix(30.0, (0.0, 0.0))
    want, _ = _expected(form, matrix)
    assert approx_equal(slots(transform_form(form, matrix)), want, abs_tol=TOL)


def test_skewed_block_keeps_points_exact() -> None:
    form = block_form("b", MEMBERS, (0, 5), 2, 2, 1.0)
    matrix = skew_matrix(20.0, 0.0, (0.0, 0.0))
    skewed = transform_form(form, matrix)
    assert skewed.recipe.transform is not None
    assert approx_equal(slots(skewed), _expected(form, matrix)[0], abs_tol=TOL)


def test_rotated_block_keeps_an_editable_recipe() -> None:
    rotated = transform_form(block_form("b", [], (0, 5), 2, 2, 1.0), rotation_matrix(90, (0, 0)))
    assert rotated.recipe.transform is None
    assert approx_equal(rotated.recipe.model_dump(mode="json")["origin"], [-5.0, 0.0], abs_tol=TOL)


def test_gate_rotates_about_an_end() -> None:
    form = line_form("f", ("A", "B", "C"), (0, 0), (2, 0))
    end = gate(form, "end", 90.0)
    assert approx_equal(slots(end), [[2, -2], [2, -1], [2, 0]], abs_tol=TOL)
    middle = gate(form, 1, 180.0)
    assert approx_equal(slots(middle), [[2, 0], [1, 0], [0, 0]], abs_tol=TOL)
    with pytest.raises(SchemaError):
        gate(form, 7, 90.0)
    with pytest.raises(LockedError):
        gate(_locked(form), "start", 90.0)


def test_full_turn_gate_is_identity() -> None:
    form = arc_form("a", MEMBERS, (0, 0), 3.0, 10.0, 120.0)
    assert approx_equal(slots(gate(form, "start", 360.0)), slots(form), abs_tol=TOL)


def test_pinwheel_rotates_every_spoke() -> None:
    spokes = [line_form(f"s{i}", ("A",), (0, 1), (0, 3)) for i in range(2)]
    turned = pinwheel(spokes, (0.0, 0.0), -90.0)
    assert all(approx_equal(slots(f), [[2, 0]], abs_tol=TOL) for f in turned)


def test_form_templates_cover_every_kind() -> None:
    assert set(form_templates()) == {
        "line", "arc", "curve", "circle", "block", "wedge", "polyline", "svg", "fill",
        "scatter", "custom", "generated", "text",
    }  # fmt: skip


SLOT_TOL = 1e-6
SKEW_MATRIX = np.array([[1.0, 0.8, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
DOUBLE_MATRIX = np.diag([2.0, 2.0, 1.0])
QUARTER_TURN = 90.0
WINDOW_START = 2.0
WINDOW_END = 8.0
LINE_LENGTH = 30.0
SPOT_GAP = 6.0


def test_regression_edit_block_rows_updates_slot_count() -> None:
    """Keeps a block's slot count in step with its edited rows (audit 9.6)."""
    edited = edit(block_form("b", [], (0, 0), 2, 2, 1.0), rows=3)
    assert slot_count(edited) == len(slots(edited))


def test_regression_edit_custom_points_updates_slot_count() -> None:
    """Keeps a custom form's slot count in step with its edited points (audit 9.6)."""
    edited = edit(
        custom_form("c", [], [(0, 0), (1, 0), (2, 0)]), points=[(0, 0), (1, 0), (2, 0), (3, 0)]
    )
    assert slot_count(edited) == len(slots(edited))


def test_regression_edit_n_none_clears_slot_count_on_curve_forms() -> None:
    """Clears a curve form's n when edit(n=None) asks for it (audit 9.6)."""
    form = line_form("l", ["A", "B"], (0, 0), (4, 0), n=4)
    assert slot_count(edit(form, n=None)) == len(form.members)


def test_regression_edit_rejects_more_members_than_slots() -> None:
    """Rejects members beyond the form's slots instead of accepting them (audit 9.6)."""
    form = line_form("l", ["A"], (0, 0), (4, 0), n=2)
    with pytest.raises(ShowPyError):
        edit(form, members=("A", "B", "C"))


@pytest.mark.defect("UP-9.6-15")
@pytest.mark.xfail(strict=True, reason="UP-9.6-15")
def test_regression_transform_form_skewed_circle_slots_map_exactly() -> None:
    """Places a skewed circle's slots exactly where the matrix maps them (audit 9.6)."""
    circle = circle_form("c", ["A", "B", "C", "D"], (0, 5), 3.0)
    expected = transform_points(slots(circle), SKEW_MATRIX)
    assert approx_equal(slots(transform_form(circle, SKEW_MATRIX)), expected, abs_tol=SLOT_TOL)


@pytest.mark.defect("UP-9.6-16")
@pytest.mark.xfail(strict=True, reason="UP-9.6-16")
def test_regression_transform_form_scales_windowed_spacing() -> None:
    """Scales a spacing window with a uniform scale of the form (audit 9.6)."""
    spacing = SpacingSpec(start=WINDOW_START, end=WINDOW_END)
    form = line_form("l", ["A", "B", "C"], (0, 0), (10, 0), spacing=spacing)
    expected = slots(form) * 2.0
    assert approx_equal(slots(transform_form(form, DOUBLE_MATRIX)), expected, abs_tol=SLOT_TOL)


@pytest.mark.defect("UP-9.6-17")
@pytest.mark.xfail(strict=True, reason="UP-9.6-17")
def test_regression_transform_form_scales_interval_spacing() -> None:
    """Keeps the slot count of an interval-spaced line under a uniform scale (audit 9.6)."""
    spacing = SpacingSpec(mode="interval", interval=SPOT_GAP)
    form = line_form("l", [], (0, 0), (LINE_LENGTH, 0), spacing=spacing)
    assert len(slots(transform_form(form, DOUBLE_MATRIX))) == len(slots(form))


@pytest.mark.defect("UP-9.6-18")
@pytest.mark.xfail(strict=True, reason="UP-9.6-18")
def test_regression_transform_form_maps_audience_facings() -> None:
    """Maps audience facings through the matrix like fixed ones (audit 9.6)."""
    form = line_form("l", ["A", "B"], (0, 0), (4, 0))
    matrix = rotation_matrix(QUARTER_TURN, (0, 0))
    expected = [transform_facing(p.facing, matrix) for p in placements(form)]
    mapped = [p.facing for p in placements(transform_form(form, matrix))]
    assert approx_equal(mapped, expected, abs_tol=SLOT_TOL)


@pytest.mark.defect("UP-9.6-56")
@pytest.mark.xfail(strict=True, reason="UP-9.6-56")
def test_regression_morph_targets_rejects_members_without_slots() -> None:
    """Raises a ShowPyError, not IndexError, when the target has fewer slots (audit 9.6)."""
    source = line_form("a", ["A", "B"], (0, 0), (1, 0))
    target = line_form("b", ["B", "A"], (0, 5), (1, 5), n=1)
    with pytest.raises(ShowPyError):
        morph_targets(source, target)


@pytest.mark.defect("UP-9.6-57")
@pytest.mark.xfail(strict=True, reason="UP-9.6-57")
def test_regression_reorder_by_x_rejects_members_without_slots() -> None:
    """Raises a ShowPyError, not ValueError, when members outnumber slots (audit 9.6)."""
    form = line_form("l", ["A", "B", "C"], (0, 0), (4, 0), n=2)
    with pytest.raises(ShowPyError):
        reorder(form, "by_x")


def test_regression_edit_rejects_non_numeric_radius_with_show_py_error() -> None:
    """Raises a ShowPyError, not a pydantic error, for edit(radius="abc") (audit 9.6)."""
    with pytest.raises(ShowPyError):
        edit(circle_form("c", ["A", "B"], (0, 5), 2.0), radius="abc")


TEXT_MEMBERS = [f"S{i}" for i in range(24)]


def _text(members: list[str] = TEXT_MEMBERS, n: int = 24) -> Form:
    return text_form(
        "go", members, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=n,
    )  # fmt: skip


def test_edit_text_keeps_members_in_reading_order() -> None:
    members = [f"S{i}" for i in range(60)]
    f = text_form("t", members, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=60)
    g = edit(f, text="GO TEAM")
    assert g.members == f.members
    assert len(slots(g)) == 60
    assert g.recipe.model_dump(mode="json")["n"] == 60
    first = placements(g)[0]
    start = text_strokes(text_recipe(g.recipe.model_dump(mode="json")))[0].point_at(0.0)
    assert first.entity == "S0"
    assert (first.x, first.y) == pytest.approx(tuple(start), abs=1e-9)
    assert not np.allclose(slots(g), slots(f))


@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")
def test_edit_text_too_long_for_the_count_names_the_minimum() -> None:
    members = [f"S{i}" for i in range(12)]
    f = text_form("t", members, "HI", fonts=["simplex"], height=4.0, at=(0, 10), n=12)
    with pytest.raises(SpacingError) as caught:
        edit(f, text="HIHI")
    assert caught.value.code == "TOO_FEW_SPOTS"
    assert caught.value.context["min_n"] == 24


def test_edit_text_keeps_the_derived_count() -> None:
    strokes = text_strokes(text_recipe(_text().recipe.model_dump(mode="json")))
    members = [f"S{i}" for i in range(spacing_count(strokes, 0.5))]
    f = text_form("t", members, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10),
                  spacing=0.5)  # fmt: skip
    g = edit(f, text="GOGO")
    assert (
        g.recipe.model_dump(mode="json")["n"]
        == f.recipe.model_dump(mode="json")["n"]
        == len(members)
    )
    assert (
        g.recipe.model_dump(mode="json")["spacing"]
        == f.recipe.model_dump(mode="json")["spacing"]
        == 0.5
    )
    assert g.members == f.members


def test_edit_text_resolves_font_names() -> None:
    edited = edit(_text(), fonts=["relief-singleline"])
    assert edited.recipe.model_dump(mode="json")["fonts"] == [
        bundled_fonts().resolve("relief-singleline")
    ]


def test_edit_text_suggests_the_field_for_a_typo() -> None:
    with pytest.raises(SchemaError) as caught:
        edit(_text(), txt="A")
    assert "text" in caught.value.hint


def test_edit_locked_text_form_needs_force() -> None:
    locked = _text().replace(locked=True)
    with pytest.raises(LockedError):
        edit(locked, text="GA")
    assert edit(locked, force=True, text="GA").recipe.model_dump(mode="json")["text"] == "GA"


@pytest.mark.parametrize(("field", "value"), [("n", 30), ("spacing", SpacingSpec(interval=1.0))])
def test_regression_edit_text_form_rejects_count_changes(field: str, value: Any) -> None:
    f = _text()
    with pytest.raises(SchemaError) as caught:
        edit(f, **{field: value})
    assert caught.value.code == "TEXT_COUNT_EDIT"
    assert caught.value.context == {"form": "go", "field": field}
    assert caught.value.hint == (
        "use edit_text_count(form, add=[...], remove=[...], n=...) to change the count"
    )
    assert f == _text()


def test_regression_edit_text_form_accepts_text_n_free_edits() -> None:
    f = _text()
    assert edit(f, text="GA").recipe.model_dump(mode="json")["text"] == "GA"
    assert edit(f, height=5.0).recipe.model_dump(mode="json")["height"] == 5.0
    assert edit(f, rotation=30.0).recipe.model_dump(mode="json")["rotation"] == 30.0
    assert edit(f, transform=[[1, 0, 2], [0, 1, 0], [0, 0, 1]]).members == f.members


@pytest.mark.parametrize("count", [5, 25])
def test_regression_edit_text_form_rejects_a_member_count_change(count: int) -> None:
    f = _text()
    members = [f"S{i}" for i in range(count)]
    with pytest.raises(SpacingError) as caught:
        edit(f, members=members)
    assert caught.value.code == "MEMBER_COUNT_MISMATCH"
    assert "edit_text_count" in caught.value.hint


def test_edit_text_form_allows_reordering_members() -> None:
    f = _text()
    assert edit(f, members=f.members[::-1]).members == f.members[::-1]


@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")
@pytest.mark.parametrize("fonts", ["hershey-roman", 5])
def test_edit_text_rejects_fonts_that_are_not_a_list(fonts: Any) -> None:
    with pytest.raises(SchemaError) as caught:
        edit(_text(), fonts=fonts)
    assert caught.value.context == {"form": "go", "field": "fonts"}
    assert caught.value.hint == "pass a list of font names or URIs"


@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")
def test_edit_text_fonts_accept_a_user_font_uri_through_the_registry() -> None:
    registry, uri = register_font(bundled_fonts(), tiny_bytes())
    members = ["A", "B", "C", "D"]
    f = text_form(
        "t", members, "I", fonts=[uri], height=2.0, at=(1, 1), n=4, font_registry=registry
    )
    swapped = edit(_text(), fonts=[uri], text="I", font_registry=registry)
    assert swapped.recipe.model_dump(mode="json")["fonts"] == [uri]
    assert edit(f, fonts=[uri], font_registry=registry).recipe.model_dump(mode="json")["fonts"] == [
        uri
    ]
    with pytest.raises(MissingFontError):
        edit(_text(), fonts=[uri])


@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")
def test_edit_text_with_a_user_font_takes_the_registry() -> None:
    registry, uri = register_font(bundled_fonts(), tiny_bytes())
    members = ["A", "B", "C", "D"]
    f = text_form(
        "t", members, "I", fonts=[uri], height=2.0, at=(1, 1), n=4, font_registry=registry
    )
    assert edit(f, text="II", font_registry=registry).recipe.model_dump(mode="json")["text"] == "II"
    with pytest.raises(MissingFontError):
        edit(f, text="II")


@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")
def test_regression_edit_text_too_few_spots_hint_names_edit_text_count() -> None:
    members = [f"S{i}" for i in range(12)]
    f = text_form("t", members, "HI", fonts=["simplex"], height=4.0, at=(0, 10), n=12)
    with pytest.raises(SpacingError) as caught:
        edit(f, text="HIHI")
    assert caught.value.code == "TOO_FEW_SPOTS"
    assert caught.value.context == {"n": 12, "min_n": 24, "text": "HIHI"}
    assert caught.value.hint == (
        "add 12 spots with edit_text_count(form, add=[...], n=24), or shorten the text"
    )


def test_regression_edit_text_sparse_warning_names_edit_text_count() -> None:
    members = [f"S{i}" for i in range(36)]
    f = text_form("t", members, "HI", fonts=["simplex"], height=2.0, at=(0, 10), n=36)
    with pytest.warns(LegibilityWarning, match=r"; raise n, or add spots with edit_text_count$"):
        edit(f, text="HIHIHI")
