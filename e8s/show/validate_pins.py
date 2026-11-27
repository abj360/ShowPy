"""
validate_pins.py --- checks that pin records name items the show holds and match their flags

A pin record implies flags on the placements, forms or path rows it covers; a flag with no record
is legal, since placements are pinned one by one without one. Every issue carries the pin's item
in `context["item"]`, naming a set or transition by set keys as the item table does.

Contains:
    MAX_NAMED_UNFLAGGED: caps how many unflagged items one pin issue names
    pin_item_path(): returns the item path a pin's issues carry
    pin_issues(): reports pins naming missing items, not parsing, or leaving flags unset
"""

from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, SchemaError, UnknownRefError
from e8s.core.ids import parse_tid
from e8s.core.validation import ValidationIssue
from e8s.show.change_models import ItemKey
from e8s.show.item_table import pin_path, set_keys_by_id
from e8s.show.models import Pin, Show
from e8s.show.pinning import covers_form, covers_placement
from e8s.show.validate_issue import ERROR, item_context, make_issue

MAX_NAMED_UNFLAGGED = 5


def _pin_item(path: tuple[str, ...]) -> ItemKey:
    return ItemKey(kind="pin", path=path)


class _PinScope(NamedTuple):
    """Indexes what the pin check reads, built once per show so each pin costs only its own items.

    Attributes:
        keys: Set ID to storage key.
        cast_ids: Every spot ID in the cast.
        set_ids: Every set ID in the show.
        placements: Each unpinned placement as (set ID, spot ID), under its set's ID and under its
            spot's ID, in show order.
        forms: Each unlocked form key under its own key and under its set's ID.
    """

    keys: Mapping[str, str]
    cast_ids: frozenset[str]
    set_ids: frozenset[str]
    placements: Mapping[str, list[tuple[str, str]]]
    forms: Mapping[str, list[str]]


def _placements_under(show: Show, name: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for owner in show.sets:
        if owner.id == name:
            found += [(owner.id, p.entity) for p in owner.placements if not p.pinned]
            continue
        try:
            placement = owner.get(name)
        except UnknownRefError:
            continue
        if not placement.pinned:
            found.append((owner.id, placement.entity))
    return found


def _named_scope(
    show: Show, names: Collection[str]
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]]]:
    placements = {name: _placements_under(show, name) for name in names}
    unlocked: list[tuple[str, str]] = [
        (key, form.set) for key, form in show.forms.items() if not form.locked
    ]
    forms = {name: [key for key, owner in unlocked if name in (key, owner)] for name in names}
    return placements, forms


def _whole_scope(show: Show) -> tuple[dict[str, list[tuple[str, str]]], dict[str, list[str]]]:
    placements: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for owner in show.sets:
        for placement in owner.placements:
            if placement.pinned:
                continue
            for name in dict.fromkeys((owner.id, placement.entity)):
                placements[name].append((owner.id, placement.entity))
    forms: dict[str, list[str]] = defaultdict(list)
    for key, form in show.forms.items():
        if form.locked:
            continue
        for name in dict.fromkeys((key, form.set)):
            forms[name].append(key)
    return placements, forms


def _pin_scope(show: Show, names: Collection[str] | None = None) -> _PinScope:
    placements, forms = _whole_scope(show) if names is None else _named_scope(show, names)
    return _PinScope(
        keys=set_keys_by_id(show),
        cast_ids=frozenset(show.cast.ids()),
        set_ids=frozenset(show.set_ids()),
        placements=placements,
        forms=forms,
    )


def _pin_exists(show: Show, record: Pin, scope: _PinScope) -> bool:
    if record.target == "entity":
        return record.id in scope.cast_ids
    if record.target == "form":
        return record.id in show.forms
    if record.target == "set":
        return record.id in scope.set_ids
    try:
        show.transition(parse_tid(record.id))
    except E8sError:
        return False
    return True


def _unresolved_pin(record: Pin, item: ItemKey, code: ErrorCode, hint: str) -> ValidationIssue:
    return make_issue(
        ERROR,
        code,
        f"pin {record.target} {record.id}",
        f"the {record.target} pin on {record.id} names something the show does not hold",
        hint,
        item_context(item, target=record.target, id=record.id),
    )


def _bad_transition_pin(record: Pin) -> ValidationIssue:
    item = _pin_item(pin_item_path({}, record))
    return make_issue(
        ERROR,
        ErrorCode.BAD_TRANSITION,
        f"pin transition {record.id}",
        f"the transition pin {record.id!r} does not parse as a transition ID",
        "write transitions as 12→13 or 12->13, or remove the pin",
        item_context(item, target=record.target, id=record.id),
    )


def _unflagged_placements(record: Pin, scope: _PinScope) -> list[str]:
    held = (record,)
    return [
        f"{set_id} {entity}"
        for set_id, entity in scope.placements.get(record.id, ())
        if covers_placement(held, set_id, entity)
    ]


def _unflagged_forms(show: Show, record: Pin, scope: _PinScope) -> list[str]:
    held = (record,)
    return [key for key in scope.forms.get(record.id, ()) if covers_form(show, held, key)]


def _unflagged_rows(show: Show, record: Pin) -> list[str]:
    # a spot added after the pin has no row yet, and adding it is a legal edit
    rows = show.transition(parse_tid(record.id)).rows
    return [entity for entity, row in rows.items() if not row.locked]


def _unflagged(show: Show, record: Pin, scope: _PinScope) -> list[str]:
    if record.target == "transition":
        return _unflagged_rows(show, record)
    found = [] if record.target == "form" else _unflagged_placements(record, scope)
    if record.target in ("form", "set"):
        found += _unflagged_forms(show, record, scope)
    return found


def _pin_mismatch(record: Pin, item: ItemKey, unflagged: list[str]) -> ValidationIssue:
    return make_issue(
        ERROR,
        ErrorCode.PIN_MISMATCH,
        f"pin {record.target} {record.id}",
        f"the {record.target} pin on {record.id} leaves {len(unflagged)} locked item(s) unflagged:"
        f" {', '.join(unflagged[:MAX_NAMED_UNFLAGGED])}",
        "re-pin it with pin(show, target, id), which sets every flag the pin covers",
        item_context(item, target=record.target, id=record.id, unflagged=len(unflagged)),
    )


def _parses(tid: str) -> bool:
    try:
        parse_tid(tid)
    except SchemaError:
        return False
    return True


def pin_item_path(keys: Mapping[str, str], record: Pin) -> tuple[str, ...]:
    """Returns the item path a pin's issues carry; an unparsable transition keeps its raw ID.

    >>> from e8s.show.models import Pin
    >>> pin_item_path({"1": "1A"}, Pin(target="set", id="1"))
    ('set', '1A')
    >>> pin_item_path({}, Pin(target="transition", id="1"))
    ('transition', '1')

    Args:
        keys: Set ID to storage key, as set_keys_by_id returns.
        record: The pin.

    Returns:
        path: The pin item's path.
    """
    if record.target == "transition" and not _parses(record.id):
        return record.target, record.id
    return pin_path(keys, record)


def pin_issues(
    show: Show, records: Collection[Pin], *, scoped: bool = False
) -> list[ValidationIssue]:
    """Reports pins that name missing items, do not parse, or leave covered flags unset.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.pinning import pin
    >>> show = pin(two_set_show(), "entity", "T1")
    >>> pin_issues(show, show.pins, scoped=True) == pin_issues(show, show.pins) == []
    True

    Args:
        show: The show that holds the pins.
        records: Pins of the show to check, in report order.
        scoped: Whether to index only the placements and forms the given pins can cover, which
            costs a pass over the sets per pin instead of one pass over every placement; the
            issues are the same either way.

    Returns:
        issues: Per pin, at most one issue.
    """
    scope = _pin_scope(show, {record.id for record in records} if scoped else None)
    return [issue for record in records for issue in _pin_issues(show, record, scope)]


def _pin_issues(show: Show, record: Pin, scope: _PinScope) -> Iterator[ValidationIssue]:
    if record.target == "transition" and not _parses(record.id):
        yield _bad_transition_pin(record)
        return
    item = _pin_item(pin_path(scope.keys, record))
    if not _pin_exists(show, record, scope):
        yield _unresolved_pin(record, item, ErrorCode.UNKNOWN_PIN, "unpin it or restore the item")
        return
    unflagged = _unflagged(show, record, scope)
    if unflagged:
        yield _pin_mismatch(record, item, unflagged)
