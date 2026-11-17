#!/usr/bin/env python3

"""
test_match_properties.py --- checks matching against brute force on small seeded random instances

Contains:
    test_applied_matches_survive_a_form_rebuild: rebuilding a form after apply_match moves nobody
"""

import itertools

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.choreo.form_place import rebuild
from showpy.choreo.forms import block_form, circle_form, line_form
from showpy.choreo.match import bottleneck_value, match, match_by_order
from showpy.choreo.match_ops import apply_match
from showpy.choreo.match_solve import FloatArray, count_crossings
from showpy.choreo.models import Form, MatchObjective, MatchOptions, MatchResult
from showpy.core.types import Points
from showpy.core.util import approx_equal, seeded_rng
from tests.fixtures.shows import two_set_show

TOL = 1e-9
MAX_SPOTS = 5
EXTRA_SLOTS = 2
FIELD = 10.0
INTEGER_GRID = 6
OBJECTIVES: tuple[MatchObjective, ...] = ("sum", "sum_squares", "bottleneck", "bottleneck_then_sum")


@st.composite
def instances(draw: st.DrawFn) -> tuple[Points, Points]:
    seed = draw(st.integers(min_value=0, max_value=2**31))
    n = draw(st.integers(min_value=1, max_value=MAX_SPOTS))
    m = n + draw(st.integers(min_value=0, max_value=EXTRA_SLOTS))
    rng = seeded_rng(seed)
    if draw(st.booleans()):
        return rng.integers(0, INTEGER_GRID, (n, 2)).astype(float), rng.integers(
            0, INTEGER_GRID, (m, 2)
        ).astype(float)
    return rng.random((n, 2)) * FIELD, rng.random((m, 2)) * FIELD


def _distances(starts: Points, slots: Points) -> FloatArray:
    return np.linalg.norm(starts[:, None, :] - slots[None, :, :], axis=2)


def _brute(starts: Points, slots: Points) -> list[tuple[float, float, float]]:
    d = _distances(starts, slots)
    rows = range(len(starts))
    out = []
    for cols in itertools.permutations(range(len(slots)), len(starts)):
        moves = d[list(rows), list(cols)]
        out.append((float(moves.max()), float(moves.sum()), float((moves**2).sum())))
    return out


def _is_one_to_one(result: MatchResult, n: int, m: int) -> bool:
    used = list(result.assignment.values())
    return len(result.assignment) == n and len(set(used)) == n and all(0 <= j < m for j in used)


@settings(max_examples=60, deadline=None)
@given(instances())
def test_bottleneck_equals_brute_force(case: tuple[Points, Points]) -> None:
    starts, slots = case
    best = min(b[0] for b in _brute(starts, slots))
    result = match(starts, slots, options=MatchOptions(objective="bottleneck"))
    assert approx_equal(result.max_distance, best, TOL)
    assert approx_equal(bottleneck_value(_distances(starts, slots)), best, TOL)


@settings(max_examples=60, deadline=None)
@given(instances())
def test_bottleneck_then_sum_equals_brute_force(case: tuple[Points, Points]) -> None:
    starts, slots = case
    brute = _brute(starts, slots)
    cap = min(b[0] for b in brute)
    best_total = min(b[1] for b in brute if b[0] <= cap + TOL)
    result = match(starts, slots)
    assert approx_equal(result.max_distance, cap, TOL)
    assert approx_equal(result.total_distance, best_total, 1e-7)


@settings(max_examples=60, deadline=None)
@given(instances())
def test_sum_equals_brute_force(case: tuple[Points, Points]) -> None:
    starts, slots = case
    brute = _brute(starts, slots)
    result = match(starts, slots, options=MatchOptions(objective="sum"))
    assert approx_equal(result.total_distance, min(b[1] for b in brute), 1e-7)
    squares = match(starts, slots, options=MatchOptions(objective="sum_squares"))
    moves = np.linalg.norm(squares.target_points() - starts, axis=1)
    assert approx_equal(float((moves**2).sum()), min(b[2] for b in brute), 1e-7)


@settings(max_examples=60, deadline=None)
@given(instances(), st.sampled_from(OBJECTIVES))
def test_result_is_a_valid_one_to_one_assignment(
    case: tuple[Points, Points], objective: MatchObjective
) -> None:
    starts, slots = case
    result = match(starts, slots, options=MatchOptions(objective=objective))
    assert _is_one_to_one(result, len(starts), len(slots))
    assert set(result.unassigned_slots) == set(range(len(slots))) - set(result.assignment.values())
    assert approx_equal(
        result.target_points(), slots[[result.assignment[e] for e in result.entities]]
    )
    assert result.crossings == count_crossings(starts, result.target_points())


@settings(max_examples=40, deadline=None)
@given(instances())
def test_preserve_order_equals_brute_force_over_increasing_assignments(
    case: tuple[Points, Points],
) -> None:
    starts, slots = case
    d = _distances(starts, slots)
    rows = list(range(len(starts)))
    best = min(
        float(d[rows, list(cols)].sum())
        for cols in itertools.combinations(range(len(slots)), len(starts))
    )
    options = MatchOptions(objective="sum", preserve_order=True)
    result = match(starts, slots, options=options)
    cols = [result.assignment[str(i)] for i in rows]
    assert cols == sorted(cols)
    assert approx_equal(result.total_distance, best, 1e-7)


@settings(max_examples=30, deadline=None)
@given(instances())
def test_match_is_deterministic(case: tuple[Points, Points]) -> None:
    starts, slots = case
    assert match(starts, slots) == match(starts, slots)


MOVE_TOL = 1e-6
TID = ("1", "2")
SPOTS = ("T1", "T2", "T3", "T4")


def _fillable_form(kind: str) -> Form:
    if kind == "line":
        return line_form("f", [], (0, 12), (6, 12), n=len(SPOTS), set_id="2")
    if kind == "arc":
        return circle_form("f", [], (3, 10), 3.0, n=len(SPOTS), set_id="2")
    return block_form("f", [], (3, 14), 2, 2, 2.0, set_id="2")


@settings(max_examples=40, deadline=None)
@given(st.sampled_from(("line", "arc", "block")), st.permutations(SPOTS))
def test_applied_matches_survive_a_form_rebuild(kind: str, order: list[str]) -> None:
    form = _fillable_form(kind)
    show = two_set_show().replace(forms={"f": form})
    permuted = match_by_order(order, form, show.set("1"))
    applied = apply_match(show, TID, permuted)
    rebuilt = rebuild(applied, "f")
    for spot in SPOTS:
        assert approx_equal(
            np.array(rebuilt.set("2").get(spot).point),
            np.array(applied.set("2").get(spot).point),
            MOVE_TOL,
        )
