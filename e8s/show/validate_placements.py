"""
validate_placements.py --- checks each placement against its spot

Every issue carries the placement it is about in `context["item"]`, with its set key.

Contains:
    placement_issues(): reports on-field placements of spots marked inactive
"""

from collections.abc import Iterator

from e8s.choreo.models import Set
from e8s.core.error_codes import ErrorCode
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.item_table import DRILL
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.validate_refs import ERROR, make_issue


def _placement_item(owner: Set, spot: str, **extra: JsonValue) -> dict[str, JsonValue]:
    item = ItemKey(kind="placement", path=(set_key(owner), spot), role=DRILL)
    return {"item": item.to_json(), **extra}


def _inactive_on_field(owner: Set, spot: str) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.INACTIVE_ON_FIELD,
        f"set {owner.id} {spot}",
        f"{spot} is marked inactive but stands on the field in set {owner.id}",
        "take the placement off the field, or mark the spot active",
        _placement_item(owner, spot),
    )


def _inactive_issues(show: Show) -> Iterator[ValidationIssue]:
    inactive = {spot.id for spot in show.cast.spots if not spot.active}
    if not inactive:
        return
    for owner in show.sets:
        for placement in owner.placements:
            if placement.on_field and placement.entity in inactive:
                yield _inactive_on_field(owner, placement.entity)


def placement_issues(show: Show) -> list[ValidationIssue]:
    """Reports on-field placements of spots marked inactive.

    An inactive spot is left out of forms and checks, so a placement that shows it on the field
    is an error.

    >>> from e8s.examples import two_set_show
    >>> placement_issues(two_set_show())
    []

    Args:
        show: The show to check.

    Returns:
        issues: One issue per offending placement, in show and placement order.
    """
    return list(_inactive_issues(show))
