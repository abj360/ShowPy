#!/usr/bin/env python3

"""
test_select.py --- tests the selection grammar and the spatial, ordering and pairing picks

Contains:
    test_*(): one behavior of showpy.cast.select each
    test_regression_glued_operators_raise_instead_of_misparsing: rejects glued +, - and & operators
        at their position instead of misparsing them
    test_regression_select_generator_is_not_silently_empty: selects the IDs a generator yields, or
        rejects it, never returns nothing
    test_regression_select_rejects_a_non_selector_type: rejects an int selector with a ShowPyError,
        not a TypeError
    test_regression_select_rejects_non_string_ids_in_a_list: rejects a list holding a non-string
        with a ShowPyError, not an AttributeError
    test_regression_select_deep_nesting_raises_schema_error: rejects 500 nested parentheses with a
        SchemaError, not RecursionError
    test_regression_mirror_pairs_finds_every_pair_within_tolerance: pairs spots within tolerance
        even when the min-sum plan misses them
    test_regression_mirror_pairs_keeps_a_pair_the_min_sum_plan_drops: keeps both pairs within
        tolerance where the shortest full plan uses one far edge
    test_regression_mirror_pairs_pairs_exactly_mirrored_spots: pairs zero-distance mirrors, even
        with zero tolerance
    test_regression_mirror_pairs_breaks_ties_by_spot_order: pairs the n-th left spot with the n-th
        right spot when pairings tie
    test_regression_mirror_pairs_without_edges_is_empty: returns no pairs when nothing lies within
        tolerance
    test_regression_mirror_pairs_refuses_a_negative_or_non_finite_tolerance: raises SchemaError
        naming the tolerance, before looking at any spot
    test_regression_mirror_pairs_memory_follows_candidate_pairs: pairs 2,000 spots per side within
        a budget far below one left-by-right distance table
    test_regression_mirror_pairs_solves_only_candidate_pairs: hands the solver one edge per
        candidate pair plus one per left spot, not left by right
    test_regression_split_rejects_absurd_part_counts_quickly: rejects an absurd part count instead
        of building 100,000 empty groups
    test_regression_select_rejects_non_string_ids_in_a_set: rejects a set holding a non-string with
        a ShowPyError, not a TypeError
    test_regression_select_rejects_over_long_expressions: rejects text over MAX_TEXT_CHARS
    test_regression_split_rejects_more_parts_than_spots: rejects k above the selection size
    test_select_accepts_nesting_at_the_depth_cap: parses MAX_DEPTH nested parentheses
    test_regression_select_set_input_ignores_hash_seed: returns one order for a Python set under
        every hash seed
    test_regression_mixed_operators_raise_with_both_readings: refuses & mixed with + or - at one
        level, naming the second operator kind and both parenthesized readings
    test_regression_select_depth_10000_raises_schema_error_not_recursion_error: rejects 10,000
        nested parentheses with SchemaError, never RecursionError
    test_regression_select_rejects_a_set_of_non_strings_with_schema_error: rejects a set of ints
        with SchemaError naming the value, before sorting it
    test_regression_digit_tails_over_64_characters_raise_schema_error: splits a 64-digit tail and
        refuses a 65-digit one in IDs, labels and range words
    test_regression_long_operator_chains_cost_only_their_right_operands: evaluates 20,000-term
        chains on 40,000 spots within the time budget
"""

import contextlib
import math
import sys
import tracemalloc

import pytest
import shapely
from scipy.sparse import csr_array
from scipy.sparse.csgraph import min_weight_full_bipartite_matching

from showpy.cast.models import Cast, Group, Shape, Spot
from showpy.cast.select import (
    Selection,
    alternate,
    in_region,
    is_label_dependent,
    label_prefix,
    mirror_pairs,
    near,
    normalize_v1_selector,
    ordered_along,
    parse_selector,
    select,
    split,
    where,
)
from showpy.choreo.form_models import Form
from showpy.choreo.models import Placement, Set
from showpy.core.caps import MAX_DEPTH, MAX_TEXT_CHARS
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import GeometryError, SchemaError, ShowPyError, UnknownRefError
from showpy.core.ids import ID_MAX_LENGTH, LEGACY_IDS_KEY, FormId, SetId, SpotId
from showpy.core.types import Region
from showpy.core.util import split_id
from showpy.geo.shapes import line
from showpy.show.models import Show
from tests.fixtures.recipes import ARC_RECIPE
from tests.fixtures.shows import two_set_show
from tests.helpers import fails_after, outputs_under_hash_seeds

RECT = Shape(kind="rectangle", width_m=1.0, depth_m=0.5)


def mixed_cast() -> Cast:
    groups = {
        "trumpets": Group(key="trumpets", label_prefix="T"),
        "tubas": Group(key="tubas", label_prefix="TB"),
        "flags": Group(key="flags", label_prefix="flag-"),
    }
    spots = (
        Spot(id=SpotId("T1"), group="trumpets", tags=("soloist",)),
        Spot(id=SpotId("T2"), group="trumpets"),
        Spot(id=SpotId("T10"), group="trumpets"),
        Spot(id=SpotId("TB1"), group="tubas", shape=RECT),
        {"id": "flag-1", "group": "flags", "shape": RECT},
        {"id": "flag-2", "group": "flags", "tags": ("soloist",)},
    )
    return Cast.model_validate(
        {
            "groups": groups,
            "spots": spots,
            "selections": {"front": ("T2", "flag-1")},
        },
        context={LEGACY_IDS_KEY: True},
    )


def mixed_show() -> Show:
    cast = mixed_cast()
    placements = tuple(
        Placement(
            entity=spot_id, x=float(i), y=0.0, form=FormId("arc1") if i < 3 else None, order=i
        )
        for i, spot_id in enumerate(cast.ids())
    )
    arc = Form(
        id=FormId("arc1"),
        set=SetId("1"),
        recipe=ARC_RECIPE,
        members=(SpotId("T10"), SpotId("T2"), SpotId("T1")),
    )
    base = two_set_show()
    return base.model_copy(
        update={
            "cast": cast,
            "sets": (
                Set(id=SetId("1"), counts=0.0, placements=placements, forms=(FormId("arc1"),)),
            ),
            "forms": {"arc1": arc},
            "transitions": (),
        }
    )


def ids(source: Show | Set | Cast, expr: str) -> tuple[str, ...]:
    return select(source, expr).ids


def test_single_id_and_all() -> None:
    cast = mixed_cast()
    assert ids(cast, "T2") == ("T2",)
    assert ids(cast, "all") == cast.ids()


def test_range_keeps_range_order_and_skips_gaps() -> None:
    cast = mixed_cast()
    assert ids(cast, "T1-T2") == ("T1", "T2")
    assert ids(cast, "T1-T10") == ("T1", "T2", "T10")


def test_range_with_unknown_endpoint_raises() -> None:
    with pytest.raises(UnknownRefError):
        select(mixed_cast(), "T1-T5")


def test_prefix_matches_label_prefix_only() -> None:
    cast = mixed_cast()
    assert ids(cast, "T*") == ("T1", "T2", "T10")
    assert ids(cast, "TB*") == ("TB1",)
    assert ids(cast, "flag-*") == ("flag-1", "flag-2")


def test_hyphenated_id_is_one_word() -> None:
    assert ids(mixed_cast(), "flag-2") == ("flag-2",)


def test_group_tag_shape_and_named_selection() -> None:
    cast = mixed_cast()
    assert ids(cast, "group:flags") == ("flag-1", "flag-2")
    assert ids(cast, "tag:soloist") == ("T1", "flag-2")
    assert ids(cast, "shape:rectangle") == ("TB1", "flag-1")
    assert ids(cast, "sel:front") == ("T2", "flag-1")


def test_form_uses_member_order_on_a_show() -> None:
    assert ids(mixed_show(), "form:arc1") == ("T10", "T2", "T1")


def test_form_on_a_set_uses_placement_order() -> None:
    assert ids(mixed_show().set("1"), "form:arc1") == ("T1", "T2", "T10")


def test_form_on_a_cast_raises() -> None:
    with pytest.raises(SchemaError):
        select(mixed_cast(), "form:arc1")


def test_group_on_a_set_raises_with_hint() -> None:
    with pytest.raises(SchemaError) as info:
        select(mixed_show().set("1"), "group:flags")
    assert "Show" in info.value.hint


def test_operators_combine_left_to_right() -> None:
    cast = mixed_cast()
    assert ids(cast, "T* + TB1") == ("T1", "T2", "T10", "TB1")
    assert ids(cast, "all - T*") == ("TB1", "flag-1", "flag-2")
    assert ids(cast, "tag:soloist & T*") == ("T1",)
    assert ids(cast, "T* - T2 + TB1") == ("T1", "T10", "TB1")
    assert ids(cast, "T* & tag:soloist & T1") == ("T1",)
    with pytest.raises(SchemaError) as info:
        select(cast, "T1 + T2 & T2")
    assert info.value.code == ErrorCode.MIXED_OPERATORS


def test_parentheses_group() -> None:
    assert ids(mixed_cast(), "T1 + (T2 & T2)") == ("T1", "T2")


def test_operators_next_to_parentheses_need_no_spaces() -> None:
    assert ids(mixed_cast(), "((T1)+(T2))&(T2)") == ("T2",)
    with pytest.raises(SchemaError) as info:
        select(mixed_cast(), "(T1)+(T2)&(T2)")
    assert info.value.code == ErrorCode.MIXED_OPERATORS
    assert ids(mixed_cast(), "(T*)-(T2)") == ("T1", "T10")


def test_selection_description_is_the_expression() -> None:
    assert select(mixed_cast(), "T1 + T2").description == "T1 + T2"


@pytest.mark.parametrize(
    ("expr", "position"),
    [("T1 + ", 5), ("T1 T2", 3), ("(T1", 3), ("T1)", 2), ("- T1", 0), ("", 0), ("bogus:x", 0)],
)
def test_bad_token_reports_position(expr: str, position: int) -> None:
    with pytest.raises(SchemaError) as info:
        select(mixed_cast(), expr)
    assert info.value.context["position"] == position
    assert "group:" in info.value.hint


def test_unknown_id_suggests_close_matches() -> None:
    with pytest.raises(UnknownRefError) as info:
        select(mixed_cast(), "T1 + T3")
    assert "T1" in info.value.hint or "T2" in info.value.hint
    assert info.value.context["position"] == 5


@pytest.mark.parametrize("expr", ["group:drums", "tag:leader", "sel:back", "X*"])
def test_unknown_reference_raises(expr: str) -> None:
    with pytest.raises(UnknownRefError):
        select(mixed_cast(), expr)


def test_bad_shape_kind_raises_schema_error() -> None:
    with pytest.raises(SchemaError):
        select(mixed_cast(), "shape:hexagon")


def test_list_and_selection_pass_through_validated() -> None:
    cast = mixed_cast()
    assert select(cast, ["T2", "T1", "T2"]).ids == ("T2", "T1")
    assert select(cast, Selection(ids=(SpotId("TB1"),))).ids == ("TB1",)
    with pytest.raises(UnknownRefError):
        select(cast, ["T1", "T9"])


def test_is_label_dependent() -> None:
    assert is_label_dependent("T1-T4 + group:flags")
    assert is_label_dependent("T*")
    assert not is_label_dependent("group:flags - tag:soloist")
    assert not is_label_dependent("all")


def spatial_set() -> Set:
    return Set(
        id=SetId("1"),
        counts=0.0,
        placements=(
            Placement(entity=SpotId("A"), x=-2.0, y=1.0, form=FormId("f"), order=2),
            Placement(entity=SpotId("B"), x=2.0, y=1.0, form=FormId("f"), order=0),
            Placement(entity=SpotId("C"), x=0.0, y=3.0, form=FormId("f"), order=1),
            Placement(entity=SpotId("D"), x=1.0, y=1.0, on_field=False),
            Placement(entity=SpotId("E"), x=-1.0, y=2.0),
            Placement(entity=SpotId("F"), x=1.05, y=2.0),
        ),
        forms=(FormId("f"),),
    )


def test_in_region_counts_boundary_and_skips_off_field() -> None:
    square = shapely.Polygon([(-2, 0), (2, 0), (2, 2), (-2, 2)])
    assert in_region(spatial_set(), square).ids == ("A", "B", "E", "F")


def test_in_region_accepts_a_ring_and_a_region() -> None:
    ring = [(-3.0, 0.0), (0.0, 0.0), (0.0, 3.0), (-3.0, 3.0)]
    assert in_region(spatial_set(), ring).ids == ("A", "C", "E")
    assert in_region(spatial_set(), Region(exterior=tuple(ring))).ids == ("A", "C", "E")


def test_in_region_rejects_too_few_points() -> None:
    with pytest.raises(GeometryError):
        in_region(spatial_set(), [(0.0, 0.0), (1.0, 1.0)])


def test_near_includes_radius_and_skips_off_field() -> None:
    assert near(spatial_set(), (2.0, 1.5), 1.1).ids == ("B", "F")
    assert near(spatial_set(), (0.0, 1.0), 2.0).ids == ("A", "B", "C", "E", "F")


def test_near_rejects_negative_radius() -> None:
    with pytest.raises(GeometryError):
        near(spatial_set(), (0.0, 0.0), -1.0)


def test_where_filters_by_predicate() -> None:
    assert where(spatial_set(), lambda p: p.y > 1.5).ids == ("C", "E", "F")


def test_ordered_along_form_x_y() -> None:
    s = spatial_set()
    assert ordered_along(s, ["A", "B", "C", "E"], by="form").ids == ("B", "C", "A", "E")
    assert ordered_along(s, ["F", "E", "A", "B"], by="x").ids == ("A", "E", "F", "B")
    assert ordered_along(s, ["C", "E", "A", "B"], by="y").ids == ("A", "B", "E", "C")


def test_ordered_along_curve_breaks_ties_by_natural_id() -> None:
    tied = Set(
        id=SetId("1"),
        counts=0.0,
        placements=(
            Placement(entity=SpotId("T10"), x=1.0, y=1.0),
            Placement(entity=SpotId("T2"), x=1.0, y=-1.0),
            Placement(entity=SpotId("T1"), x=3.0, y=0.0),
        ),
    )
    assert ordered_along(tied, "all", by=line((0.0, 0.0), (4.0, 0.0))).ids == ("T2", "T10", "T1")


def test_split_makes_near_equal_consecutive_groups() -> None:
    parts = split(Selection(ids=tuple(map(SpotId, "ABCDEFG"))), 3)
    assert [p.ids for p in parts] == [("A", "B", "C"), ("D", "E"), ("F", "G")]


def test_split_rejects_k_below_one() -> None:
    with pytest.raises(SchemaError):
        split(["A"], 0)


def test_alternate_interleaves() -> None:
    parts = alternate(list("ABCDE"))
    assert [p.ids for p in parts] == [("A", "C", "E"), ("B", "D")]
    assert [p.ids for p in alternate(list("ABCDE"), 3)] == [("A", "D"), ("B", "E"), ("C",)]


def test_mirror_pairs_pairs_across_axis_within_tol() -> None:
    assert mirror_pairs(spatial_set(), "all") == [("A", "B"), ("E", "F")]
    assert mirror_pairs(spatial_set(), "all", tol=0.01) == [("A", "B")]


def test_mirror_pairs_uses_optimal_assignment() -> None:
    crowded = Set(
        id=SetId("1"),
        counts=0.0,
        placements=(
            Placement(entity=SpotId("L1"), x=-1.0, y=0.0),
            Placement(entity=SpotId("L2"), x=-1.0, y=0.3),
            Placement(entity=SpotId("R1"), x=1.0, y=0.25),
            Placement(entity=SpotId("R2"), x=1.0, y=0.55),
        ),
    )
    assert mirror_pairs(crowded, "all", tol=0.3) == [("L1", "R1"), ("L2", "R2")]


def test_mirror_pairs_respects_axis() -> None:
    assert mirror_pairs(spatial_set(), ["E", "F"], axis_x=1.0, tol=0.5) == []
    assert mirror_pairs(spatial_set(), ["C", "B"], axis_x=1.0, tol=0.1) == []


def test_regression_glued_operators_raise_instead_of_misparsing() -> None:
    """Rejects glued +, - and & operators at their position instead of misparsing them."""
    with pytest.raises(SchemaError) as info:
        select(mixed_cast(), "T1+T2-T10")
    assert info.value.context["position"] == 2
    with pytest.raises(SchemaError):
        select(mixed_cast(), "T1 +T2")
    assert ids(mixed_cast(), "T1 + T2 - T2") == ("T1",)
    assert ids(mixed_cast(), "T1-T2") == ("T1", "T2")


NESTING_DEPTH = 500
HUGE_PARTS = 10**5
SPLIT_PEAK_BUDGET_BYTES = 5_000_000
MIRROR_TOL_M = 0.1
SET_SELECT_SCRIPT = (
    "from showpy.cast.models import Cast, Group, Spot\n"
    "from showpy.cast.select import select\n"
    "ids = [f'T{i}' for i in range(1, 7)]\n"
    "cast = Cast(groups={'g': Group(key='g', label_prefix='T')},\n"
    "            spots=tuple(Spot(id=i, group='g') for i in ids))\n"
    "try:\n"
    "    print(select(cast, set(ids)).ids)\n"
    "except Exception as error:\n"
    "    print(type(error).__name__)\n"
)


def _line_cast() -> Cast:
    return Cast(
        groups={"g": Group(key="g", label_prefix="T")},
        spots=tuple(Spot(id=SpotId(f"T{i}"), group="g") for i in range(1, 5)),
    )


def test_regression_select_generator_is_not_silently_empty() -> None:
    """Selects the IDs a generator yields, or rejects it, never returns nothing (audit 9.3)."""
    try:
        picked = select(_line_cast(), (i for i in ["T1", "T2"]))  # type: ignore[arg-type]
    except ShowPyError:
        return
    assert picked.ids == ("T1", "T2")


def test_regression_select_rejects_a_non_selector_type() -> None:
    """Rejects an int selector with a ShowPyError, not a TypeError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        select(_line_cast(), 5)  # type: ignore[arg-type]


def test_regression_select_rejects_non_string_ids_in_a_list() -> None:
    """Rejects a list holding a non-string with a ShowPyError, not an AttributeError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        select(_line_cast(), ["T1", 3])  # type: ignore[list-item]


def test_regression_select_deep_nesting_raises_schema_error() -> None:
    """Rejects 500 nested parentheses with a SchemaError, not RecursionError (audit 9.3)."""
    expression = "(" * NESTING_DEPTH + "T1" + ")" * NESTING_DEPTH
    with pytest.raises(SchemaError):
        select(_line_cast(), expression)
    with pytest.raises(SchemaError):
        normalize_v1_selector(expression)


def test_regression_mirror_pairs_finds_every_pair_within_tolerance() -> None:
    """Pairs spots within tolerance even when the min-sum plan misses them (audit 9.3)."""
    placements = (
        Placement(entity=SpotId("A"), x=-1.0, y=0.0),
        Placement(entity=SpotId("B"), x=-1.0, y=-0.4),
        Placement(entity=SpotId("R1"), x=1.09, y=0.0),
        Placement(entity=SpotId("R2"), x=1.0, y=0.3),
    )
    set_ = Set(id=SetId("1"), counts=0.0, placements=placements)
    assert len(mirror_pairs(set_, "all", tol=MIRROR_TOL_M)) >= 1


def mirror_set(*spots: tuple[str, float, float]) -> Set:
    placements = tuple(Placement(entity=SpotId(name), x=x, y=y) for name, x, y in spots)
    return Set(id=SetId("1"), counts=0.0, placements=placements)


def test_regression_mirror_pairs_keeps_a_pair_the_min_sum_plan_drops() -> None:
    """Keeps both pairs within tolerance where the shortest full plan uses one far edge."""
    set_ = mirror_set(("L1", -1.0, 0.0), ("L2", -1.0, 0.08), ("R1", 1.0, 0.08), ("R2", 1.06, 0.15))
    assert mirror_pairs(set_, "all", tol=MIRROR_TOL_M) == [("L1", "R1"), ("L2", "R2")]


def test_regression_mirror_pairs_pairs_exactly_mirrored_spots() -> None:
    """Pairs spots whose mirror distance is exactly zero, even with zero tolerance."""
    set_ = mirror_set(("L1", -1.0, 0.0), ("L2", -2.0, 1.0), ("R2", 2.0, 1.0), ("R1", 1.0, 0.0))
    assert mirror_pairs(set_, "all", tol=0.0) == [("L1", "R1"), ("L2", "R2")]
    assert mirror_pairs(set_, "all", axis_x=0.5, tol=0.0) == []


def test_regression_mirror_pairs_breaks_ties_by_spot_order() -> None:
    """Pairs the n-th left spot with the n-th right spot when pairings tie."""
    stacked = mirror_set(
        ("L1", -1.0, 0.0),
        ("L2", -1.0, 0.0),
        ("L3", -1.0, 0.0),
        ("R1", 1.0, 0.0),
        ("R2", 1.0, 0.0),
        ("R3", 1.0, 0.0),
    )
    assert mirror_pairs(stacked, "all") == [("L1", "R1"), ("L2", "R2"), ("L3", "R3")]
    reordered = ["L2", "L1", "L3", "R3", "R1", "R2"]
    assert mirror_pairs(stacked, reordered) == [("L2", "R3"), ("L1", "R1"), ("L3", "R2")]
    fork = mirror_set(("L", -1.0, 0.0), ("R1", 1.0, 0.05), ("R2", 1.0, -0.05))
    assert mirror_pairs(fork, "all") == [("L", "R1")]
    assert mirror_pairs(fork, ["R2", "L", "R1"]) == [("L", "R2")]
    spare_left = ["L1", "L2", "L3", "R1", "R2"]
    assert mirror_pairs(stacked, spare_left) == [("L1", "R1"), ("L2", "R2")]


def test_regression_mirror_pairs_without_edges_is_empty() -> None:
    """Returns no pairs when no right spot lies within tolerance of a mirror image."""
    set_ = mirror_set(("L1", -1.0, 0.0), ("L2", -3.0, 0.0), ("R1", 1.5, 0.0), ("R2", 3.0, 0.5))
    assert mirror_pairs(set_, "all", tol=MIRROR_TOL_M) == []
    assert mirror_pairs(set_, "all", tol=0.0) == []
    assert mirror_pairs(set_, ["L1", "L2"]) == []


@pytest.mark.parametrize("tol", [-1.0, -1e-12, math.nan, math.inf, -math.inf])
def test_regression_mirror_pairs_refuses_a_negative_or_non_finite_tolerance(tol: float) -> None:
    """Raises SchemaError naming the tolerance, before looking at any spot."""
    one_sided = mirror_set(("L1", -1.0, 0.0))
    for set_ in (mirror_set(("L1", -1.0, 0.0), ("R1", 1.0, 0.0)), one_sided):
        with pytest.raises(SchemaError) as info:
            mirror_pairs(set_, "all", tol=tol)
        assert str(info.value.context["tol"]) == str(tol)
        assert info.value.hint == "tol is a finite distance in meters, 0 or more"


MIRROR_ROWS = 40
MIRROR_COLUMNS = 50
MIRROR_SPACING_M = 1.0
MIRROR_PEAK_BUDGET_BYTES = 8_000_000


def mirror_grid() -> Set:
    return mirror_set(
        *(
            (f"{side}{row}-{col}", sign * (col + 1) * MIRROR_SPACING_M, row * MIRROR_SPACING_M)
            for side, sign in (("L", -1.0), ("R", 1.0))
            for row in range(MIRROR_ROWS)
            for col in range(MIRROR_COLUMNS)
        )
    )


def test_regression_mirror_pairs_memory_follows_candidate_pairs() -> None:
    """Pairs 2,000 spots per side within a budget far below one left-by-right distance table."""
    set_ = mirror_grid()
    tracemalloc.start()
    try:
        pairs = mirror_pairs(set_, "all", tol=MIRROR_TOL_M)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(pairs) == MIRROR_ROWS * MIRROR_COLUMNS
    assert all(left[1:] == right[1:] for left, right in pairs)
    assert peak < MIRROR_PEAK_BUDGET_BYTES


def test_regression_mirror_pairs_solves_only_candidate_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hands the solver one edge per candidate pair plus one per left spot, not left by right."""
    graph_sizes: list[int] = []

    def recording_solve(graph: csr_array) -> object:
        graph_sizes.append(graph.nnz)
        return min_weight_full_bipartite_matching(graph)

    monkeypatch.setattr("showpy.cast.select.min_weight_full_bipartite_matching", recording_solve)
    left_spots = MIRROR_ROWS * MIRROR_COLUMNS
    assert len(mirror_pairs(mirror_grid(), "all", tol=MIRROR_TOL_M)) == left_spots
    assert graph_sizes == [2 * left_spots]


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_split_rejects_absurd_part_counts_quickly() -> None:
    """Rejects an absurd part count instead of building 100,000 empty groups (audit 9.3; P0 #8)."""
    tracemalloc.start()
    try:
        with contextlib.suppress(ShowPyError):
            split(["a"], HUGE_PARTS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < SPLIT_PEAK_BUDGET_BYTES


def test_regression_select_set_input_ignores_hash_seed() -> None:
    """Returns one order for a Python set under every hash seed (audit 9.3; P0 #9)."""
    outputs = outputs_under_hash_seeds(SET_SELECT_SCRIPT)
    assert len(set(outputs)) == 1, outputs


def test_select_accepts_nesting_at_the_depth_cap() -> None:
    expression = "(" * MAX_DEPTH + "T1" + ")" * MAX_DEPTH
    assert select(_line_cast(), expression).ids == ("T1",)
    with pytest.raises(SchemaError):
        select(_line_cast(), "(" + expression + ")")


def test_regression_select_rejects_non_string_ids_in_a_set() -> None:
    """Rejects a set holding a non-string with a ShowPyError, not a TypeError."""
    with pytest.raises(ShowPyError):
        select(_line_cast(), {"T1", 3})  # type: ignore[arg-type]


def test_regression_select_rejects_over_long_expressions() -> None:
    """Rejects text over MAX_TEXT_CHARS before tokenizing it."""
    with pytest.raises(SchemaError):
        select(_line_cast(), "T1 " * MAX_TEXT_CHARS)


def test_regression_split_rejects_more_parts_than_spots() -> None:
    """Rejects k above the selection size instead of padding with empty groups."""
    with pytest.raises(SchemaError):
        split(["A", "B"], 3)
    assert [p.ids for p in split([], 1)] == [()]


DEEP_NESTING = 10_000


@pytest.mark.parametrize(
    ("expr", "position", "readings"),
    [
        ("T1 + T2 & T2", 8, "write (T1 + T2) & T2 or T1 + (T2 & T2)"),
        ("all - T1 & T2", 9, "write (all - T1) & T2 or all - (T1 & T2)"),
        ("T2 & T2 + T1", 8, "write (T2 & T2) + T1 or T2 & (T2 + T1)"),
        ("T* & (T1 + T2 - T10 & T1)", 20, "write (T1 + T2 - T10) & T1 or T1 + T2 - (T10 & T1)"),
        ("T1 + T2 & (T2 + T1)", 8, "write (T1 + T2) & (T2 + T1) or T1 + (T2 & (T2 + T1))"),
        ("T1 + T2 &", 8, "write (T1 + T2) & ... or T1 + (T2 & ...)"),
    ],
)
def test_regression_mixed_operators_raise_with_both_readings(
    expr: str, position: int, readings: str
) -> None:
    """Refuses & mixed with + or - at one level, naming the second kind and both readings."""
    with pytest.raises(SchemaError) as info:
        select(mixed_cast(), expr)
    assert info.value.code == ErrorCode.MIXED_OPERATORS
    assert info.value.context["position"] == position
    assert info.value.hint == readings


def test_parse_selector_lists_words_and_operators_in_postfix_order() -> None:
    ast = parse_selector("T1 - (T2 + T10) - TB1")
    assert [token.text for token in ast.program] == ["T1", "T2", "T10", "+", "-", "TB1", "-"]
    assert ast.text == "T1 - (T2 + T10) - TB1"


def test_select_keeps_the_left_order_then_new_right_ids() -> None:
    assert ids(mixed_cast(), "T10 + T1-T2 + T10") == ("T10", "T1", "T2")
    assert ids(mixed_cast(), "(T10 + T2 + T1) & T*") == ("T10", "T2", "T1")
    assert ids(mixed_cast(), "(T10 + T2 + T1) - T2") == ("T10", "T1")


def test_regression_select_depth_10000_raises_schema_error_not_recursion_error() -> None:
    """Rejects 10,000 nested parentheses with SchemaError; the parser keeps no call stack."""
    expression = "(" * DEEP_NESTING + "T1" + ")" * DEEP_NESTING
    with pytest.raises(SchemaError) as info:
        parse_selector(expression)
    assert (info.value.context["what"], info.value.context["cap"]) == ("nesting depth", MAX_DEPTH)
    with pytest.raises(SchemaError):
        select(_line_cast(), expression)


@pytest.mark.parametrize(
    ("stored", "normalized"),
    [
        ("T1 + T2 & T2", "(T1 + T2) & T2"),
        ("T1+T2&T2", "(T1 + T2) & T2"),
        ("T1+T2-T10", "T1 + T2-T10"),
        ("T1 -T2", "T1 - T2"),
        ("T1 + T2 & T10 - T1", "((T1 + T2) & T10) - T1"),
        ("(T1)+(T2)&(T2)", "((T1) + (T2)) & (T2)"),
        ("T* & (T1 + T2 & T10)", "T* & ((T1 + T2) & T10)"),
        ("tag:soloist&T*", "tag:soloist & T*"),
    ],
)
def test_normalize_v1_selector_keeps_the_v1_reading(stored: str, normalized: str) -> None:
    assert normalize_v1_selector(stored) == normalized
    assert parse_selector(normalized).text == normalized


@pytest.mark.parametrize("stored", ["T1 + T2", "T1-T10 - T2", "(T1 + T2) & T2", "all", "T1  +  T2"])
def test_normalize_v1_selector_leaves_v2_text_unchanged(stored: str) -> None:
    assert normalize_v1_selector(stored) == stored


@pytest.mark.parametrize("stored", ["", "T1 +", "(T1", "T1)", "T1 T2", "+ T1", "T1 +-T2", "()"])
def test_normalize_v1_selector_refuses_text_v1_could_not_parse(stored: str) -> None:
    with pytest.raises(SchemaError):
        normalize_v1_selector(stored)


def test_regression_select_rejects_a_set_of_non_strings_with_schema_error() -> None:
    """Rejects a set of ints with SchemaError naming the value, before sorting it."""
    with pytest.raises(SchemaError) as info:
        select(_line_cast(), {1, 2})  # type: ignore[arg-type]
    assert info.value.context["value"] in {"1", "2"}


def test_regression_digit_tails_over_64_characters_raise_schema_error() -> None:
    """Splits a 64-digit tail and refuses a 65-digit one in IDs, labels and range words."""
    fits, too_long = "1" * ID_MAX_LENGTH, "1" * (ID_MAX_LENGTH + 1)
    assert split_id(f"T{fits}") == ("T", int(fits))
    assert label_prefix(f"T{fits}") == "T"
    for call in (lambda: split_id(f"T{too_long}"), lambda: label_prefix(f"T{too_long}")):
        with pytest.raises(SchemaError) as info:
            call()
        assert info.value.context["digits"] == ID_MAX_LENGTH + 1
    with pytest.raises(SchemaError):
        select(_line_cast(), f"T1-T{too_long}")


LARGE_CAST_SPOTS = 40_000
LONG_CHAIN_TERMS = 20_000
LONG_CHAIN_BUDGET_S = 5


@pytest.fixture(scope="module")
def large_cast() -> Cast:
    return Cast(
        groups={"g": Group(key="g", label_prefix="T")},
        spots=tuple(Spot(id=SpotId(f"T{i}"), group="g") for i in range(1, LARGE_CAST_SPOTS + 1)),
    )


@fails_after(LONG_CHAIN_BUDGET_S)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_long_operator_chains_cost_only_their_right_operands(large_cast: Cast) -> None:
    """Evaluates 20,000-term `-` and `+` chains on 40,000 spots without copying the left side.

    Copying the left operand per operator made `all - T1 - ... - T20000` take about a minute.
    """
    terms = range(1, LONG_CHAIN_TERMS + 1)
    removed = select(large_cast, "all" + "".join(f" - T{i}" for i in terms))
    assert removed.ids[0] == f"T{LONG_CHAIN_TERMS + 1}"
    assert len(removed.ids) == LARGE_CAST_SPOTS - LONG_CHAIN_TERMS
    added = select(large_cast, " + ".join(f"T{i}" for i in reversed(terms)))
    assert added.ids[:2] == (f"T{LONG_CHAIN_TERMS}", f"T{LONG_CHAIN_TERMS - 1}")
    assert len(added.ids) == LONG_CHAIN_TERMS
