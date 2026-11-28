#!/usr/bin/env python3

"""
test_validate_refs.py --- tests the facing override checks of validate_refs

Contains:
    test_regression_facing_override_starting_at_the_show_end_is_past_the_show: reports an override
        that starts on the last count, so covers no show time
    test_regression_facing_override_with_a_negative_start_is_reported: reports an override that
        starts before count 0
    test_regression_facing_override_toward_an_unknown_spot_is_reported: reports an override that
        faces toward a spot outside the cast, keyed by the overrides item
"""

from e8s.choreo.path_models import EntityFacing, FacingMode, FacingOverride, FixedFacing
from e8s.core.ids import SpotId
from e8s.examples import two_set_show
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.set_queries import index
from e8s.show.validate import validate_show
from e8s.show.validate_refs import reference_issues

OVERRIDES_ITEM = ItemKey(kind="facing_override", path=())
NEGATIVE_START = -2.0
UNKNOWN_TARGET = "ZZ9"


def _with_override(start: float, end: float) -> Show:
    override = FacingOverride(
        entities=(SpotId("T1"),),
        mode=FacingMode.FIXED,
        value=FixedFacing(degrees=90.0),
        start=start,
        end=end,
    )
    return two_set_show().replace(facing_overrides=(override,))


def test_regression_facing_override_starting_at_the_show_end_is_past_the_show() -> None:
    """Reports an override that starts on the last count, so covers no show time."""
    total = index(two_set_show()).total_counts
    (issue,) = reference_issues(_with_override(total, total + 1))
    assert (issue.code, issue.level) == ("PAST_THE_SHOW", "error")
    assert ItemKey.model_validate(issue.context["item"]) == OVERRIDES_ITEM
    assert reference_issues(_with_override(total - 1, total)) == []


def test_regression_facing_override_with_a_negative_start_is_reported() -> None:
    """Reports an override that starts before count 0, naming the start."""
    (issue,) = reference_issues(_with_override(NEGATIVE_START, 1.0))
    assert (issue.code, issue.level) == ("TIME_RANGE", "error")
    assert "-2" in issue.message
    assert ItemKey.model_validate(issue.context["item"]) == OVERRIDES_ITEM
    assert issue.context["start"] == NEGATIVE_START


def test_regression_facing_override_toward_an_unknown_spot_is_reported() -> None:
    """Reports an override facing toward a spot outside the cast, naming it, as UNKNOWN_SPOT."""
    override = FacingOverride(
        entities=(SpotId("T1"),),
        mode=FacingMode.TOWARD_ENTITY,
        value=EntityFacing(entity=SpotId(UNKNOWN_TARGET)),
        start=0.0,
        end=1.0,
    )
    show = two_set_show().replace(facing_overrides=(override,))
    (issue,) = [i for i in validate_show(show).issues if i.code == "UNKNOWN_SPOT"]
    assert issue.level == "error"
    assert UNKNOWN_TARGET in issue.message
    assert ItemKey.model_validate(issue.context["item"]) == OVERRIDES_ITEM
    assert issue.context["target"] == UNKNOWN_TARGET
    toward_cast = override.replace(value=EntityFacing(entity=SpotId("T2")))
    known = two_set_show().replace(facing_overrides=(toward_cast,))
    assert [i for i in validate_show(known).issues if i.code == "UNKNOWN_SPOT"] == []
