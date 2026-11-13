#!/usr/bin/env python3

"""
test_match_ops.py --- tests applying matches, swapping spots and swap suggestions on a show

Contains:
    test_regression_apply_match_updates_order_and_facing_of_formed_spots: rewrites order and facing
    test_regression_apply_match_updates_form_members_like_swap: keeps Form.members in step with the
    test_regression_clear_rows_keeps_transition_order: leaves show.transitions in their original
"""

import pytest

from showpy.choreo.form_place import rebuild
from showpy.choreo.forms import circle_form, line_form, slot_poses
from showpy.choreo.match import match, match_by_order
from showpy.choreo.match_ops import apply_match, clear_rows, suggest_swaps, swap
from showpy.choreo.models import FacingSpec, Form, TransitionSpec
from showpy.core.errors import LockedError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show
from tests.helpers import pin_spot, with_curve_rows

TID = ("1", "2")
ROW_SPOTS = ("T1", "T2")


def test_apply_match_writes_targets_and_drops_rows() -> None:
    show = with_curve_rows(two_set_show(), ROW_SPOTS)
    result = match(show.set("1"), [(6.0, 3.0), (4.0, 3.0), (2.0, 3.0), (0.0, 3.0)])
    applied = apply_match(show, TID, result)
    assert applied.set("2").get("T1").point == (0.0, 3.0)
    assert applied.transition(TID).rows == {}
    assert show.set("2").get("T1").point == (0.0, 9.0)


def test_apply_match_respects_pins_and_locked_rows() -> None:
    result = match(two_set_show().set("1"), [(0.0, 3.0), (2.0, 3.0), (4.0, 3.0), (6.0, 3.0)])
    with pytest.raises(LockedError):
        apply_match(pin_spot(two_set_show(), "2", "T1"), TID, result)
    with pytest.raises(LockedError):
        apply_match(with_curve_rows(two_set_show(), ROW_SPOTS, locked=True), TID, result)
    forced = apply_match(
        with_curve_rows(two_set_show(), ROW_SPOTS, locked=True), TID, result, force=True
    )
    assert forced.transition(TID).rows == {}


def test_apply_match_skips_pinned_spot_already_on_target() -> None:
    show = pin_spot(two_set_show(), "2", "T1")
    result = match(show.set("1"), [(0.0, 9.0), (2.0, 3.0), (4.0, 3.0), (6.0, 3.0)])
    assert apply_match(show, TID, result).set("2").get("T2").point == (2.0, 3.0)


def test_apply_match_rejects_non_consecutive_transition() -> None:
    result = match(two_set_show().set("1"), [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)])
    with pytest.raises(UnknownRefError):
        apply_match(two_set_show(), ("2", "1"), result)


def test_clear_rows_keeps_other_rows() -> None:
    cleared = clear_rows(with_curve_rows(two_set_show(), ROW_SPOTS), TID, ["T1"])
    assert list(cleared.transition(TID).rows) == ["T2"]


def test_swap_exchanges_places_forms_and_drops_rows() -> None:
    show = with_curve_rows(two_set_show(), ROW_SPOTS)
    form = line_form("front", ["T1", "T2", "T3", "T4"], (0, 9), (6, 9), set_id="2")
    show = show.model_copy(update={"forms": {"front": form}})
    swapped = swap(show, "2", "T1", "T2")
    assert swapped.set("2").get("T1").x == 2.0
    assert swapped.set("2").get("T2").x == 0.0
    assert swapped.forms["front"].members == ("T2", "T1", "T3", "T4")
    assert list(swapped.transition(TID).rows) == []


def test_swap_respects_locked_forms_and_pins() -> None:
    form = line_form("f", ["T1", "T2"], (0, 9), (2, 9), set_id="2").model_copy(
        update={"locked": True}
    )
    show = two_set_show().model_copy(update={"forms": {"f": form}})
    with pytest.raises(LockedError):
        swap(show, "2", "T1", "T2")
    assert swap(show, "2", "T1", "T2", force=True).forms["f"].members == ("T2", "T1")
    with pytest.raises(LockedError):
        swap(pin_spot(two_set_show(), "2", "T3"), "2", "T3", "T4")


def test_suggest_swaps_finds_the_swap_that_undoes_a_tangle() -> None:
    tangled = swap(two_set_show(), "2", "T1", "T4")
    suggestions = suggest_swaps(tangled, TID)
    assert suggestions[0][:2] == ("T1", "T4")
    assert suggestions[0][2] > 3.0


def test_suggest_swaps_returns_nothing_for_a_clean_move() -> None:
    assert suggest_swaps(two_set_show(), TID) == []


def test_suggest_swaps_ranks_crossing_removal_without_gain() -> None:
    tangled = swap(two_set_show(), "2", "T2", "T3")
    best = suggest_swaps(tangled, TID, k=1)
    assert best[0][:2] == ("T2", "T3")


RING_CENTER = (3.0, 10.0)
RING_RADIUS = 3.0
REVERSED_RING = ["T4", "T3", "T2", "T1"]
POSE_TOL = 1e-6
NEXT_TRANSITION_COUNTS = 4.0


def _ring_show() -> tuple[Show, Form]:
    ring = circle_form(
        "ring",
        ["T1", "T2", "T3", "T4"],
        RING_CENTER,
        RING_RADIUS,
        facing=FacingSpec(mode="outward"),
        set_id="2",
    )
    show = two_set_show().model_copy(update={"forms": {"ring": ring}})
    return rebuild(show, "ring"), ring


@pytest.mark.defect("UP-9.6-01")
@pytest.mark.xfail(strict=True, reason="UP-9.6-01")
def test_regression_apply_match_updates_order_and_facing_of_formed_spots() -> None:
    """Rewrites order and facing with the new slot, not only x and y (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    result = match_by_order(REVERSED_RING, ring, show.set("1"))
    placed = apply_match(show, TID, result).set("2")
    poses = slot_poses(ring)[1]
    for entity, slot in result.assignment.items():
        assert placed.get(entity).order == slot
        assert approx_equal(placed.get(entity).facing, poses[slot], POSE_TOL)


@pytest.mark.defect("UP-9.6-02")
@pytest.mark.xfail(strict=True, reason="UP-9.6-02")
def test_regression_apply_match_updates_form_members_like_swap() -> None:
    """Keeps Form.members in step with matched slots so rebuild keeps them (audit 9.6; P0 #12)."""
    show, _ = _ring_show()
    ring = show.forms["ring"]
    result = match_by_order(REVERSED_RING, ring, show.set("1"))
    matched = apply_match(show, TID, result)
    assert matched.forms["ring"].members == tuple(REVERSED_RING)
    kept = matched.set("2").get("T1").point
    assert approx_equal(rebuild(matched, "ring").set("2").get("T1").point, kept, POSE_TOL)


@pytest.mark.defect("UP-9.6-24")
@pytest.mark.xfail(strict=True, reason="UP-9.6-24")
def test_regression_clear_rows_keeps_transition_order() -> None:
    """Leaves show.transitions in their original order when rows are cleared (audit 9.6)."""
    show = with_curve_rows(two_set_show(), ROW_SPOTS)
    later = TransitionSpec(id=("2", "3"), counts=NEXT_TRANSITION_COUNTS)
    show = show.model_copy(update={"transitions": (show.transitions[0], later)})
    cleared = clear_rows(show, TID, ROW_SPOTS)
    assert [t.id for t in cleared.transitions] == [t.id for t in show.transitions]
