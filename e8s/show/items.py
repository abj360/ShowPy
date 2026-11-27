"""
items.py --- splits a show into stored items by design role and lists the items two shows differ in

Every stored value of a show lands in exactly one item. An item key is a kind, a path of stable
identifiers (outermost first, never a list position) and, where one stored object splits into
several items, a role. Paths address sets by their storage key, never by display ID, so a
renumber changes only the sets' own identity items.

Key rules (kind, path: value):
    placement (set key, spot ID), one item per role: `drill` (x, y, on_field, form, order),
        `facing` (facing), `labels` (note) and `pin` (pinned)
    set (set key,): no role holds the display ID, which the path leaves out; roles `timing`
        (counts), `labels` (label, cue), `build` (source_hash, state), `forms` (forms) and
        `placements`, the spot IDs in stored placement order
    set_order (): the set keys in show order
    spot_order (): the spot IDs in cast order
    pin_order (): the pin item paths in show order
    form (form ID,): form IDs are unique across the show, so the path needs no set
    transition (transition key,): counts, notes and source_hash; the transition key is the
        stored folder name of the two set keys, such as `1-2`
    path_row (transition key, spot ID) and route (transition key, route ID)
    spot (spot ID,), group (group key,) and selection (selection name,): the spot IDs listed
    pin (pin target, pin ID): one ID can be pinned both as a spot and as a form; a set pin
        names its set key and a transition pin its transition key, so a renumber moves no pin
    facing_override (): every override in one item, in show order
    sheet (), timeline ()
    show (): ID, title, pack, dependencies, extensions and the cast's label scheme, which
        configures the whole show's labels and has no kind of its own

Values are the item's `to_json()` fields without the identity fields its path holds, with one
departure: a value that names a set by display ID names it by set key instead, so a renumber
changes no value either. Only a form's `set` names a set in a value; a set the show does not
hold keeps its stored ID.

Notes and proposals are stored beside the show, not in it, so they hold no items. `items`
refuses a show the stored layout cannot hold; both functions refuse two stored values that claim
one item, with SchemaError.

Stated limit: facing overrides carry no ID, so all of them are one item, and edits to two
different overrides conflict. Finer keys need an override ID, which is a format change.

The item table, its roles and the way paths name sets and transitions live in `item_table`; how
one object splits into items lives in `item_entries`.

Contains:
    items(): splits a show into its items, in key order
    changes(): lists the items two shows differ in, skipping objects the shows share
"""

import operator
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from e8s.cast.models import Group, Spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import FacingOverride, TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue
from e8s.core.readonly import ReadOnlyMap
from e8s.sheet.models import Sheet
from e8s.show.change_models import ChangeSet, ItemChange, ItemKey, ItemKind
from e8s.show.item_entries import (
    Difference,
    Entry,
    RawKey,
    form_entries,
    group_entries,
    is_same_form,
    order_entries,
    override_entries,
    pin_entries,
    pin_order_entries,
    selection_entries,
    set_entries,
    sheet_entries,
    show_entries,
    source_differences,
    spot_entries,
    spot_order_entries,
    timeline_entries,
    transition_entries,
)
from e8s.show.item_table import ItemPath, pin_path, set_keys_by_id, transition_key
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key
from e8s.show.storable import require_storable
from e8s.time.models import Timeline

type _Path = ItemPath


DUPLICATE_HINTS: Mapping[ItemKind, str] = ReadOnlyMap(
    {
        "set": "give every set its own key; add_set() picks a free one",
        "transition": "store its paths with store_paths(show, PathsOp(spec=...)), which keeps"
        " one spec per pair",
        "spot": "give every spot its own ID",
        "pin": "re-pin it with pin(show, target, id), which keeps one pin per target and ID",
    }
)


def _unique[T](kind: ItemKind, pairs: Iterable[tuple[_Path, T]]) -> dict[_Path, T]:
    """Maps each item path to its source object, refusing a path two stored values claim."""
    found: dict[_Path, T] = {}
    for path, source in pairs:
        if path in found:
            raise SchemaError(
                f"two stored values claim the {kind} item {'/'.join(path)}",
                hint=DUPLICATE_HINTS[kind],
                context={"kind": kind, "path": list(path)},
            )
        found[path] = source
    return found


def _forms(show: Show) -> dict[_Path, tuple[Form, str]]:
    keys = set_keys_by_id(show)
    return {(i,): (form, keys.get(form.set, form.set)) for i, form in show.forms.items()}


def _transitions(show: Show) -> dict[_Path, TransitionSpec]:
    keys = set_keys_by_id(show)
    found = (((transition_key(keys, spec.id),), spec) for spec in show.transitions)
    return _unique("transition", found)


def _pins(show: Show) -> dict[_Path, Pin]:
    keys = set_keys_by_id(show)
    return _unique("pin", ((pin_path(keys, record), record) for record in show.pins))


def _pin_paths(show: Show) -> dict[_Path, tuple[_Path, ...]]:
    keys = set_keys_by_id(show)
    return {(): tuple(pin_path(keys, record) for record in show.pins)}


class _Area(Protocol):
    def entries(self, show: Show) -> Iterator[Entry]: ...

    def differences(self, before: Show, after: Show) -> Iterator[Difference]: ...


@dataclass(frozen=True)
class _Sourced[T]:
    """Expands the source objects of one part of a show into items.

    Attributes:
        sources: Maps a show to its source objects of this part, by item path.
        expand: Lists the items of one source object.
        is_same: Reports whether two sources give the same items unread; identity by default.
    """

    sources: Callable[[Show], Mapping[_Path, T]]
    expand: Callable[[_Path, T], Iterator[Entry]]
    is_same: Callable[[T, T], bool] = operator.is_

    def entries(self, show: Show) -> Iterator[Entry]:
        """Lists the items of this part of a show."""
        for path, source in self.sources(show).items():
            yield from self.expand(path, source)

    def differences(self, before: Show, after: Show) -> Iterator[Difference]:
        """Lists the items of this part that differ, skipping source objects both shows share."""
        yield from source_differences(
            self.sources(before), self.sources(after), self.expand, self.is_same
        )


AREAS: tuple[_Area, ...] = (
    _Sourced[Show](lambda show: {(): show}, show_entries),
    _Sourced[tuple[Set, ...]](lambda show: {(): show.sets}, order_entries),
    _Sourced[Set](
        lambda show: _unique("set", (((set_key(s),), s) for s in show.sets)), set_entries
    ),
    _Sourced[tuple[Form, str]](_forms, form_entries, is_same_form),
    _Sourced[TransitionSpec](_transitions, transition_entries),
    _Sourced[Spot](
        lambda show: _unique("spot", (((s.id,), s) for s in show.cast.spots)), spot_entries
    ),
    _Sourced[tuple[Spot, ...]](lambda show: {(): show.cast.spots}, spot_order_entries),
    _Sourced[Group](
        lambda show: {(k,): group for k, group in show.cast.groups.items()}, group_entries
    ),
    _Sourced[tuple[str, ...]](
        lambda show: {(k,): ids for k, ids in show.cast.selections.items()}, selection_entries
    ),
    _Sourced[Pin](_pins, pin_entries),
    _Sourced[tuple[_Path, ...]](_pin_paths, pin_order_entries),
    _Sourced[tuple[FacingOverride, ...]](
        lambda show: {(): show.facing_overrides}, override_entries
    ),
    _Sourced[Sheet](lambda show: {(): show.sheet}, sheet_entries),
    _Sourced[Timeline](lambda show: {(): show.timeline}, timeline_entries),
)


def _item_key(raw: RawKey) -> ItemKey:
    kind, path, role = raw
    return ItemKey(kind=kind, path=path, role=role)


def items(show: Show) -> dict[ItemKey, JsonValue]:
    """Splits a show into its stored items, each with its JSON value, in key order.

    Args:
        show: The show to split; it must be storable.

    Returns:
        show_items: Each item key mapped to the item's JSON value.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.change_models import ItemKey
    >>> items(two_set_show())[ItemKey(kind="placement", path=("2", "T1"), role="facing")]
    {'facing': 0.0}
    """
    require_storable(show)
    entries = ((_item_key(raw), value) for area in AREAS for raw, value in area.entries(show))
    return dict(sorted(entries, key=lambda entry: entry[0].sort_key()))


def changes(before: Show, after: Show) -> ChangeSet:
    """Lists the items two shows differ in, comparing JSON values item by item.

    Sets, forms, transitions and other objects the two shows share by identity are skipped
    unread, and no whole-show check runs, so the cost follows what changed. Two stored values
    that claim one item still raise SchemaError. An added item has no `before`, a removed one
    no `after`.

    Args:
        before: The older show.
        after: The newer show.

    Returns:
        change_set: One change per differing item, in key order.

    >>> from e8s.choreo.models import PlacementPatch
    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> moved = show.sets[1].with_placements({"T1": PlacementPatch(x=3.0)})
    >>> [c.key.role for c in changes(show, show.replace(sets=(show.sets[0], moved))).changes]
    ['drill']
    """
    found = (
        ItemChange(key=_item_key(raw), before=was, after=now)
        for area in AREAS
        for raw, was, now in area.differences(before, after)
    )
    return ChangeSet(changes=tuple(sorted(found, key=lambda change: change.key.sort_key())))
