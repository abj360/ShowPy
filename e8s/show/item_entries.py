"""
item_entries.py --- splits one stored object of a show into its items, each with its JSON value

An entry is a raw item key (kind, path, role) with the item's JSON value. Each function lists the
entries of one source object, so a reader can expand only the objects that changed. The key rules
are those `items` documents.

Contains:
    RawKey, Entry, Difference: name an item key, an item with its value and a changed item
    entry_differences(): lists the items two entry lists hold different values for
    placement_entries(): lists the role items of one placement of a set
    set_own_entries(): lists a set's own items, leaving its placements out
    set_entries(): lists every item of a set, its placements included
    row_entries(), route_entries(): list the item of one path row or route of a transition
    transition_own_entries(): lists a transition's own item, leaving its rows and routes out
    transition_entries(): lists every item of a transition
    form_entries(): lists a form's item, naming its set by key
    spot_entries(), group_entries(), selection_entries(), pin_entries(): list one cast or pin item
    pin_order_entries(), spot_order_entries(), order_entries(): list the order items
    override_entries(), sheet_entries(), timeline_entries(), show_entries(): list the show-wide
        items
"""

from collections.abc import Iterable, Iterator, Mapping
from typing import cast

from e8s.cast.models import Group, Spot
from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, Set
from e8s.choreo.path_models import FacingOverride, PathRow, Route, TransitionSpec
from e8s.core.json_types import FrozenJsonValue, JsonValue
from e8s.core.readonly import thaw_json
from e8s.sheet.models import Sheet
from e8s.show.change_models import ItemKind
from e8s.show.item_table import (
    ENTITY_FIELD,
    ID_FIELD,
    KEY_FIELD,
    LABEL_SCHEME_FIELD,
    PLACEMENT_ROLES,
    PLACEMENTS,
    SET_FIELD,
    SET_IDENTITY_FIELDS,
    SET_ROLES,
    SHOW_FIELDS,
    TARGET_FIELD,
    TRANSITION_FIELDS,
    ItemPath,
)
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key
from e8s.time.models import Timeline

type RawKey = tuple[ItemKind, ItemPath, str | None]
type Entry = tuple[RawKey, JsonValue]
type Difference = tuple[RawKey, JsonValue, JsonValue]


def entry_differences(was: Iterable[Entry], now: Iterable[Entry]) -> Iterator[Difference]:
    """Lists the items whose values differ between two entry lists; a missing item is None.

    >>> list(entry_differences([(("sheet", (), None), 1)], [(("sheet", (), None), 2)]))
    [(('sheet', (), None), 1, 2)]
    """
    was_items, now_items = dict(was), dict(now)
    for key in was_items.keys() | now_items.keys():
        value_was, value_now = was_items.get(key), now_items.get(key)
        if value_was != value_now:
            yield key, value_was, value_now


def _without(dumped: dict[str, JsonValue], *names: str) -> dict[str, JsonValue]:
    return {name: value for name, value in dumped.items() if name not in names}


def _picked(dumped: Mapping[str, JsonValue], names: Iterable[str]) -> dict[str, JsonValue]:
    return {name: dumped[name] for name in names}


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


def placement_entries(key: str, placement: Placement) -> Iterator[Entry]:
    """Lists the role items of one placement of the set stored under `key`."""
    dumped = placement.to_json()
    for role, fields in PLACEMENT_ROLES:
        yield ("placement", (key, placement.entity), role), _picked(dumped, fields)


def set_own_entries(path: ItemPath, current: Set) -> Iterator[Entry]:
    """Lists a set's identity, placement order and role items, leaving its placements out."""
    values = _set_values(current)
    yield ("set", path, None), _picked(values, SET_IDENTITY_FIELDS)
    yield ("set", path, PLACEMENTS), [placement.entity for placement in current.placements]
    for role, fields in SET_ROLES:
        yield ("set", path, role), _picked(values, fields)


def set_entries(path: ItemPath, current: Set) -> Iterator[Entry]:
    """Lists every item of a set, its placements first."""
    (key,) = path
    for placement in current.placements:
        yield from placement_entries(key, placement)
    yield from set_own_entries(path, current)


def row_entries(key: str, entity: str, row: PathRow) -> Iterator[Entry]:
    """Lists the item of the path row a transition stored under `key` keeps for `entity`."""
    yield ("path_row", (key, entity), None), _without(row.to_json(), ENTITY_FIELD)


def route_entries(key: str, route_id: str, route: Route) -> Iterator[Entry]:
    """Lists the item of the route a transition stored under `key` keeps as `route_id`."""
    yield ("route", (key, route_id), None), _without(route.to_json(), ID_FIELD)


def transition_own_entries(path: ItemPath, spec: TransitionSpec) -> Iterator[Entry]:
    """Lists a transition's own item: counts, notes and source hash."""
    yield ("transition", path, None), _picked(_transition_values(spec), TRANSITION_FIELDS)


def transition_entries(path: ItemPath, spec: TransitionSpec) -> Iterator[Entry]:
    """Lists every item of a transition: its own, then its rows and routes."""
    (key,) = path
    yield from transition_own_entries(path, spec)
    for entity, row in spec.rows.items():
        yield from row_entries(key, entity, row)
    for route_id, route in spec.routes.items():
        yield from route_entries(key, route_id, route)


def form_entries(path: ItemPath, source: tuple[Form, str]) -> Iterator[Entry]:
    """Lists a form's item from the form and the key of the set it names."""
    form, key = source
    yield ("form", path, None), {**_without(form.to_json(), ID_FIELD), SET_FIELD: key}


def spot_entries(path: ItemPath, spot: Spot) -> Iterator[Entry]:
    """Lists a spot's item."""
    yield ("spot", path, None), _without(spot.to_json(), ID_FIELD)


def group_entries(path: ItemPath, group: Group) -> Iterator[Entry]:
    """Lists a group's item."""
    yield ("group", path, None), _without(group.to_json(), KEY_FIELD)


def selection_entries(path: ItemPath, spot_ids: tuple[str, ...]) -> Iterator[Entry]:
    """Lists a named selection's item."""
    yield ("selection", path, None), list(spot_ids)


def pin_entries(path: ItemPath, record: Pin) -> Iterator[Entry]:
    """Lists a pin's item."""
    yield ("pin", path, None), _without(record.to_json(), TARGET_FIELD, ID_FIELD)


def pin_order_entries(path: ItemPath, pin_paths: tuple[ItemPath, ...]) -> Iterator[Entry]:
    """Lists the item of the pins' order."""
    yield ("pin_order", path, None), [list(pin_path) for pin_path in pin_paths]


def spot_order_entries(path: ItemPath, spots: tuple[Spot, ...]) -> Iterator[Entry]:
    """Lists the item of the cast's spot order."""
    yield ("spot_order", path, None), [spot.id for spot in spots]


def override_entries(path: ItemPath, overrides: tuple[FacingOverride, ...]) -> Iterator[Entry]:
    """Lists the one item that holds every facing override."""
    yield ("facing_override", path, None), [override.to_json() for override in overrides]


def sheet_entries(path: ItemPath, sheet: Sheet) -> Iterator[Entry]:
    """Lists the sheet's item."""
    yield ("sheet", path, None), sheet.to_json()


def timeline_entries(path: ItemPath, timeline: Timeline) -> Iterator[Entry]:
    """Lists the timeline's item."""
    yield ("timeline", path, None), timeline.to_json()


def order_entries(path: ItemPath, sets: tuple[Set, ...]) -> Iterator[Entry]:
    """Lists the item of the set order, naming sets by key."""
    yield ("set_order", path, None), [set_key(current) for current in sets]


def _show_values(show: Show) -> dict[str, JsonValue]:
    """Reads the show's metadata fields and the cast's label scheme as their JSON values."""
    return {
        ID_FIELD: show.id,
        "title": show.title,
        "pack": show.pack,
        "dependencies": dict(show.dependencies),
        # Show validation freezes every extension value.
        "extensions": thaw_json(cast("FrozenJsonValue", show.extensions)),
        LABEL_SCHEME_FIELD: show.cast.label_scheme.to_json(),
    }


def show_entries(path: ItemPath, show: Show) -> Iterator[Entry]:
    """Lists the show's metadata item."""
    yield ("show", path, None), _picked(_show_values(show), SHOW_FIELDS)
