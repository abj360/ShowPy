"""
merge.py --- merges two edits of one show item by item

Each side's changes are read against the common base with `changes`, so the merge costs what the
two sides changed. An item changed on one side only takes that side's value; an item changed the
same way on both keeps it; an item changed differently on both is a conflict. Order items (the
set, spot and pin orders and each set's placement order) merge element by element instead, and
conflict only where the two edits of the order cannot combine.

A merged show the models refuse, such as a selection naming a spot the other side deleted, is a
conflict on the items of the refused part that the two sides changed; the merge never repairs it.

Contains:
    merge(): merges two edits of one base show, or lists the items that conflict
"""

from collections.abc import Mapping
from typing import cast

from e8s.core.json_types import FrozenJsonValue, JsonValue
from e8s.core.readonly import thaw_json
from e8s.core.types import JsonAny
from e8s.show.change_models import ChangeSet, ItemChange, ItemKey, MergeConflict, MergeResult
from e8s.show.item_assembly import assemble
from e8s.show.item_reading import Refusal
from e8s.show.item_table import PLACEMENTS
from e8s.show.items import changes
from e8s.show.models import Show
from e8s.show.order_merge import Element, OrderConflict, merge_order

type _Settled = tuple[dict[ItemKey, JsonValue], frozenset[ItemKey], tuple[MergeConflict, ...]]

ORDER_KINDS = frozenset({"set_order", "spot_order", "pin_order"})
SHOW_ITEM = ItemKey(kind="show", path=())


def _by_key(change_set: ChangeSet) -> dict[ItemKey, ItemChange]:
    return {change.key: change for change in change_set.changes}


def _thawed(value: JsonAny) -> JsonValue:
    # Item change and conflict validation freezes every value.
    return thaw_json(cast("FrozenJsonValue", value))


def _is_order(key: ItemKey) -> bool:
    return key.kind in ORDER_KINDS or (key.kind == "set" and key.role == PLACEMENTS)


def _element(part: JsonValue) -> Element:
    return tuple(map(str, part)) if isinstance(part, list) else str(part)


def _elements(value: JsonAny) -> list[Element]:
    thawed = _thawed(value)
    return [_element(part) for part in thawed] if isinstance(thawed, list) else []


def _json_order(order: tuple[Element, ...]) -> JsonValue:
    return [element if isinstance(element, str) else list(element) for element in order]


def _reason(mine: ItemChange, other: ItemChange) -> str:
    if mine.before is None:
        return "both sides added this item with different values"
    if mine.after is None or other.after is None:
        return "one side removed this item and the other changed it"
    return "both sides changed this item differently"


def _settle(key: ItemKey, mine: ItemChange, other: ItemChange) -> JsonValue | MergeConflict:
    """Settles an item both sides changed: their shared value, a merged order, or a conflict."""
    if mine.after == other.after:
        return _thawed(mine.after)
    reason = _reason(mine, other)
    if _is_order(key) and mine.after is not None and other.after is not None:
        order = merge_order(_elements(mine.before), _elements(mine.after), _elements(other.after))
        if not isinstance(order, OrderConflict):
            return _json_order(order)
        reason = order.reason
    return MergeConflict(
        key=key, base=mine.before, ours=mine.after, theirs=other.after, reason=reason
    )


def _settle_all(
    mine: Mapping[ItemKey, ItemChange], other: Mapping[ItemKey, ItemChange]
) -> _Settled:
    """Settles every changed item: merged values, the items combining both sides, conflicts.

    An item merged from two different edits, or taken from the one side that changed it,
    combines the sides; an item both sides changed the same way does not.
    """
    values: dict[ItemKey, JsonValue] = {}
    combined = set(mine.keys() ^ other.keys())
    conflicts: list[MergeConflict] = []
    for key in combined:
        values[key] = _thawed((mine[key] if key in mine else other[key]).after)
    for key in sorted(mine.keys() & other.keys(), key=ItemKey.sort_key):
        settled = _settle(key, mine[key], other[key])
        if isinstance(settled, MergeConflict):
            conflicts.append(settled)
            continue
        values[key] = settled
        if mine[key].after != other[key].after:
            combined.add(key)
    return values, frozenset(combined), tuple(conflicts)


def _refused(
    refusal: Refusal,
    mine: Mapping[ItemKey, ItemChange],
    other: Mapping[ItemKey, ItemChange],
    combined: frozenset[ItemKey],
) -> tuple[MergeConflict, ...]:
    """Reports a refused merge as conflicts on the refused part's items that combine the sides."""
    reason = f"the models refuse the merged show: {refusal.error}"
    involved = [key for key in refusal.keys if key in combined] or list(refusal.keys)
    if not involved:
        return (MergeConflict(key=SHOW_ITEM, reason=reason),)
    conflicts: list[MergeConflict] = []
    for key in involved:
        mine_change, other_change = mine.get(key), other.get(key)
        either = mine_change if mine_change is not None else other_change
        base = None if either is None else either.before
        ours = base if mine_change is None else mine_change.after
        theirs = base if other_change is None else other_change.after
        conflicts.append(MergeConflict(key=key, base=base, ours=ours, theirs=theirs, reason=reason))
    return tuple(conflicts)


def merge(base: Show, ours: Show, theirs: Show) -> MergeResult:
    """Merges two edits of one base show item by item, or lists the items that conflict.

    An item changed on one side only takes that side's value, an item changed the same way on
    both keeps it, and an item changed differently on both is a conflict naming it, with the
    reason. Order items merge element by element: removals from either side apply, insertions
    and moves land after the element before them on their side, and runs landing at one place
    are ordered by natural sort. So `merge(base, ours, theirs)` equals `merge(base, theirs,
    ours)` with `ours` and `theirs` swapped in each conflict. A merged show the models refuse is
    reported as conflicts on the items involved, never repaired. Untouched objects are shared
    with `base`, and no input is changed.

    Args:
        base: The show both sides were edited from.
        ours: One side's edit.
        theirs: The other side's edit.

    Returns:
        result: The merged show, keeping `base.revision`, or the conflicts in key order. When one
            side changed no item, the other side itself.

    >>> from e8s.choreo.models import PlacementPatch
    >>> from e8s.examples import two_set_show
    >>> base = two_set_show()
    >>> first, second = base.sets
    >>> def edited(patch: PlacementPatch):
    ...     return base.replace(sets=(first, second.with_placements({"T1": patch})))
    >>> merged = merge(base, edited(PlacementPatch(x=3.0)), edited(PlacementPatch(facing=90.0)))
    >>> merged.show.set("2").get("T1").x, merged.show.set("2").get("T1").facing
    (3.0, 90.0)
    >>> clash = merge(base, edited(PlacementPatch(x=3.0)), edited(PlacementPatch(x=4.0)))
    >>> [(c.key.path, c.key.role, c.reason) for c in clash.conflicts]
    [(('2', 'T1'), 'drill', 'both sides changed this item differently')]
    """
    mine, other = _by_key(changes(base, ours)), _by_key(changes(base, theirs))
    if not other:
        return MergeResult(show=ours)
    if not mine:
        return MergeResult(show=theirs)
    values, combined, conflicts = _settle_all(mine, other)
    if conflicts:
        return MergeResult(conflicts=conflicts)
    built = assemble(base, values)
    if isinstance(built, Refusal):
        return MergeResult(conflicts=_refused(built, mine, other, combined))
    return MergeResult(show=built)
