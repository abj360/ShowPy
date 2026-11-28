"""
validate_path_refs.py --- checks that the spots path rows and routes name or face are in the cast

Every issue carries its row's or route's item key in `context["item"]`; the transition folder in
the key uses set keys, as stored folders do.

Contains:
    path_spot_issues(): reports path rows and routes that name or face spots outside the cast
"""

from collections.abc import Iterable, Mapping, Set

from e8s.cast.rewrite import faces_entity
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.core.error_codes import ErrorCode
from e8s.core.ids import format_tid
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.item_table import set_keys_by_id, transition_key
from e8s.show.models import Show
from e8s.show.validate_issue import ERROR, make_issue


def _facing_issue(
    row: PathRow, where: str, item: ItemKey, cast_ids: Set[str]
) -> ValidationIssue | None:
    target = faces_entity(row.facing_mode, row.facing_value)
    if target is None or target in cast_ids:
        return None
    return make_issue(
        ERROR,
        ErrorCode.UNKNOWN_SPOT,
        f"{where} {row.entity} facing",
        f"{row.entity} faces toward {target}, which is not in the cast",
        "face another spot, or change the row's facing mode",
        {"item": item.to_json(), "target": target},
    )


def _spec_issues(
    spec: TransitionSpec, cast_ids: Set[str], keys: Mapping[str, str]
) -> list[ValidationIssue]:
    where = format_tid(spec.id)
    folder = transition_key(keys, spec.id)
    issues = [
        make_issue(
            ERROR,
            ErrorCode.UNKNOWN_SPOT,
            f"{where} {entity}",
            f"{entity} is not in the cast",
            "remove the row",
            {"item": ItemKey(kind="path_row", path=(folder, entity)).to_json()},
        )
        for entity in spec.rows
        if entity not in cast_ids
    ]
    for entity, row in spec.rows.items():
        facing = _facing_issue(
            row, where, ItemKey(kind="path_row", path=(folder, entity)), cast_ids
        )
        if facing is not None:
            issues.append(facing)
    for route in spec.routes.values():
        unknown = [m for m in route.members if m not in cast_ids]
        if unknown:
            item = ItemKey(kind="route", path=(folder, route.id))
            issues.append(
                make_issue(
                    ERROR,
                    ErrorCode.UNKNOWN_SPOT,
                    f"{where} route {route.id}",
                    f"unknown members {unknown}",
                    "rebuild the route",
                    {"item": item.to_json(), "spots": list(unknown)},
                )
            )
    return issues


def path_spot_issues(show: Show, specs: Iterable[TransitionSpec]) -> list[ValidationIssue]:
    """Reports path rows and routes that name or face spots outside the cast.

    The cast and the set keys are read once for all the transitions given.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> path_spot_issues(show, [show.transition(("1", "2"))])
    []

    Args:
        show: The show whose cast the spots must be in.
        specs: Transitions' paths, stored or candidate.

    Returns:
        issues: Per transition, one issue per unknown row, then one per row facing an unknown
            spot, then one per route with unknown members.
    """
    cast_ids = frozenset(show.cast.ids())
    keys = set_keys_by_id(show)
    return [issue for spec in specs for issue in _spec_issues(spec, cast_ids, keys)]
