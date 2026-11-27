#!/usr/bin/env python3

"""
test_merge_refs.py --- tests the dangling-reference check run on merged shows

Contains:
    test_regression_dangling_conflicts_name_the_item_the_merge_breaks: reports a form member that
        only the merge leaves outside the cast, keyed by the form
    test_regression_dangling_conflicts_leave_a_side_s_own_errors_to_validation: reports nothing
        for an error one side already held
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
    conflicts = dangling_conflicts(ours, theirs, merged)
    assert [c.key for c in conflicts] == [ItemKey(kind="form", path=(FORM_ID,))]
    assert any(DELETED_SPOT in c.reason for c in conflicts)


def test_regression_dangling_conflicts_leave_a_side_s_own_errors_to_validation() -> None:
    """Reports nothing for a dangling member that one side already held before the merge."""
    base = two_set_show()
    theirs = _with_form(base, ["T3", "T4"])
    broken = theirs._copied(
        update={"forms": {FORM_ID: theirs.forms[FormId(FORM_ID)].replace(members=("T3", "ZZ"))}}
    )
    assert dangling_conflicts(base, broken, broken) == []
