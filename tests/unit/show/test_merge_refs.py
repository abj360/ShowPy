#!/usr/bin/env python3

"""
test_merge_refs.py --- tests the dangling-reference check run on merged shows

Contains:
    test_regression_dangling_conflicts_name_the_item_the_merge_breaks: reports a form member that
        only the merge leaves outside the cast, keyed by the form
    test_regression_dangling_conflicts_leave_errors_the_base_or_head_held: reports nothing for an
        error the base or the head already held
    test_regression_dangling_conflicts_report_a_proposal_s_own_dangling_member: reports a dangling
        member the proposal itself brings
"""

from e8s.cast.spots import delete_spots
from e8s.choreo.forms import line_form
from e8s.core.ids import FormId
from e8s.examples import two_set_show
from e8s.show.change_models import ItemKey
from e8s.show.changes import apply_change
from e8s.show.collab_models import FormOp
from e8s.show.merge_refs import dangling_conflicts
from e8s.show.models import Show

DELETED_SPOT = "T4"
FORM_ID = "f1"


def _with_form(show: Show, members: list[str]) -> Show:
    form = line_form(FORM_ID, members, (0.0, 0.0), (4.0, 0.0), set_id="2")
    return apply_change(show, [FormOp(form=form)], force=True)


def test_regression_dangling_conflicts_name_the_item_the_merge_breaks() -> None:
    """Reports a form member only the merge leaves outside the cast, keyed by the form."""
    base = two_set_show()
    ours = delete_spots(base, DELETED_SPOT)
    theirs = _with_form(base, ["T3", DELETED_SPOT])
    listing = ours.sets[1].replace(forms=(FORM_ID,))
    merged = ours._copied(update={"forms": theirs.forms, "sets": (ours.sets[0], listing)})
    conflicts = dangling_conflicts(base, ours, merged)
    assert [c.key for c in conflicts] == [ItemKey(kind="form", path=(FORM_ID,))]
    assert any(DELETED_SPOT in c.reason for c in conflicts)


def _ghost_member(show: Show) -> Show:
    form = show.forms[FormId(FORM_ID)].replace(members=("T3", "ZZ"))
    return show._copied(update={"forms": {FORM_ID: form}})


def test_regression_dangling_conflicts_leave_errors_the_base_or_head_held() -> None:
    """Reports nothing for a dangling member the base or the head already held."""
    broken = _ghost_member(_with_form(two_set_show(), ["T3", "T4"]))
    assert dangling_conflicts(broken, two_set_show(), broken) == []
    assert dangling_conflicts(two_set_show(), broken, broken) == []


def test_regression_dangling_conflicts_report_a_proposal_s_own_dangling_member() -> None:
    """Reports a dangling member the proposal brings, though no head edit caused it."""
    base = two_set_show()
    broken = _ghost_member(_with_form(base, ["T3", "T4"]))
    (conflict,) = dangling_conflicts(base, base, broken)
    assert conflict.key == ItemKey(kind="form", path=(FORM_ID,))
    assert "ZZ" in conflict.reason
