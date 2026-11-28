#!/usr/bin/env python3

"""
test_validate_path_refs.py --- tests the path row and route spot check

Contains:
    test_regression_path_spot_issues_carry_path_row_and_route_items: gives each path row or route
        naming a spot outside the cast the item key of that row or route, by set keys
    test_path_spot_issues_report_nothing_for_cast_spots: reports nothing when every row and route
        member is in the cast
    test_regression_validate_show_reports_a_row_facing_a_spot_outside_the_cast: reports a row that
        faces toward a spot the cast does not hold, keyed by that row
"""

from e8s.choreo.path_models import EntityFacing, FacingMode, PathRow, Route, TransitionSpec
from e8s.core.ids import RouteId, SpotId, make_tid
from e8s.examples import two_set_show
from e8s.geo.shapes import line
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.validate import validate_show
from e8s.show.validate_path_refs import path_spot_issues

GHOST = "Z9"
ROUTE_ID = "r"
RENUMBERED_KEY = "2A"


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
    issues = path_spot_issues(_renumbered(), [_spec(("T1", GHOST), ("T1", GHOST))])
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
    assert path_spot_issues(two_set_show(), [_spec(("T1", "T2"), ("T1",))]) == []


def test_regression_validate_show_reports_a_row_facing_a_spot_outside_the_cast() -> None:
    """Reports a row that faces toward a spot the cast does not hold, keyed by that row."""
    row = PathRow(
        entity=SpotId("T1"),
        facing_mode=FacingMode.TOWARD_ENTITY,
        facing_value=EntityFacing(entity=SpotId(GHOST)),
    )
    spec = TransitionSpec(id=make_tid("1", "2"), counts=8.0, rows={SpotId("T1"): row})
    show = two_set_show()._copied(update={"transitions": (spec,)})
    issues = [i for i in validate_show(show).issues if i.code == "UNKNOWN_SPOT"]
    assert [ItemKey.model_validate(i.context["item"]) for i in issues] == [
        ItemKey(kind="path_row", path=("1-2", "T1"))
    ]
    assert GHOST in issues[0].message
    assert issues[0].context["target"] == GHOST
