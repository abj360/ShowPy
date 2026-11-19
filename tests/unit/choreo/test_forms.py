#!/usr/bin/env python3

"""
test_forms.py --- tests form constructors, the recipe contract, slot spacing and facings

Contains:
    test_regression_fill_form_rejects_zero_interval: raises SpacingError, not ZeroDivisionError, for
        fill_form(interval=0)
    test_regression_scatter_form_rejects_zero_min_gap: raises SpacingError, not ZeroDivisionError,
        for scatter_form(min_gap=0)
    test_regression_scatter_form_rejects_negative_min_gap_without_hanging: raises SpacingError for
        scatter_form(min_gap=-1) instead of hanging
    test_regression_misspelled_recipe_key_is_rejected: raises SchemaError for an unknown recipe key
        such as "anchr"
    test_regression_misspelled_fill_pattern_is_rejected: raises SchemaError for an unknown fill
        pattern such as "hexx"
    test_regression_bad_block_anchor_raises_schema_error: raises SchemaError, not KeyError, for an
        unknown block anchor
    test_regression_arc_form_rejects_sweeps_that_overlap_themselves: rejects an arc past 360 degrees
        instead of producing coincident slots
    test_regression_form_rejects_fractional_slot_count: rejects n=2.5 instead of truncating it to 2
    test_regression_svg_form_rejects_empty_svg: raises a ShowPyError, not ValueError, for
        svg_form("")
    test_regression_generated_form_with_numpy_params_serializes_or_is_rejected: serializes numpy
        generator params, or rejects them with a ShowPyError
    test_text_form_*: stores font URIs, follows reading order, needs one member per slot, keeps
        user fonts behind their registry and maps slots and facings through transforms
    test_regression_placements_rejects_a_text_form_short_of_members: raises MEMBER_COUNT_MISMATCH
        with the edit_text_count hint instead of leaving letters out
"""

import contextlib
import json
import math
import sys
from typing import Any

import numpy as np
import pytest

from showpy.choreo.form_fns import DEFAULT_FORM_FNS, register_form_fn
from showpy.choreo.form_ops import gate, reorder, transform_form
from showpy.choreo.forms import (
    arc_form,
    block_form,
    circle_form,
    curve_form,
    custom_form,
    edit_params,
    fill_form,
    generated_form,
    line_form,
    placements,
    polyline_form,
    scatter_form,
    slots,
    svg_form,
    text_form,
    wedge_form,
)
from showpy.choreo.models import FacingSpec, Form, SpacingSpec
from showpy.core.errors import (
    LockedError,
    MissingFontError,
    SchemaError,
    ShowPyError,
    SpacingError,
    UnknownRefError,
)
from showpy.core.ids import FormId, SetId, SpotId
from showpy.core.types import Box2, Points
from showpy.core.util import approx_equal
from showpy.geo.affine import rotation_matrix, translation_matrix
from showpy.geo.curves import curve_from_construction
from showpy.geo.fonts import BUNDLED_FONTS, FontRegistry, bundled_fonts, register_font
from tests.fixtures.fonts import TINY, tiny_bytes
from tests.helpers import fails_after

TOL = 1e-6
MEMBERS = ["A", "B", "C", "D", "E"]


def _facings(form: Form) -> list[float]:
    return [p.facing for p in placements(form)]


def test_curve_kinds_store_a_rebuildable_curve_recipe() -> None:
    forms = [
        line_form("a", MEMBERS, (0, 0), (4, 0)),
        arc_form("b", MEMBERS, (0, 0), 2.0, 0.0, 180.0),
        curve_form("c", MEMBERS, (0, 0), (2, 2), (4, 0)),
        circle_form("d", MEMBERS, (0, 5), 3.0),
        wedge_form("e", MEMBERS, (0, 5), 3.0, 90.0),
        polyline_form("f", MEMBERS, (0, 0), (2, 0), (2, 2)),
    ]
    for form in forms:
        assert set(form.geometry) == {"curve"}
        curve_from_construction(form.geometry["curve"])
        assert Form.model_validate_json(form.model_dump_json()) == form
        json.dumps(form.model_dump(mode="json"))


def test_non_curve_recipes_follow_the_contract() -> None:
    square = [(0, 0), (4, 0), (4, 4), (0, 4)]
    assert set(block_form("b", [], (0, 5), 2, 3, 1.0).geometry) == {
        "origin", "rows", "cols", "interval", "facing", "anchor", "n",
    }  # fmt: skip
    assert set(fill_form("f", [], square, 1.0).geometry) == {
        "outline", "interval", "pattern", "angle_deg",
    }  # fmt: skip
    assert set(scatter_form("s", ["A"], square, 1.0).geometry) == {"region", "min_gap", "seed"}
    assert set(custom_form("c", [], [(0, 0)]).geometry) == {"points", "facings", "n"}
    assert generated_form("g", ["A"], "spiral", turns=2.0).geometry == {
        "fn": "spiral",
        "params": {"turns": 2.0},
    }


def test_line_count_spacing_includes_ends() -> None:
    assert approx_equal(slots(line_form("f", MEMBERS, (0, 0), (8, 0)))[:, 0], [0, 2, 4, 6, 8])


def test_spacing_window_start_and_end() -> None:
    spec = SpacingSpec(start=2.0, end=6.0)
    pts = slots(line_form("f", ["A", "B", "C"], (0, 0), (8, 0), spec))
    assert approx_equal(pts[:, 0], [2, 4, 6])


def test_spacing_window_outside_shape_raises() -> None:
    with pytest.raises(SpacingError):
        slots(line_form("f", ["A", "B"], (0, 0), (4, 0), SpacingSpec(end=9.0)))


def test_interval_spacing_aligns_and_fits() -> None:
    line = ((0.0, 0.0), (10.0, 0.0))
    start = SpacingSpec(mode="interval", interval=2.0)
    assert approx_equal(slots(line_form("f", ["A", "B"], *line, start))[:, 0], [0, 2])
    end = start.model_copy(update={"align": "end"})
    assert approx_equal(slots(line_form("f", ["A", "B"], *line, end))[:, 0], [8, 10])
    center = start.model_copy(update={"align": "center"})
    assert approx_equal(slots(line_form("f", ["A", "B"], *line, center))[:, 0], [4, 6])
    assert len(slots(line_form("f", [], *line, start))) == 6
    with pytest.raises(SpacingError, match="do not fit"):
        slots(line_form("f", list("ABCDEFG"), *line, start))


def test_gaps_add_room_after_members() -> None:
    spec = SpacingSpec(gaps=((0, 2.0),))
    pts = slots(line_form("f", ["A", "B", "C"], (0, 0), (10, 0), spec))
    assert approx_equal(pts[:, 0], [0, 6, 10])
    with pytest.raises(SpacingError):
        slots(line_form("f", ["A", "B"], (0, 0), (10, 0), SpacingSpec(gaps=((5, 1.0),))))


def test_interval_spacing_with_gaps() -> None:
    spec = SpacingSpec(mode="interval", interval=1.0, gaps=((0, 2.0),))
    pts = slots(line_form("f", ["A", "B", "C"], (0, 0), (10, 0), spec))
    assert approx_equal(pts[:, 0], [0, 3, 4])


def test_closed_curve_never_repeats_its_start() -> None:
    pts = slots(circle_form("f", ["A", "B", "C", "D"], (0, 0), 1.0))
    assert approx_equal(pts, [[1, 0], [0, 1], [-1, 0], [0, -1]], TOL)


def test_slot_count_n_without_members() -> None:
    form = line_form("f", [], (0, 0), (4, 0), n=3)
    assert len(slots(form)) == 3
    assert placements(form) == []
    with pytest.raises(SpacingError, match="no members"):
        slots(scatter_form("s", [], [(0, 0), (4, 0), (4, 4)], 1.0))


def test_members_beyond_slots_raise() -> None:
    with pytest.raises(SpacingError, match="slots"):
        placements(line_form("f", MEMBERS, (0, 0), (4, 0), n=2))


def test_proportional_spacing_shares_svg_subpaths_by_length() -> None:
    form = svg_form("f", [], "M0 0 L1 0 M0 1 L3 1", Box2(min=(0, 0), max=(3, 1)), n=6)
    pts = slots(form)
    assert len(pts) == 6
    lower = pts[:, 1] < 0.5
    assert sorted([int(lower.sum()), int((~lower).sum())]) == [2, 4]


def test_block_and_fill_and_scatter_slots() -> None:
    assert len(slots(block_form("b", [], (0, 5), 2, 3, 1.0))) == 6
    square = [(0, 0), (2, 0), (2, 2), (0, 2)]
    assert len(slots(fill_form("f", [], square, 1.0))) == 9
    assert len(slots(fill_form("f", ["A", "B"], square, 1.0))) == 2
    pts = slots(scatter_form("s", MEMBERS, [(0, 0), (8, 0), (8, 8), (0, 8)], 1.5, seed=3))
    gaps = np.linalg.norm(pts[:, None] - pts[None], axis=2)[np.triu_indices(5, 1)]
    assert gaps.min() >= 1.5 - TOL
    again = scatter_form("s", MEMBERS, [(0, 0), (8, 0), (8, 8), (0, 8)], 1.5, seed=3)
    assert approx_equal(slots(again), pts)


def test_placements_carry_form_order_and_positions() -> None:
    rows = placements(line_form("f", ["A", "B"], (0, 0), (2, 0), set_id="3"))
    assert [(p.entity, p.form, p.order, p.x) for p in rows] == [
        ("A", "f", 0, 0.0),
        ("B", "f", 1, 2.0),
    ]


def test_facing_modes() -> None:
    line = ((0.0, 0.0), (4.0, 0.0))
    assert _facings(line_form("f", ["A"], *line)) == [0.0]
    assert _facings(line_form("f", ["A"], *line, facing=FacingSpec(mode="fixed", value=45.0))) == [
        45.0
    ]
    along = FacingSpec(mode="along")
    assert approx_equal(_facings(line_form("f", ["A", "B"], *line, facing=along)), [90.0, 90.0])
    normal = FacingSpec(mode="normal")
    assert approx_equal(_facings(line_form("f", ["A", "B"], *line, facing=normal)), [180.0, 180.0])
    toward = FacingSpec(mode="toward_point", value=(0.0, -5.0))
    assert approx_equal(_facings(line_form("f", ["A"], (0, 0), (0, 2), facing=toward)), [0.0])


def test_offset_value_turns_computed_facings() -> None:
    spec = FacingSpec(mode="along", value=180.0)
    assert approx_equal(_facings(line_form("f", ["A"], (0, 0), (4, 0), facing=spec)), [270.0])


def test_circle_faces_outward_and_arc_uses_its_center() -> None:
    assert approx_equal(_facings(circle_form("f", ["A", "B"], (0, 5), 2.0)), [90.0, 270.0])
    inward_arc = arc_form(
        "a", ["A", "B"], (0, 0), 3.0, 0.0, 90.0, facing=FacingSpec(mode="outward")
    )
    assert approx_equal(_facings(inward_arc), [90.0, 180.0], TOL)


def test_outward_on_centroid_spot_faces_the_audience() -> None:
    block = block_form("b", list("ABC"), (0, 5), 1, 3, 1.0, facing=FacingSpec(mode="outward"))
    assert approx_equal(_facings(block), [270.0, 0.0, 90.0])


def test_along_on_non_curve_kinds_uses_neighbouring_slots() -> None:
    block = block_form("b", list("ABC"), (0, 5), 1, 3, 1.0, facing=FacingSpec(mode="along"))
    assert approx_equal(_facings(block), [90.0, 90.0, 90.0])


def test_block_faces_its_direction_by_default() -> None:
    assert _facings(block_form("b", ["A"], (0, 5), 1, 1, 1.0, direction_deg=30.0)) == [30.0]


def test_wedge_apex_is_the_middle_slot() -> None:
    pts = slots(wedge_form("w", list("ABC"), (0, 5), 2.0, 90.0))
    assert approx_equal(pts[1], [0, 5])


def test_custom_form_keeps_points_and_facings() -> None:
    form = custom_form("c", ["A", "B"], [(1, 2), (3, 4)], facings=[10.0, 20.0])
    assert [(p.x, p.y, p.facing) for p in placements(form)] == [(1.0, 2.0, 10.0), (3.0, 4.0, 20.0)]
    with pytest.raises(SchemaError):
        custom_form("c", [], [(1, 2)], facings=[1.0, 2.0])


def test_generated_form_uses_registered_generator_and_keeps_params() -> None:
    def dots(params: dict[str, float], n: int) -> Points:
        step = float(params.get("step", 1.0))
        return np.stack([np.arange(n) * step, np.zeros(n)], axis=1, dtype=np.float64)

    registry = register_form_fn(DEFAULT_FORM_FNS, "dots", dots)  # type: ignore[arg-type]
    form = generated_form("g", ["A", "B", "C"], "dots", registry=registry, step=2.0)
    assert approx_equal(slots(form, registry)[:, 0], [0, 2, 4])
    wider = edit_params(form, registry=registry, step=3.0)
    assert approx_equal(slots(wider, registry)[:, 0], [0, 3, 6])
    assert form.geometry["params"] == {"step": 2.0}


def test_generated_form_rejects_unknown_generator_with_suggestion() -> None:
    with pytest.raises(UnknownRefError, match="spiral"):
        generated_form("g", ["A"], "spirall")


def test_edit_params_respects_lock_and_kind() -> None:
    form = generated_form("g", ["A"], "spiral").model_copy(update={"locked": True})
    with pytest.raises(LockedError):
        edit_params(form, turns=1.0)
    assert edit_params(form, force=True, turns=1.0).geometry["params"]["turns"] == 1.0
    with pytest.raises(SchemaError, match="not generated"):
        edit_params(line_form("f", ["A"], (0, 0), (1, 0)), turns=1.0)


def test_missing_recipe_key_raises_schema_error() -> None:
    broken = Form(
        id=FormId("b"), set=SetId("1"), kind="block", geometry={"rows": 1}, members=(SpotId("A"),)
    )
    with pytest.raises(SchemaError, match="origin"):
        slots(broken)


def test_transform_key_maps_non_curve_points() -> None:
    form = custom_form("c", ["A"], [(1, 0)])
    moved = form.model_copy(
        update={"geometry": {**form.geometry, "transform": [[1, 0, 2], [0, 1, 3], [0, 0, 1]]}}
    )
    assert approx_equal(slots(moved), [[3, 3]])


def test_arc_slot_positions_are_on_the_circle() -> None:
    pts = slots(arc_form("a", MEMBERS, (1, 2), 3.0, 30.0, 120.0))
    assert approx_equal(np.linalg.norm(pts - [1, 2], axis=1), [3.0] * 5, TOL)
    angles = np.degrees(np.arctan2(pts[:, 1] - 2, pts[:, 0] - 1))
    assert approx_equal(angles, [30, 60, 90, 120, 150], TOL)
    assert math.isclose(
        float(np.linalg.norm(pts[0] - pts[1])), float(np.linalg.norm(pts[3] - pts[4]))
    )


SQUARE_AREA = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0)]
BLOCK_RECIPE = {"origin": [0.0, 0.0], "rows": 1, "cols": 2, "interval": 1.0}
OVERLAPPING_SWEEP_DEG = 720.0
FRACTIONAL_SLOT_COUNT = 2.5
TIMEOUT_S = 5


def test_regression_fill_form_rejects_zero_interval() -> None:
    """Raises SpacingError, not ZeroDivisionError, for fill_form(interval=0) (audit 9.6)."""
    with pytest.raises(SpacingError):
        slots(fill_form("f", [], SQUARE_AREA, 0.0))


def test_regression_scatter_form_rejects_zero_min_gap() -> None:
    """Raises SpacingError, not ZeroDivisionError, for scatter_form(min_gap=0) (audit 9.6)."""
    with pytest.raises(SpacingError):
        slots(scatter_form("f", ["A", "B"], SQUARE_AREA, 0.0))


@fails_after(TIMEOUT_S)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_scatter_form_rejects_negative_min_gap_without_hanging() -> None:
    """Raises SpacingError for scatter_form(min_gap=-1) instead of hanging (audit 9.6; P0 #7)."""
    with pytest.raises(SpacingError):
        slots(scatter_form("f", ["A", "B"], SQUARE_AREA, -1.0))


@pytest.mark.defect("UP-9.6-45")
@pytest.mark.xfail(strict=True, reason="UP-9.6-45")
def test_regression_misspelled_recipe_key_is_rejected() -> None:
    """Raises SchemaError for an unknown recipe key such as "anchr" (audit 9.6)."""
    recipe = {**BLOCK_RECIPE, "anchr": "front-left"}
    with pytest.raises(SchemaError):
        slots(Form(id=FormId("f"), set=SetId("1"), kind="block", geometry=recipe, members=()))


@pytest.mark.defect("UP-9.6-46")
@pytest.mark.xfail(strict=True, reason="UP-9.6-46")
def test_regression_misspelled_fill_pattern_is_rejected() -> None:
    """Raises SchemaError for an unknown fill pattern such as "hexx" (audit 9.6)."""
    with pytest.raises(SchemaError):
        slots(fill_form("f", [], SQUARE_AREA, 1.0, pattern="hexx"))  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.6-47")
@pytest.mark.xfail(strict=True, reason="UP-9.6-47")
def test_regression_bad_block_anchor_raises_schema_error() -> None:
    """Raises SchemaError, not KeyError, for an unknown block anchor (audit 9.6)."""
    with pytest.raises(SchemaError):
        slots(block_form("f", [], (0, 0), 1, 2, 1.0, anchor=""))  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.6-48")
@pytest.mark.xfail(strict=True, reason="UP-9.6-48")
def test_regression_arc_form_rejects_sweeps_that_overlap_themselves() -> None:
    """Rejects an arc past 360 degrees instead of producing coincident slots (audit 9.6)."""
    with pytest.raises(ShowPyError):
        slots(arc_form("a", ["A", "B", "C"], (0, 0), 5.0, 0.0, OVERLAPPING_SWEEP_DEG))


@pytest.mark.defect("UP-9.6-49")
@pytest.mark.xfail(strict=True, reason="UP-9.6-49")
def test_regression_form_rejects_fractional_slot_count() -> None:
    """Rejects n=2.5 instead of truncating it to 2 (audit 9.6)."""
    with pytest.raises(SchemaError):
        slots(line_form("l", [], (0, 0), (4, 0), n=FRACTIONAL_SLOT_COUNT))  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.6-58")
@pytest.mark.xfail(strict=True, reason="UP-9.6-58")
def test_regression_svg_form_rejects_empty_svg() -> None:
    """Raises a ShowPyError, not ValueError, for svg_form("") (audit 9.6)."""
    with pytest.raises(ShowPyError):
        svg_form("f", [], "", Box2(min=(0, 0), max=(1, 1)))


@pytest.mark.defect("UP-9.6-60")
@pytest.mark.xfail(strict=True, reason="UP-9.6-60")
def test_regression_generated_form_with_numpy_params_serializes_or_is_rejected() -> None:
    """Serializes numpy generator params, or rejects them with a ShowPyError (audit 9.6)."""
    center = np.array([0.0, 1.0])
    with contextlib.suppress(ShowPyError):
        json.loads(generated_form("g", ["A"], "spiral", center=center).model_dump_json())


TEXT_MEMBERS = [f"S{i}" for i in range(24)]
EMPTY_FONTS = FontRegistry(fonts={}, sources={}, names={})
TURN = 90.0


def _go(members: list[str] = TEXT_MEMBERS, n: int = 24, facing: FacingSpec | None = None) -> Form:
    return text_form(
        "go", members, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=n, facing=facing
    )


def _tiny_form() -> tuple[Form, FontRegistry]:
    registry, uri = register_font(bundled_fonts(), tiny_bytes())
    members = ["A", "B", "C"]
    form = text_form("t", members, "I", fonts=[uri], height=2.0, at=(1, 1), n=3,
                     font_registry=registry)  # fmt: skip
    return form, registry


def test_text_form_stores_uris_not_names() -> None:
    assert _go().geometry["fonts"] == [BUNDLED_FONTS["hershey-roman"]]
    assert _go().geometry["n"] == 24


def test_text_form_placements_follow_reading_order() -> None:
    form = _go()
    placed = placements(form)
    assert [p.entity for p in placed] == TEXT_MEMBERS
    assert [(p.x, p.y) for p in placed] == [tuple(row) for row in slots(form).tolist()]


def test_existing_kinds_ignore_the_font_registry() -> None:
    form = line_form("f", ["A", "B", "C"], (0, 0), (4, 0))
    assert np.array_equal(slots(form, font_registry=EMPTY_FONTS), slots(form))
    assert placements(form, font_registry=EMPTY_FONTS) == placements(form)


def test_text_form_with_a_user_font_needs_its_registry() -> None:
    form, registry = _tiny_form()
    uri = form.geometry["fonts"][0]
    assert len(slots(form, font_registry=registry)) == 3
    with pytest.raises(MissingFontError) as caught:
        slots(form)
    assert caught.value.context["font"] == uri
    with pytest.raises(MissingFontError) as gated:
        gate(form, "start", TURN)
    assert gated.value.context["font"] == uri
    with pytest.raises(MissingFontError) as reordered:
        reorder(form, "by_x")
    assert reordered.value.context["font"] == uri
    assert TINY["name"] not in caught.value.message


@pytest.mark.parametrize(
    ("members", "hint"),
    [(TEXT_MEMBERS[:20], "add 4 more spots"), ([*TEXT_MEMBERS, "X", "Y"], "remove 2 spots")],
)
def test_text_form_needs_one_member_per_slot(members: list[str], hint: str) -> None:
    with pytest.raises(SpacingError) as caught:
        _go(members)
    assert caught.value.code == "MEMBER_COUNT_MISMATCH"
    assert caught.value.hint == hint
    assert caught.value.context == {"form": "go", "n": 24, "members": len(members)}


@pytest.mark.parametrize("counts", [{}, {"n": 24, "spacing": 0.5}])
def test_text_form_needs_exactly_one_of_n_and_spacing(counts: dict[str, Any]) -> None:
    with pytest.raises(SchemaError) as caught:
        text_form("go", TEXT_MEMBERS, "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10),
                  **counts)  # fmt: skip
    assert caught.value.code == "N_OR_SPACING"
    assert "n=" in caught.value.hint
    assert "spacing=" in caught.value.hint


def test_text_form_stores_n_and_no_spacing_for_an_exact_count() -> None:
    assert _go().geometry["spacing"] is None


def test_text_form_rejects_too_few_spots() -> None:
    with pytest.raises(SpacingError) as caught:
        _go(TEXT_MEMBERS[:3], n=3)
    assert caught.value.code == "TOO_FEW_SPOTS"


def test_transformed_text_moves_slots_and_facings() -> None:
    form = _go(facing=FacingSpec(mode="along"))
    before = slots(form)
    moved = transform_form(form, translation_matrix(0, 3))
    assert np.allclose(slots(moved), before + np.array([0.0, 3.0]), atol=1e-9)
    turned = transform_form(form, rotation_matrix(TURN, (0, 0)))
    for old, new in zip(placements(form), placements(turned), strict=True):
        assert abs((new.facing - old.facing - TURN + 180.0) % 360.0 - 180.0) < 1e-9


def test_regression_placements_rejects_a_text_form_short_of_members() -> None:
    short = _go().replace(members=tuple(TEXT_MEMBERS[:-1]))
    with pytest.raises(SpacingError) as caught:
        placements(short)
    assert caught.value.code == "MEMBER_COUNT_MISMATCH"
    assert caught.value.context == {"form": "go", "n": 24, "members": 23}
    assert caught.value.hint.startswith("add 1 more spots; ")
    assert "edit_text_count" in caught.value.hint
