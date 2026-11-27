#!/usr/bin/env python3

"""
test_validate_placements.py --- tests the placement checks of validate_show

Contains:
    test_regression_inactive_spot_on_the_field_is_an_error_per_placement: reports each on-field
        placement of an inactive spot with its placement item, and skips off-field ones
"""

from e8s.choreo.models import PlacementPatch
from e8s.examples import two_set_show
from e8s.show.change_models import ItemKey
from e8s.show.models import Show
from e8s.show.validate_placements import placement_issues

INACTIVE_SPOT = "T1"


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
