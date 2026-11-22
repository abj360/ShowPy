#!/usr/bin/env python3

"""
test_match_ops.py --- tests applying matches, swapping spots and swap suggestions on a show

Contains:
    test_regression_apply_match_updates_order_and_facing_of_formed_spots: rewrites order and facing
        with the new slot, not only x and y
    test_regression_apply_match_updates_form_members_like_swap: keeps Form.members in step with
        matched slots so rebuild keeps them
    test_regression_apply_match_rejects_a_text_form_left_short: raises MEMBER_COUNT_MISMATCH when
        a match takes a text form's last member to another form
    test_regression_apply_match_rejects_a_slot_held_by_an_unmatched_member: raises MatchError
        instead of putting two spots in one slot
    test_regression_apply_match_rejects_leaving_an_interior_slot_empty: raises MatchError
        when a matched spot leaves a hole between members
    test_regression_apply_match_respects_locked_forms: needs force to change a locked form
    test_regression_apply_match_rejects_a_form_of_another_set: raises MatchError before writing
    test_regression_apply_match_rejects_an_unknown_form: raises UnknownRefError
    test_regression_apply_match_rejects_an_out_of_range_slot: raises MatchError for a slot past
        the form's last slot or below zero
    test_regression_apply_match_reports_a_pinned_spot_before_a_bad_slot: raises LockedError ahead
        of the MatchError a bad slot would raise
    test_regression_apply_match_names_a_pinned_row_on_a_matched_slot: raises MatchError naming
        the pinned spot that already stands on the slot
    test_regression_apply_match_names_the_spot_holding_a_slot: raises MatchError naming both spots
    test_regression_apply_match_ignores_a_form_the_set_no_longer_places: leaves a stale form
        alone and does not refuse the match because of it
    test_regression_apply_match_rejects_slots_of_a_form_the_set_no_longer_places: raises
        MatchError instead of writing a stale form
    test_regression_swap_ignores_a_form_the_set_no_longer_places: neither swaps the members of a
        stale form nor raises LockedError for a locked one
    test_apply_match_leaves_unchanged_slots_alone: skips spots already in their slot
    test_apply_match_trims_trailing_empty_slots: drops trailing holes from members
    test_regression_clear_rows_keeps_transition_order: leaves show.transitions in their original
        order when rows are cleared
"""

import pytest

from showpy.choreo.form_models import FacingSpec, Form
from showpy.choreo.form_place import place, rebuild
from showpy.choreo.forms import circle_form, line_form, slot_poses, text_form
from showpy.choreo.match import match, match_by_order
from showpy.choreo.match_models import MatchResult
from showpy.choreo.match_ops import apply_match, clear_rows, suggest_swaps, swap, with_set
from showpy.choreo.path_models import TransitionSpec
from showpy.core.errors import LockedError, MatchError, SpacingError, UnknownRefError
from showpy.core.ids import FormId, SpotId, make_tid
from showpy.core.util import approx_equal
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show
from tests.helpers import pin_spot, replaced_form_show, with_curve_rows

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
    assert swapped.forms[FormId("front")].members == ("T2", "T1", "T3", "T4")
    assert list(swapped.transition(TID).rows) == []


def test_swap_respects_locked_forms_and_pins() -> None:
    form = line_form("f", ["T1", "T2"], (0, 9), (2, 9), set_id="2").model_copy(
        update={"locked": True}
    )
    show = two_set_show().model_copy(update={"forms": {"f": form}})
    with pytest.raises(LockedError):
        swap(show, "2", "T1", "T2")
    assert swap(show, "2", "T1", "T2", force=True).forms[FormId("f")].members == ("T2", "T1")
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


def test_regression_apply_match_updates_order_and_facing_of_formed_spots() -> None:
    """Rewrites order and facing with the new slot, not only x and y (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    result = match_by_order(REVERSED_RING, ring, show.set("1"))
    placed = apply_match(show, TID, result).set("2")
    poses = slot_poses(ring)[1]
    for entity, slot in result.assignment.items():
        assert placed.get(entity).order == slot
        assert approx_equal(placed.get(entity).facing, poses[slot], abs_tol=POSE_TOL)


def test_regression_apply_match_updates_form_members_like_swap() -> None:
    """Keeps Form.members in step with matched slots so rebuild keeps them (audit 9.6; P0 #12)."""
    show, _ = _ring_show()
    ring = show.forms[FormId("ring")]
    result = match_by_order(REVERSED_RING, ring, show.set("1"))
    matched = apply_match(show, TID, result)
    assert matched.forms[FormId("ring")].members == tuple(REVERSED_RING)
    kept = matched.set("2").get("T1").point
    assert approx_equal(rebuild(matched, "ring").set("2").get("T1").point, kept, abs_tol=POSE_TOL)


def _ring_without(member: str) -> tuple[Show, Form]:
    show, ring = _ring_show()
    smaller = ring.replace(members=tuple(m for m in ring.members if m != member))
    return show.replace(forms={"ring": smaller}), smaller


def test_regression_apply_match_rejects_a_slot_held_by_an_unmatched_member() -> None:
    """Refuses to stack two spots on one slot (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    result = match_by_order(["T2"], ring, show.set("1")).replace(slots={"T2": ("ring", 0)})
    with pytest.raises(MatchError):
        apply_match(show, TID, result)


def test_regression_apply_match_rejects_leaving_an_interior_slot_empty() -> None:
    """Refuses a match that opens a hole inside a form's members (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    result = match(show.set("1"), [ring], entities=["T1"])
    moved = result.replace(assignment={"T1": 3}, slots={"T1": ("ring", 3)})
    with pytest.raises(MatchError):
        apply_match(show, TID, moved)


def test_regression_apply_match_respects_locked_forms() -> None:
    """Needs force=True to change members of a locked form (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    show = show.replace(forms={"ring": ring.replace(locked=True)})
    result = match_by_order(REVERSED_RING, ring, show.set("1"))
    with pytest.raises(LockedError):
        apply_match(show, TID, result)
    assert apply_match(show, TID, result, force=True).forms[FormId("ring")].members == tuple(
        REVERSED_RING
    )


def test_regression_apply_match_rejects_a_form_of_another_set() -> None:
    """Refuses slots of a form outside the destination set, writing nothing (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    show = show.replace(forms={"ring": ring.replace(set="1")})
    result = match_by_order(REVERSED_RING, show.forms[FormId("ring")], show.set("1"))
    with pytest.raises(MatchError):
        apply_match(show, TID, result)


def test_regression_apply_match_rejects_an_unknown_form() -> None:
    """Names the missing form instead of writing a dangling reference (audit 9.6; P0 #12)."""
    show, ring = _ring_show()
    result = match_by_order(REVERSED_RING, ring, show.set("1"))
    result = result.replace(slots={e: ("ghost", i) for e, (_, i) in result.slots.items()})
    with pytest.raises(UnknownRefError):
        apply_match(show, TID, result)


@pytest.mark.parametrize("slot", [len(REVERSED_RING), -1])
def test_regression_apply_match_rejects_an_out_of_range_slot(slot: int) -> None:
    """Names the form, slot and slot count instead of leaking an IndexError (lane G review)."""
    show, ring = _ring_show()
    result = match_by_order(["T1"], ring, show.set("1")).replace(slots={"T1": ("ring", slot)})
    with pytest.raises(MatchError) as caught:
        apply_match(show, TID, result)
    assert caught.value.context == {"form": "ring", "slot": slot, "slots": len(REVERSED_RING)}


def test_regression_apply_match_ignores_a_form_the_set_no_longer_places() -> None:
    """Matches onto the set's current form while a stale form still names it (lane G review)."""
    show = replaced_form_show()
    matched = apply_match(
        show, TID, match_by_order(["T3", "T2"], show.forms[FormId("b")], show.set("1"))
    )
    assert matched.forms[FormId("a")] == show.forms[FormId("a")]
    assert matched.forms[FormId("b")].members == ("T3", "T2")
    assert matched.set("2").get("T3").point == (0.0, 12.0)


def test_regression_apply_match_rejects_slots_of_a_form_the_set_no_longer_places() -> None:
    """Refuses slots of a form the set no longer lists, writing nothing (lane G review)."""
    show = replaced_form_show()
    result = match_by_order(["T1"], show.forms[FormId("a")], show.set("1"))
    with pytest.raises(MatchError) as caught:
        apply_match(show, TID, result)
    assert caught.value.context == {"form": "a", "set": "2", "set_forms": ["b"]}


def test_regression_swap_ignores_a_form_the_set_no_longer_places() -> None:
    """Swaps only forms the set lists, even when a stale form is locked (lane G review)."""
    show = replaced_form_show()
    stale = show.forms[FormId("a")].replace(locked=True)
    show = show.replace(forms={**show.forms, stale.id: stale})
    swapped = swap(show, "2", "T2", "T3")
    assert swapped.forms[FormId("a")] == stale
    assert swapped.forms[FormId("b")].members == ("T3", "T2")


def test_regression_apply_match_reports_a_pinned_spot_before_a_bad_slot() -> None:
    """Raises LockedError for a pinned mover even when the result also names a missing slot."""
    show, ring = _ring_show()
    pinned = pin_spot(show, "2", "T2")
    result = match_by_order(["T2"], ring, show.set("1")).replace(
        slots={"T2": ("ring", len(REVERSED_RING))}
    )
    with pytest.raises(LockedError):
        apply_match(pinned, TID, result)


def test_regression_apply_match_names_a_pinned_row_on_a_matched_slot() -> None:
    """Names the pinned spot, absent from the form's members, that already stands on the slot."""
    show, ring = _ring_show()
    short = ring.replace(members=("T1", "T2", None, None))
    stored = pin_spot(show.replace(forms={"ring": short}), "2", "T4")
    result = match_by_order(["T3"], ring, show.set("1")).replace(slots={"T3": ("ring", 3)})
    with pytest.raises(MatchError, match="T4") as caught:
        apply_match(stored, TID, result)
    assert caught.value.context["spot"] == "T4"


def test_regression_apply_match_names_the_spot_holding_a_slot() -> None:
    """Names the member already on the slot and the spot matched onto it."""
    show, ring = _ring_show()
    result = match_by_order(["T1"], ring, show.set("1")).replace(slots={"T1": ("ring", 1)})
    with pytest.raises(MatchError, match=r"puts T1 .* where T2 already"):
        apply_match(show, TID, result)


def test_apply_match_leaves_unchanged_slots_alone() -> None:
    show, ring = _ring_show()
    pinned = pin_spot(show, "2", "T1")
    result = match_by_order(["T1", "T2"], ring, show.set("1"))
    assert apply_match(pinned, TID, result).forms[FormId("ring")].members == ring.members


def test_apply_match_trims_trailing_empty_slots() -> None:
    show, smaller = _ring_without("T4")
    row = line_form("row", [], (0, 14), (2, 14), n=2, set_id="2")
    show = show.replace(forms={"ring": smaller, "row": row})
    result = match_by_order(["T3"], row, show.set("1"))
    matched = apply_match(show, TID, result)
    assert matched.forms[FormId("ring")].members == ("T1", "T2")
    assert matched.forms[FormId("row")].members == ("T3",)
    assert matched.set("2").get("T3").form == "row"


@pytest.mark.defect("UP-9.6-24")
@pytest.mark.xfail(strict=True, reason="UP-9.6-24")
def test_regression_clear_rows_keeps_transition_order() -> None:
    """Leaves show.transitions in their original order when rows are cleared (audit 9.6)."""
    show = with_curve_rows(two_set_show(), ROW_SPOTS)
    later = TransitionSpec(id=make_tid("2", "3"), counts=NEXT_TRANSITION_COUNTS)
    show = show.model_copy(update={"transitions": (show.transitions[0], later)})
    cleared = clear_rows(show, TID, ROW_SPOTS)
    assert [t.id for t in cleared.transitions] == [t.id for t in show.transitions]


def test_regression_apply_match_rejects_a_text_form_left_short() -> None:
    """Refuses a match that moves a text form's last member into another form's slot."""
    show = two_set_show()
    dash = text_form("dash", ["T1", "T2", "T3", "T4"], "-", fonts=["simplex"], height=2.0,
                     at=(0, 9), n=4)  # fmt: skip
    row = line_form("row", [], (0, 14), (4, 14), n=1)
    placed, filled = place(show, "2", [dash, row])
    stored = with_set(show, placed).replace(forms={form.id: form for form in filled})
    moved = MatchResult(
        entities=(SpotId("T4"),),
        assignment={SpotId("T4"): 0},
        targets=((0.0, 14.0),),
        slots={SpotId("T4"): (FormId("row"), 0)},
    )
    with pytest.raises(SpacingError) as caught:
        apply_match(stored, TID, moved)
    assert caught.value.code == "MEMBER_COUNT_MISMATCH"
    assert caught.value.context == {"form": "dash", "n": 4, "members": 3}
