#!/usr/bin/env python3

"""
test_validate_refs.py --- tests the path reference check of validate_refs

Contains:
    test_regression_path_spot_issues_carry_path_row_and_route_items: gives each path row or route
        naming a spot outside the cast the item key of that row or route, by set keys
    test_path_spot_issues_report_nothing_for_cast_spots: reports nothing when every row and route
        member is in the cast
    test_regression_facing_override_starting_at_the_show_end_is_past_the_show: reports an override
        that starts on the last count, so covers no show time
    test_regression_facing_override_with_a_negative_start_is_reported: reports an override that
        starts before count 0
"""

from e8s.choreo.path_models import (
    FacingMode,
    FacingOverride,
    FixedFacing,
    PathRow,
    Route,
    TransitionSpec,
)
from e8s.core.ids import RouteId, SpotId, make_tid
from e8s.examples import two_set_show
from e8s.geo.shapes import line
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.set_queries import index
from e8s.show.validate_refs import path_spot_issues, reference_issues

GHOST = "Z9"
ROUTE_ID = "r"
RENUMBERED_KEY = "2A"
OVERRIDES_ITEM = ItemKey(kind="facing_override", path=())
NEGATIVE_START = -2.0


def _spec(members: tuple[str, ...], rows: tuple[str, ...]) -> TransitionSpec:
    route = Route(
        id=RouteId(ROUTE_ID),
        curve=line((0.0, 5.0), (0.0, 15.0)),
        members=tuple(map(SpotId, members)),
        offsets=tuple(0.0 for _ in members),
        travel=4.0,
    )
    return TransitionSpec(
        id=make_tid("1", "2"),
        counts=8.0,
        rows={SpotId(e): PathRow(entity=SpotId(e)) for e in rows},
        routes={RouteId(ROUTE_ID): route},
    )


def _renumbered() -> Show:
    show = two_set_show()
    return show.replace(sets=(show.sets[0], show.sets[1].replace(key=RENUMBERED_KEY)))


def test_regression_path_spot_issues_carry_path_row_and_route_items() -> None:
    """Gives each row or route naming a spot outside the cast its own item key, by set keys."""
    issues = path_spot_issues(_renumbered(), _spec(("T1", GHOST), ("T1", GHOST)))
    folder = f"1-{RENUMBERED_KEY}"
    assert [(i.code, i.where) for i in issues] == [
        ("UNKNOWN_SPOT", f"1→2 {GHOST}"),
        ("UNKNOWN_SPOT", f"1→2 route {ROUTE_ID}"),
    ]
    assert [ItemKey.model_validate(i.context["item"]) for i in issues] == [
        ItemKey(kind="path_row", path=(folder, GHOST)),
        ItemKey(kind="route", path=(folder, ROUTE_ID)),
    ]
    assert GHOST in issues[0].message
    assert GHOST in issues[1].message


def test_path_spot_issues_report_nothing_for_cast_spots() -> None:
    """Reports nothing when every row and route member is in the cast."""
    assert path_spot_issues(two_set_show(), _spec(("T1", "T2"), ("T1",))) == []


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
