#!/usr/bin/env python3

"""
test_validate_pins.py --- tests the pin record checks

Contains:
    test_scoped_pin_issues_equal_the_whole_show_pin_issues: reports the same issues when the pin
        check indexes only the items the pins can cover
"""

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
