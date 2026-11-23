#!/usr/bin/env python3

"""
test_match.py --- tests spot-to-slot matching: objectives, constraints, penalties and errors

Contains:
    test_regression_match_reports_moves_longer_than_max_coord: reports a 2e6 m move between legal
        points at opposite coordinate bounds
    test_regression_match_by_order_rejects_duplicate_spots: rejects a repeated spot instead of
        collapsing it to one
    test_regression_match_wraps_a_failing_cost_fn: raises MatchError when a cost_fn callback raises
    test_regression_match_names_the_nan_start: raises SchemaError, or a MatchError naming the NaN
        start, not an empty one
    test_match_records_form_slots_across_concatenated_forms: maps columns to (form, local slot)
    test_match_onto_points_records_no_form_slots: leaves slots empty for point targets
    test_match_by_order_records_form_slots: maps each spot to its slot in the given form
"""

import math
from typing import cast

import numpy as np
import pytest

from e8s.choreo.forms import line_form
from e8s.choreo.match import bottleneck_value, cost_matrix, match, match_by_order
from e8s.choreo.match_models import CostFn, MatchOptions, MatchWeights
from e8s.choreo.models import PlacementPatch
from e8s.core.caps import MAX_COORD_M
from e8s.core.errors import E8sError, MatchError, SchemaError, UnknownRefError
from e8s.core.ids import SpotId
from e8s.core.types import Point
from e8s.core.util import approx_equal
from tests.helpers import make_facing_set

TOL = 1e-9


def test_bottleneck_then_sum_minimizes_largest_move_first() -> None:
    starts = [(0.0, 0.0), (1.0, 0.0)]
    slots = [(1.0, 0.0), (2.0, 0.0)]
    result = match(starts, slots)
    assert result.assignment == {"0": 0, "1": 1}
    assert approx_equal(result.max_distance, 1.0, abs_tol=TOL)
    assert approx_equal(result.total_distance, 2.0, abs_tol=TOL)


def test_sum_and_bottleneck_disagree_on_a_known_instance() -> None:
    starts = [(0.0, 0.0), (-7.0 / 6.0, math.sqrt(275.0 / 36.0))]
    slots = [(0.0, 0.0), (3.0, 0.0)]
    by_sum = match(starts, slots, options=MatchOptions(objective="sum"))
    assert by_sum.assignment == {"0": 0, "1": 1}
    assert approx_equal(by_sum.total_distance, 5.0, abs_tol=TOL)
    by_bottleneck = match(starts, slots)
    assert by_bottleneck.assignment == {"0": 1, "1": 0}
    assert approx_equal(by_bottleneck.max_distance, 3.0, abs_tol=TOL)
    assert approx_equal(by_bottleneck.total_distance, 6.0, abs_tol=TOL)
    assert match(starts, slots, options=MatchOptions(objective="bottleneck")).max_distance == (
        by_bottleneck.max_distance
    )


def test_bottleneck_value_matches_known_answer() -> None:
    assert bottleneck_value([[5.0, 1.0], [1.0, 9.0]]) == 1.0
    assert bottleneck_value([[5.0, 2.0, 7.0], [3.0, 8.0, 4.0]]) == 3.0


def test_bottleneck_value_rejects_infeasible_matrix() -> None:
    with pytest.raises(MatchError):
        bottleneck_value([[math.inf, 1.0], [math.inf, 2.0]])


def test_rectangular_leaves_unassigned_slots() -> None:
    result = match([(0.0, 0.0)], [(5.0, 0.0), (0.0, 1.0), (9.0, 9.0)])
    assert result.assignment == {"0": 1}
    assert result.unassigned_slots == (0, 2)
    assert "2 slots left open" in result.explain


def test_more_spots_than_slots_raises() -> None:
    with pytest.raises(MatchError, match="only 1 slots"):
        match([(0.0, 0.0), (1.0, 0.0)], [(0.0, 0.0)])


def test_result_does_not_depend_on_input_order() -> None:
    starts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    slots = [(1.0, 1.0), (0.0, 1.0), (2.0, 1.0)]
    forward = match(starts, slots, entities=["A", "B", "C"])
    backward = match(starts[::-1], slots, entities=["C", "B", "A"])
    assert forward.assignment == backward.assignment
    assert forward.entities == ("A", "B", "C")


def test_from_set_uses_on_field_spots_and_facings() -> None:
    src = make_facing_set(("A", 0.0, 0.0, 0.0), ("B", 5.0, 0.0, 0.0))
    off = src.with_placements({"B": PlacementPatch(on_field=False)})
    result = match(off, [(0.0, 1.0), (5.0, 1.0)])
    assert result.entities == ("A",)


def test_forms_as_slots_concatenate_in_order() -> None:
    forms = [line_form("a", [], (0, 0), (1, 0), n=2), line_form("b", [], (5, 0), (6, 0), n=2)]
    result = match([(6.0, 0.0)], forms)
    assert result.assignment == {"0": 3}


def test_pinned_forces_assignment() -> None:
    result = match(
        [(0.0, 0.0), (1.0, 0.0)],
        [(0.0, 0.0), (1.0, 0.0)],
        entities=["A", "B"],
        options=MatchOptions(pinned={SpotId("A"): 1}),
    )
    assert result.assignment == {"A": 1, "B": 0}
    assert "pinned A to slot 1" in result.explain


def test_pinned_unknown_spot_and_clash() -> None:
    with pytest.raises(UnknownRefError):
        match(
            [(0.0, 0.0)],
            [(0.0, 0.0)],
            entities=["A"],
            options=MatchOptions(pinned={SpotId("Z"): 0}),
        )
    with pytest.raises(MatchError, match="both pinned"):
        match(
            [(0.0, 0.0), (1.0, 0.0)],
            [(0.0, 0.0), (1.0, 0.0)],
            entities=["A", "B"],
            options=MatchOptions(pinned={SpotId("A"): 0, SpotId("B"): 0}),
        )


def test_forbid_and_infeasible_names_blocking_constraint() -> None:
    options = MatchOptions(forbid=((SpotId("A"), 0), (SpotId("A"), 1)))
    with pytest.raises(MatchError) as caught:
        match([(0.0, 0.0)], [(0.0, 0.0), (1.0, 0.0)], entities=["A"], options=options)
    assert caught.value.context["blocking"] == ["forbid"]
    assert caught.value.context["spots_without_slot"] == ["A"]


def test_infeasible_names_only_the_constraint_whose_removal_helps() -> None:
    options = MatchOptions(pinned={SpotId("A"): 0}, max_speed=1.0, counts=1.0)
    with pytest.raises(MatchError) as caught:
        match(
            [(0.0, 0.0), (0.5, 0.0)],
            [(0.0, 0.0), (9.0, 0.0)],
            entities=["A", "B"],
            options=options,
        )
    assert caught.value.context["blocking"] == ["max_speed"]


def test_no_backward_forbids_travel_against_facing() -> None:
    src = make_facing_set(("A", 0.0, 5.0, 0.0), ("B", 1.0, 5.0, 0.0))
    slots = [(0.0, 3.0), (1.0, 7.0), (5.0, 3.0)]
    free = match(src, slots, options=MatchOptions(objective="sum"))
    assert free.assignment[SpotId("B")] == 1
    strict = match(src, slots, options=MatchOptions(objective="sum", no_backward=True))
    assert 1 not in strict.assignment.values()


def test_no_backward_penalty_adds_meters() -> None:
    src = make_facing_set(("A", 0.0, 5.0, 0.0))
    cost = cost_matrix(src, [(0.0, 6.0), (0.0, 4.0)], MatchOptions(no_backward=2.5))
    assert approx_equal(cost, [[3.5, 1.0]], abs_tol=TOL)


def test_backward_and_turn_weights() -> None:
    src = make_facing_set(("A", 0.0, 5.0, 0.0))
    back = cost_matrix(src, [(0.0, 7.0)], MatchOptions(weights=MatchWeights(backward=1.0)))
    assert approx_equal(back, [[4.0]], abs_tol=TOL)
    turn = cost_matrix(src, [(2.0, 5.0)], MatchOptions(weights=MatchWeights(turn=0.01)))
    assert approx_equal(turn, [[2.9]], abs_tol=TOL)


def test_backward_rules_need_facings() -> None:
    with pytest.raises(MatchError, match="facing"):
        match([(0.0, 0.0)], [(0.0, 1.0)], options=MatchOptions(no_backward=True))


def test_max_speed_forbids_fast_moves_and_needs_counts() -> None:
    with pytest.raises(MatchError, match="counts"):
        match([(0.0, 0.0)], [(1.0, 0.0)], options=MatchOptions(max_speed=1.0))
    result = match(
        [(0.0, 0.0), (0.1, 0.0)],
        [(0.0, 0.0), (8.0, 0.0)],
        entities=["A", "B"],
        options=MatchOptions(max_speed=1.0, counts=8.0, objective="sum"),
    )
    assert result.max_speed is not None
    assert result.max_speed <= 1.0 + TOL


def test_keep_groups_uses_slot_group_labels() -> None:
    groups = {"A": "brass", "B": "drums"}
    result = match(
        [(0.0, 0.0), (1.0, 0.0)],
        [(0.0, 0.0), (1.0, 0.0)],
        entities=["A", "B"],
        groups=groups,
        slot_groups=["drums", "brass"],
    )
    assert result.assignment == {"A": 1, "B": 0}
    loose = match(
        [(0.0, 0.0), (1.0, 0.0)],
        [(0.0, 0.0), (1.0, 0.0)],
        entities=["A", "B"],
        options=MatchOptions(keep_groups=False),
        groups=groups,
        slot_groups=["drums", "brass"],
    )
    assert loose.assignment == {"A": 0, "B": 1}


def test_group_slots_reserve_slots_for_a_group() -> None:
    result = match(
        [(0.0, 0.0), (1.0, 0.0)],
        [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)],
        entities=["A", "B"],
        options=MatchOptions(group_slots={"drums": (2,)}),
        groups={"A": "brass", "B": "drums"},
    )
    assert result.assignment == {"A": 0, "B": 2}


def test_group_slots_reject_overlap() -> None:
    with pytest.raises(SchemaError):
        match(
            [(0.0, 0.0)],
            [(0.0, 0.0), (1.0, 0.0)],
            options=MatchOptions(group_slots={"a": (0,), "b": (0,)}),
        )


def test_preserve_order_keeps_slot_order() -> None:
    starts = [(0.0, 0.0), (10.0, 0.0)]
    slots = [(10.0, 1.0), (0.0, 1.0), (5.0, 1.0)]
    result = match(starts, slots, entities=["A", "B"], options=MatchOptions(preserve_order=True))
    assert result.assignment[SpotId("A")] < result.assignment[SpotId("B")]
    assert result.assignment == {"A": 1, "B": 2}


def test_preserve_order_infeasible_names_order() -> None:
    options = MatchOptions(preserve_order=True, pinned={SpotId("A"): 1})
    with pytest.raises(MatchError) as caught:
        match([(0.0, 0.0), (1.0, 0.0)], [(0.0, 0.0), (1.0, 0.0)], ["A", "B"], options)
    blocking = caught.value.context["blocking"]
    assert isinstance(blocking, list)
    assert "preserve_order" in blocking


def test_cost_fn_adds_cost_and_infinity_forbids() -> None:
    def avoid_slot_zero(_entity: str, slot: int, _start: Point, _target: Point) -> float:
        return math.inf if slot == 0 else 0.0

    result = match(
        [(0.0, 0.0)], [(0.0, 0.0), (3.0, 0.0)], options=MatchOptions(cost_fn=avoid_slot_zero)
    )
    assert result.assignment == {"0": 1}
    assert "cost_fn" not in MatchOptions(cost_fn=avoid_slot_zero).model_dump()


def test_cost_fn_rejects_negative_costs() -> None:
    with pytest.raises(SchemaError):
        match([(0.0, 0.0)], [(1.0, 0.0)], options=MatchOptions(cost_fn=lambda *_: -1.0))


def test_crossing_weight_untangles_a_cheaper_crossing() -> None:
    def favour_crossing(entity: str, slot: int, _start: Point, _target: Point) -> float:
        return 1.0 if (entity, slot) in {("0", 1), ("1", 0)} else 0.0

    starts = [(0.0, 0.0), (2.0, 0.0)]
    slots = [(2.0, 2.0), (0.0, 2.0)]
    tangled = match(starts, slots, options=MatchOptions(objective="sum", cost_fn=favour_crossing))
    assert tangled.crossings == 1
    weights = MatchWeights(crossing=10.0)
    options = MatchOptions(objective="sum", cost_fn=favour_crossing, weights=weights)
    untangled = match(starts, slots, options=options)
    assert untangled.crossings == 0
    assert any("swaps" in line for line in untangled.explain)


def test_group_spread_weight_keeps_groups_together() -> None:
    starts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    slots = [(0.0, 5.0), (1.0, 5.0), (2.0, 5.0), (3.0, 5.0)]
    groups = {"0": "a", "1": "b", "2": "a", "3": "b"}
    spread = match(
        starts,
        slots,
        options=MatchOptions(objective="sum", weights=MatchWeights(group_spread=100.0)),
        groups=groups,
    )
    a_slots = sorted(spread.assignment[SpotId(e)] for e in ("0", "2"))
    assert a_slots in ([0, 1], [2, 3])


def test_unknown_weight_is_rejected() -> None:
    with pytest.raises(SchemaError, match="unknown match weight"):
        MatchOptions.model_validate({"weights": {"crosing": 1.0}})


def test_empty_problem_returns_empty_result() -> None:
    result = match(np.zeros((0, 2)), [(0.0, 0.0)])
    assert result.assignment == {}
    assert result.unassigned_slots == (0,)


def test_match_by_order_reports_distances_from_set() -> None:
    src = make_facing_set(("A", 0.0, 0.0, 0.0), ("B", 2.0, 0.0, 0.0))
    form = line_form("f", [], (0, 3), (2, 3), n=2)
    result = match_by_order(["B", "A"], form, src)
    assert result.assignment == {"B": 0, "A": 1}
    assert approx_equal(result.max_distance, math.hypot(2, 3), abs_tol=TOL)
    with pytest.raises(MatchError):
        match_by_order(["A", "B", "C"], form)


PAIR_STARTS = [(0.0, 0.0), (1.0, 0.0)]
PAIR_SLOTS = [(0.0, 1.0), (1.0, 1.0)]


def _failing_cost(entity: str, slot: int, _start: Point, _target: Point) -> float:
    raise RuntimeError(f"cost of {entity} for slot {slot} failed")


def test_regression_match_by_order_rejects_duplicate_spots() -> None:
    """Rejects a repeated spot instead of collapsing it to one (audit 9.6)."""
    with pytest.raises(E8sError):
        match_by_order(["A", "A"], np.asarray(PAIR_SLOTS))


def test_regression_match_wraps_a_failing_cost_fn() -> None:
    """Raises MatchError when a cost_fn callback raises (audit 9.6)."""
    with pytest.raises(MatchError):
        match(PAIR_STARTS, PAIR_SLOTS, options=MatchOptions(cost_fn=_failing_cost))


def test_regression_match_cost_fn_error_is_chained_and_teaches() -> None:
    """Chains the callback's own exception and says what cost_fn must return."""
    with pytest.raises(MatchError, match="cost of 0 for slot 0 failed") as excinfo:
        match(PAIR_STARTS, PAIR_SLOTS, options=MatchOptions(cost_fn=_failing_cost))
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "must return a number" in excinfo.value.hint


def test_regression_match_wraps_a_non_numeric_cost_fn_result() -> None:
    """Reports a cost_fn that returns no number as a MatchError."""
    returns_text = cast(CostFn, lambda *_: "cheap")
    with pytest.raises(MatchError, match="cost_fn failed"):
        match(PAIR_STARTS, PAIR_SLOTS, options=MatchOptions(cost_fn=returns_text))


def test_regression_match_names_the_nan_slot() -> None:
    """Refuses a non-finite slot point and names the slot."""
    with pytest.raises(SchemaError, match="slot 1 is not finite"):
        match(PAIR_STARTS, [(0.0, 1.0), (math.nan, 1.0)])


def test_regression_match_names_the_spot_with_a_non_finite_start() -> None:
    """Names the spot whose start point is not finite."""
    with pytest.raises(SchemaError, match="start of spot '1' is not finite"):
        match([(0.0, 0.0), (math.inf, 0.0)], PAIR_SLOTS)


def test_regression_match_names_the_nan_start() -> None:
    """Raises SchemaError, or a MatchError naming the NaN start, not an empty one (audit 9.6)."""
    with pytest.raises((SchemaError, MatchError)) as excinfo:
        match([(math.nan, 0.0), (1.0, 0.0)], PAIR_SLOTS)
    if isinstance(excinfo.value, MatchError):
        assert "nan" in str(excinfo.value).lower()


def test_match_records_form_slots_across_concatenated_forms() -> None:
    first = line_form("a", [], (0, 0), (2, 0), n=2)
    second = line_form("b", [], (0, 5), (2, 5), n=2)
    result = match([(0.0, 5.0), (2.0, 0.0)], [first, second], entities=["P", "Q"])
    assert result.assignment == {"P": 2, "Q": 1}
    assert result.slots == {"P": ("b", 0), "Q": ("a", 1)}


def test_match_onto_points_records_no_form_slots() -> None:
    assert match([(0.0, 0.0)], [(1.0, 0.0)]).slots == {}


def test_match_by_order_records_form_slots() -> None:
    form = line_form("f", [], (0, 0), (2, 0), n=2)
    assert match_by_order(["B", "A"], form).slots == {"B": ("f", 0), "A": ("f", 1)}
    assert match_by_order(["B", "A"], np.array([[0.0, 0.0], [1.0, 0.0]])).slots == {}


def test_regression_match_reports_moves_longer_than_max_coord() -> None:
    """Reports the 2e6 m move of a spot crossing the full coordinate range."""
    result = match([(-MAX_COORD_M, 0.0)], [(MAX_COORD_M, 0.0)])
    assert result.max_distance == 2 * MAX_COORD_M
    assert result.total_distance == 2 * MAX_COORD_M
