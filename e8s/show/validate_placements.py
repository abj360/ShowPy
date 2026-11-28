"""
validate_placements.py --- checks each placement against its spot and the sheet

Every issue carries the placement it is about in `context["item"]`, with its set key.

Contains:
    FAR_OFF_SHEET_M: sets how far past the sheet's bounding box a placement draws a warning, in
        meters; real shows stay within a few hundred meters of the sheet, so a kilometer past it
        is a unit or typing mistake
    inactive_issues(): reports on-field placements of spots marked inactive in chosen sets
    far_off_issues(): warns about placements far off the sheet in chosen sets
    placement_issues(): reports on-field placements of spots marked inactive and placements far
        off the sheet
"""

from collections.abc import Iterable, Iterator

import numpy as np

from e8s.choreo.models import Set
from e8s.core.error_codes import ErrorCode
from e8s.core.json_types import JsonValue
from e8s.core.validation import ValidationIssue
from e8s.geo.measure import bounds
from e8s.show.change_models import ItemKey
from e8s.show.item_table import DRILL
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.validate_issue import ERROR, WARNING, make_issue

FAR_OFF_SHEET_M = 1_000.0


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


def inactive_issues(show: Show, owners: Iterable[Set]) -> list[ValidationIssue]:
    """Reports on-field placements of spots marked inactive in the given sets.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> inactive_issues(show, show.sets)
    []

    Args:
        show: The show whose cast says which spots are inactive.
        owners: The show's sets to check, in report order.

    Returns:
        issues: One error per offending placement, in set and placement order.
    """
    return list(_inactive_issues(show, owners))


def _inactive_issues(show: Show, owners: Iterable[Set]) -> Iterator[ValidationIssue]:
    inactive = {spot.id for spot in show.cast.spots if not spot.active}
    if not inactive:
        return
    for owner in owners:
        for placement in owner.placements:
            if placement.on_field and placement.entity in inactive:
                yield _inactive_on_field(owner, placement.entity)


def _far_off(owner: Set, spot: str, distance: float) -> ValidationIssue:
    return make_issue(
        WARNING,
        ErrorCode.FAR_OFF_SHEET,
        f"set {owner.id} {spot}",
        f"{spot} stands {distance:,.0f} m past the sheet's bounds in set {owner.id}",
        f"move it within {FAR_OFF_SHEET_M:,.0f} m of the sheet, or check the coordinate's units",
        _placement_item(owner, spot, distance_m=distance),
    )


def far_off_issues(show: Show, owners: Iterable[Set]) -> list[ValidationIssue]:
    """Warns about placements more than FAR_OFF_SHEET_M past the sheet in the given sets.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> far_off_issues(show, show.sets)
    []

    Args:
        show: The show whose sheet bounds the placements.
        owners: The show's sets to check, in report order.

    Returns:
        issues: One warning per offending placement, in set and placement order.
    """
    return list(_far_off_issues(show, owners))


def _far_off_issues(show: Show, owners: Iterable[Set]) -> Iterator[ValidationIssue]:
    box = bounds(np.asarray(show.sheet.outline, dtype=np.float64))
    low, high = np.asarray(box.min), np.asarray(box.max)
    for owner in owners:
        if not owner.placements:
            continue
        points = owner.positions()
        past = np.maximum(np.maximum(low - points, points - high), 0.0)
        distances = np.hypot(past[:, 0], past[:, 1])
        far = np.isfinite(distances) & (distances > FAR_OFF_SHEET_M)
        for position in np.flatnonzero(far):
            entity = owner.placements[position].entity
            yield _far_off(owner, entity, float(distances[position]))


def placement_issues(show: Show) -> list[ValidationIssue]:
    """Reports on-field placements of inactive spots and placements far off the sheet.

    An inactive spot is left out of forms and checks, so a placement that shows it on the field
    is an error. A placement more than FAR_OFF_SHEET_M meters past the sheet's bounding box is a
    warning; non-finite coordinates are left to the finite-number check.

    >>> from e8s.examples import two_set_show
    >>> placement_issues(two_set_show())
    []

    Args:
        show: The show to check.

    Returns:
        issues: One issue per offending placement, in show and placement order.
    """
    return inactive_issues(show, show.sets) + far_off_issues(show, show.sets)
