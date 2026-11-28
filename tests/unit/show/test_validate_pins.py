#!/usr/bin/env python3

"""
test_validate_pins.py --- tests the pin record checks

Contains:
    test_scoped_pin_issues_equal_the_whole_show_pin_issues: reports the same issues when the pin
        check indexes only the items the pins can cover
    test_scoped_pin_issues_match_where_ids_collide: reports the same issues, per pin and in
        aggregate, when a spot ID equals a set ID, a form key equals a set ID, and some
        placements are pinned and some forms locked
"""

from e8s.cast.spots import rename_spots
from e8s.choreo.models import PlacementPatch
from e8s.core.ids import FormId, SpotId
from e8s.show.models import Pin, Show
from e8s.show.validate_pins import pin_issues
from tests.fixtures.generators import seeded_show

UNFLAGGED_PINS = (
    Pin(target="entity", id="B1"),
    Pin(target="set", id="2"),
    Pin(target="form", id="brass_2"),
    Pin(target="transition", id="1→2"),
    Pin(target="entity", id="Z9"),
    Pin(target="transition", id="12"),
)

SET_NAMED_SPOT = "2"
SET_NAMED_FORM = FormId("3")
COPIED_FORM = FormId("brass_3")
LOCKED_FORM = FormId("woodwinds_2")
PINNED_SPOT = SpotId("W1")
COLLIDING_PINS = (
    Pin(target="entity", id=SET_NAMED_SPOT),
    Pin(target="set", id="2"),
    Pin(target="set", id=SET_NAMED_FORM),
    Pin(target="form", id=SET_NAMED_FORM),
    Pin(target="form", id=LOCKED_FORM),
    Pin(target="entity", id=PINNED_SPOT),
    Pin(target="set", id="4"),
)


def _pinned_without_flags() -> Show:
    return seeded_show(3, 8, 4)._copied(update={"pins": UNFLAGGED_PINS})


def test_scoped_pin_issues_equal_the_whole_show_pin_issues() -> None:
    """Reports the same issues when the check indexes only the items the pins can cover."""
    show = _pinned_without_flags()
    whole = pin_issues(show, show.pins)
    assert {str(issue.code) for issue in whole} == {
        "PIN_MISMATCH",
        "UNKNOWN_PIN",
        "BAD_TRANSITION",
    }
    assert pin_issues(show, show.pins, scoped=True) == whole
    for record in show.pins:
        alone = pin_issues(show, [record], scoped=True)
        assert alone == [i for i in whole if i.context["id"] == record.id]


def _pinned_placements(show: Show) -> Show:
    pinned = PlacementPatch(pinned=True)
    sets = tuple(
        owner.with_placements({PINNED_SPOT: pinned}, force=True) if index % 2 else owner
        for index, owner in enumerate(show.sets)
    )
    return show._copied(update={"sets": sets})


def _colliding() -> Show:
    show = rename_spots(seeded_show(3, 8, 4), {"B1": SET_NAMED_SPOT})[0]
    copied = show.forms[COPIED_FORM].replace(id=SET_NAMED_FORM)
    forms = {
        **show.forms,
        LOCKED_FORM: show.forms[LOCKED_FORM].replace(locked=True),
        SET_NAMED_FORM: copied,
    }
    return _pinned_placements(show)._copied(update={"forms": forms, "pins": COLLIDING_PINS})


def test_scoped_pin_issues_match_where_ids_collide() -> None:
    """Reports the same issues per pin and in aggregate where IDs collide across kinds."""
    show = _colliding()
    whole = pin_issues(show, show.pins)
    flagged = {(issue.context["target"], issue.context["id"]) for issue in whole}
    assert {("entity", SET_NAMED_SPOT), ("set", "2"), ("set", "3"), ("form", "3")} <= flagged
    assert ("form", LOCKED_FORM) not in flagged
    assert pin_issues(show, show.pins, scoped=True) == whole
    for record in show.pins:
        alone = pin_issues(show, [record], scoped=True)
        assert alone == pin_issues(show, [record])
        assert alone == [
            issue
            for issue in whole
            if (issue.context["target"], issue.context["id"]) == (record.target, record.id)
        ]
