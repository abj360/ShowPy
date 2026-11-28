#!/usr/bin/env python3

"""
test_validate_placements.py --- tests the placement checks of validate_show

Contains:
    test_regression_inactive_spot_on_the_field_is_an_error_per_placement: reports each on-field
        placement of an inactive spot with its placement item, and skips off-field ones
    test_regression_far_off_sheet_warning_starts_past_the_named_distance: warns about a placement
        just past FAR_OFF_SHEET_M beyond the sheet's bounds, not one just inside it
    test_regression_far_off_sheet_skips_non_finite_coordinates: leaves infinite coordinates to the
        finite-number check
"""

import math

import numpy as np

from e8s.choreo.models import PlacementPatch
from e8s.core.validation import ValidationIssue
from e8s.examples import two_set_show
from e8s.geo.measure import bounds
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.validate_placements import FAR_OFF_SHEET_M, placement_issues

INACTIVE_SPOT = "T1"
MOVED_SPOT = "T2"
EDGE_MARGIN_M = 1.0


def _inactive(show: Show) -> Show:
    spots = tuple(s.replace(active=False) if s.id == INACTIVE_SPOT else s for s in show.cast.spots)
    return show.replace(cast=show.cast.replace(spots=spots))


def test_regression_inactive_spot_on_the_field_is_an_error_per_placement() -> None:
    """Reports each on-field placement of an inactive spot; an off-field one is not reported."""
    show = _inactive(two_set_show())
    issues = placement_issues(show)
    assert [i.code for i in issues] == ["INACTIVE_ON_FIELD", "INACTIVE_ON_FIELD"]
    assert {i.level for i in issues} == {"error"}
    items = [ItemKey.model_validate(i.context["item"]) for i in issues]
    assert items == [
        ItemKey(kind="placement", path=(key, INACTIVE_SPOT), role="drill") for key in ("1", "2")
    ]
    off = show.sets[0].with_placements({INACTIVE_SPOT: PlacementPatch(on_field=False)})
    left = placement_issues(show.replace(sets=(off, show.sets[1])))
    assert [ItemKey.model_validate(i.context["item"]).path[0] for i in left] == ["2"]


def _moved_past_edge(past_m: float) -> list[ValidationIssue]:
    show = two_set_show()
    right_edge = bounds(np.asarray(show.sheet.outline)).max[0]
    patch = {MOVED_SPOT: PlacementPatch(x=right_edge + past_m)}
    moved = show.sets[1].with_placements(patch, force=True)
    return placement_issues(show.replace(sets=(show.sets[0], moved)))


def test_regression_far_off_sheet_warning_starts_past_the_named_distance() -> None:
    """Warns about a placement just past FAR_OFF_SHEET_M beyond the sheet, not one inside it."""
    assert _moved_past_edge(FAR_OFF_SHEET_M - EDGE_MARGIN_M) == []
    (issue,) = _moved_past_edge(FAR_OFF_SHEET_M + EDGE_MARGIN_M)
    assert (issue.code, issue.level) == ("FAR_OFF_SHEET", "warning")
    assert MOVED_SPOT in issue.where
    assert ItemKey.model_validate(issue.context["item"]) == ItemKey(
        kind="placement", path=("2", MOVED_SPOT), role="drill"
    )
    assert issue.context["distance_m"] == FAR_OFF_SHEET_M + EDGE_MARGIN_M


def test_regression_far_off_sheet_skips_non_finite_coordinates() -> None:
    """Leaves an infinite coordinate to the finite-number check instead of a distance warning."""
    show = two_set_show()
    placements = tuple(
        p._copied(update={"x": math.inf}) if p.entity == MOVED_SPOT else p
        for p in show.sets[1].placements
    )
    infinite = show.sets[1]._copied(update={"placements": placements})
    assert placement_issues(show._copied(update={"sets": (show.sets[0], infinite)})) == []
