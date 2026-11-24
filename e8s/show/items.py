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
hold keeps its stored ID. Notes and
proposals are stored beside the show, not in it, so they hold no items. `items` refuses a show the
stored layout cannot hold; both functions refuse two stored values that claim one item, with
SchemaError.

Stated limit: facing overrides carry no ID, so all of them are one item, and edits to two
different overrides conflict. Finer keys need an override ID, which is a format change.

Contains:
    ITEM_TABLE: maps each stored (model, field) to the item kind and role that holds its value
    items(): splits a show into its items, in key order
    changes(): lists the items two shows differ in, skipping objects the shows share
"""

import operator
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from e8s.cast.models import Group, Spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import Set
from e8s.choreo.path_models import FacingOverride, TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.ids import parse_tid
from e8s.core.json_types import FrozenJsonValue, JsonValue
from e8s.core.readonly import ReadOnlyMap, thaw_json
from e8s.sheet.models import Sheet
from e8s.show.change_models import ChangeSet, ItemChange, ItemKey, ItemKind
from e8s.show.document_paths import transition_folder
from e8s.show.documents import require_storable
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key
from e8s.time.models import Timeline

type _Path = tuple[str, ...]
type _RawKey = tuple[ItemKind, _Path, str | None]
type _Entry = tuple[_RawKey, JsonValue]
type _Diff = tuple[_RawKey, JsonValue, JsonValue]
type _Row = tuple[ItemKind, str | None]

DRILL = "drill"
FACING = "facing"
LABELS = "labels"
PIN = "pin"
TIMING = "timing"
BUILD = "build"
FORMS = "forms"
PLACEMENTS = "placements"
ENTITY_FIELD = "entity"
ID_FIELD = "id"
KEY_FIELD = "key"
SET_FIELD = "set"
TARGET_FIELD = "target"
LABEL_SCHEME_FIELD = "label_scheme"


def _rows(model: str, kind: ItemKind, role: str | None, fields: str) -> dict[tuple[str, str], _Row]:
    return {(model, field): (kind, role) for field in fields.split()}


ITEM_TABLE: Mapping[tuple[str, str], tuple[ItemKind, str | None]] = ReadOnlyMap(
    {
        **_rows("Show", "show", None, "id title pack dependencies extensions cast"),
        **_rows("Show", "set_order", None, "sets"),
        **_rows("Show", "pin_order", None, "pins"),
        **_rows("Show", "facing_override", None, "facing_overrides"),
        **_rows("Cast", "spot_order", None, "spots"),
        **_rows("Cast", "group", None, "groups"),
        **_rows("Cast", "selection", None, "selections"),
        **_rows("Cast", "show", None, LABEL_SCHEME_FIELD),
        **_rows("Set", "set", None, "id key"),
        **_rows("Set", "set", PLACEMENTS, "placements"),
        **_rows("Set", "set", TIMING, "counts"),
        **_rows("Set", "set", LABELS, "label cue"),
        **_rows("Set", "set", BUILD, "source_hash state"),
        **_rows("Set", "set", FORMS, "forms"),
        **_rows("Placement", "placement", None, ENTITY_FIELD),
        **_rows("Placement", "placement", DRILL, "x y on_field form order"),
        **_rows("Placement", "placement", FACING, "facing"),
        **_rows("Placement", "placement", LABELS, "note"),
        **_rows("Placement", "placement", PIN, "pinned"),
        **_rows("TransitionSpec", "transition", None, "id counts notes source_hash"),
        **_rows("TransitionSpec", "path_row", None, "rows"),
        **_rows("TransitionSpec", "route", None, "routes"),
        **_rows("Form", "form", None, "id set recipe members spacing facing locked meta"),
        **_rows(
            "PathRow",
            "path_row",
            None,
            "entity style start end pace easing motion_fn motion_params waypoints route pivot"
            " sweep_deg facing_mode facing_value turn_counts turn_direction locked note",
        ),
        **_rows("Route", "route", None, "id curve members offsets travel start end"),
        **_rows("Spot", "spot", None, "id group shape color symbol tags active"),
        **_rows(
            "Group",
            "group",
            None,
            "key name label_prefix color symbol default_radius moves limits",
        ),
        **_rows("Pin", "pin", None, "target id by reason"),
        **_rows("FacingOverride", "facing_override", None, "entities mode value start end"),
        **_rows("FacingOverride", "facing_override", None, "turn_counts"),
        **_rows(
            "Sheet",
            "sheet",
            None,
            "name owner ref based_on surface_type size_m outline walkable grid guides thumbnail",
        ),
        **_rows(
            "Timeline",
            "timeline",
            None,
            "mode tempos holds markers cues measures audio total_counts",
        ),
    }
)


def _roles(model: str, kind: ItemKind) -> tuple[tuple[str, tuple[str, ...]], ...]:
    roles: dict[str, list[str]] = {}
    for (owner, field), (item_kind, role) in ITEM_TABLE.items():
        if owner == model and item_kind == kind and role is not None:
            roles.setdefault(role, []).append(field)
    return tuple((role, tuple(fields)) for role, fields in roles.items())


PLACEMENT_ROLES = _roles("Placement", "placement")
SET_ROLES = tuple(role for role in _roles("Set", "set") if role[0] != PLACEMENTS)
TRANSITION_FIELDS = tuple(
    field
    for (owner, field), (kind, _) in ITEM_TABLE.items()
    if owner == "TransitionSpec" and kind == "transition" and field != ID_FIELD
)


def _without(dumped: dict[str, JsonValue], *names: str) -> dict[str, JsonValue]:
    return {name: value for name, value in dumped.items() if name not in names}


def _picked(dumped: Mapping[str, JsonValue], names: Iterable[str]) -> dict[str, JsonValue]:
    return {name: dumped[name] for name in names}


DUPLICATE_HINTS: Mapping[ItemKind, str] = ReadOnlyMap(
    {
        "set": "give every set its own key; add_set() picks a free one",
        "transition": "store one path spec per set pair",
        "spot": "give every spot its own ID",
        "pin": "keep one pin per target and ID; pin() replaces the old one",
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


def _set_values(current: Set) -> dict[str, JsonValue]:
    """Reads a set's own stored fields as their JSON values, leaving its placements unread."""
    return {
        ID_FIELD: current.id,
        "counts": current.counts,
        "label": current.label,
        "cue": current.cue,
        "source_hash": current.source_hash,
        "state": current.state.value,
        "forms": list(current.forms),
    }


def _transition_values(spec: TransitionSpec) -> dict[str, JsonValue]:
    """Reads a path spec's own stored fields as their JSON values, leaving its rows unread."""
    return {"counts": spec.counts, "notes": spec.notes, "source_hash": spec.source_hash}


def _set_entries(path: _Path, current: Set) -> Iterator[_Entry]:
    (key,) = path
    for placement in current.placements:
        dumped = placement.to_json()
        for role, fields in PLACEMENT_ROLES:
            yield ("placement", (key, placement.entity), role), _picked(dumped, fields)
    values = _set_values(current)
    yield ("set", path, None), {ID_FIELD: values[ID_FIELD]}
    yield ("set", path, PLACEMENTS), [placement.entity for placement in current.placements]
    for role, fields in SET_ROLES:
        yield ("set", path, role), _picked(values, fields)


def _transition_entries(path: _Path, spec: TransitionSpec) -> Iterator[_Entry]:
    (key,) = path
    yield ("transition", path, None), _picked(_transition_values(spec), TRANSITION_FIELDS)
    for entity, row in spec.rows.items():
        yield ("path_row", (key, entity), None), _without(row.to_json(), ENTITY_FIELD)
    for route_id, route in spec.routes.items():
        yield ("route", (key, route_id), None), _without(route.to_json(), ID_FIELD)


def _form_entries(path: _Path, source: tuple[Form, str]) -> Iterator[_Entry]:
    form, key = source
    yield ("form", path, None), {**_without(form.to_json(), ID_FIELD), SET_FIELD: key}


def _forms(show: Show) -> dict[_Path, tuple[Form, str]]:
    keys = _keys(show)
    return {(i,): (form, keys.get(form.set, form.set)) for i, form in show.forms.items()}


def _same_form(was: tuple[Form, str], now: tuple[Form, str]) -> bool:
    return was[0] is now[0] and was[1] == now[1]


def _spot_entries(path: _Path, spot: Spot) -> Iterator[_Entry]:
    yield ("spot", path, None), _without(spot.to_json(), ID_FIELD)


def _group_entries(path: _Path, group: Group) -> Iterator[_Entry]:
    yield ("group", path, None), _without(group.to_json(), KEY_FIELD)


def _selection_entries(path: _Path, spot_ids: tuple[str, ...]) -> Iterator[_Entry]:
    yield ("selection", path, None), list(spot_ids)


def _pin_entries(path: _Path, record: Pin) -> Iterator[_Entry]:
    yield ("pin", path, None), _without(record.to_json(), TARGET_FIELD, ID_FIELD)


def _pin_order_entries(path: _Path, pin_paths: tuple[_Path, ...]) -> Iterator[_Entry]:
    yield ("pin_order", path, None), [list(pin_path) for pin_path in pin_paths]


def _spot_order_entries(path: _Path, spots: tuple[Spot, ...]) -> Iterator[_Entry]:
    yield ("spot_order", path, None), [spot.id for spot in spots]


def _override_entries(path: _Path, overrides: tuple[FacingOverride, ...]) -> Iterator[_Entry]:
    yield ("facing_override", path, None), [override.to_json() for override in overrides]


def _sheet_entries(path: _Path, sheet: Sheet) -> Iterator[_Entry]:
    yield ("sheet", path, None), sheet.to_json()


def _timeline_entries(path: _Path, timeline: Timeline) -> Iterator[_Entry]:
    yield ("timeline", path, None), timeline.to_json()


def _order_entries(path: _Path, sets: tuple[Set, ...]) -> Iterator[_Entry]:
    yield ("set_order", path, None), [set_key(current) for current in sets]


def _show_entries(path: _Path, show: Show) -> Iterator[_Entry]:
    meta: dict[str, JsonValue] = {
        ID_FIELD: show.id,
        "title": show.title,
        "pack": show.pack,
        "dependencies": dict(show.dependencies),
        # Show validation freezes every extension value.
        "extensions": thaw_json(cast("FrozenJsonValue", show.extensions)),
        LABEL_SCHEME_FIELD: show.cast.label_scheme.to_json(),
    }
    yield ("show", path, None), meta


def _keys(show: Show) -> dict[str, str]:
    return {current.id: set_key(current) for current in show.sets}


def _transition_key(keys: Mapping[str, str], tid: tuple[str, str]) -> str:
    """Returns a transition's item key: the stored folder of its set keys; an unknown ID stays."""
    return transition_folder((keys.get(tid[0], tid[0]), keys.get(tid[1], tid[1])))


def _pin_path(keys: Mapping[str, str], record: Pin) -> _Path:
    """Returns a pin's item path, naming a set or transition by set keys, as stored folders do."""
    if record.target == "set":
        return record.target, keys.get(record.id, record.id)
    if record.target == "transition":
        return record.target, _transition_key(keys, parse_tid(record.id))
    return record.target, record.id


def _transitions(show: Show) -> dict[_Path, TransitionSpec]:
    keys = _keys(show)
    found = (((_transition_key(keys, spec.id),), spec) for spec in show.transitions)
    return _unique("transition", found)


def _pins(show: Show) -> dict[_Path, Pin]:
    keys = _keys(show)
    return _unique("pin", ((_pin_path(keys, record), record) for record in show.pins))


def _pin_paths(show: Show) -> dict[_Path, tuple[_Path, ...]]:
    keys = _keys(show)
    return {(): tuple(_pin_path(keys, record) for record in show.pins)}


class _Area(Protocol):
    def entries(self, show: Show) -> Iterator[_Entry]: ...

    def differences(self, before: Show, after: Show) -> Iterator[_Diff]: ...


@dataclass(frozen=True)
class _Sourced[T]:
    """Expands the source objects of one part of a show into items.

    Attributes:
        sources: Maps a show to its source objects of this part, by item path.
        expand: Lists the items of one source object.
        is_same: Reports whether two sources give the same items unread; identity by default.
    """

    sources: Callable[[Show], Mapping[_Path, T]]
    expand: Callable[[_Path, T], Iterator[_Entry]]
    is_same: Callable[[T, T], bool] = operator.is_

    def entries(self, show: Show) -> Iterator[_Entry]:
        """Lists the items of this part of a show."""
        for path, source in self.sources(show).items():
            yield from self.expand(path, source)

    def differences(self, before: Show, after: Show) -> Iterator[_Diff]:
        """Lists the items of this part that differ, skipping source objects both shows share."""
        old, new = self.sources(before), self.sources(after)
        for path in old.keys() | new.keys():
            was, now = old.get(path), new.get(path)
            if was is not None and now is not None and self.is_same(was, now):
                continue
            was_items = {} if was is None else dict(self.expand(path, was))
            now_items = {} if now is None else dict(self.expand(path, now))
            for key in was_items.keys() | now_items.keys():
                value_was, value_now = was_items.get(key), now_items.get(key)
                if value_was != value_now:
                    yield key, value_was, value_now


AREAS: tuple[_Area, ...] = (
    _Sourced[Show](lambda show: {(): show}, _show_entries),
    _Sourced[tuple[Set, ...]](lambda show: {(): show.sets}, _order_entries),
    _Sourced[Set](
        lambda show: _unique("set", (((set_key(s),), s) for s in show.sets)), _set_entries
    ),
    _Sourced[tuple[Form, str]](_forms, _form_entries, _same_form),
    _Sourced[TransitionSpec](_transitions, _transition_entries),
    _Sourced[Spot](
        lambda show: _unique("spot", (((s.id,), s) for s in show.cast.spots)), _spot_entries
    ),
    _Sourced[tuple[Spot, ...]](lambda show: {(): show.cast.spots}, _spot_order_entries),
    _Sourced[Group](
        lambda show: {(k,): group for k, group in show.cast.groups.items()}, _group_entries
    ),
    _Sourced[tuple[str, ...]](
        lambda show: {(k,): ids for k, ids in show.cast.selections.items()}, _selection_entries
    ),
    _Sourced[Pin](_pins, _pin_entries),
    _Sourced[tuple[_Path, ...]](_pin_paths, _pin_order_entries),
    _Sourced[tuple[FacingOverride, ...]](
        lambda show: {(): show.facing_overrides}, _override_entries
    ),
    _Sourced[Sheet](lambda show: {(): show.sheet}, _sheet_entries),
    _Sourced[Timeline](lambda show: {(): show.timeline}, _timeline_entries),
)


def _item_key(raw: _RawKey) -> ItemKey:
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
