"""
item_table.py --- maps each stored field of a show to its item, and names objects in item paths

Every stored (model, field) pair has a row naming the item kind and the design role that hold
its value. The roles and role-less field lists below are read from the table, so a field moves
between items by editing its row alone. Item paths name sets by storage key, never by display
ID, so a renumber changes only the sets' own identity items.

Contains:
    ITEM_TABLE: maps each stored (model, field) to the item kind and role that holds its value;
        Show.cast has no row, since it holds only the Cast, whose fields have rows of their own
    PLACEMENT_ROLES: lists each placement role with the fields it holds
    SET_ROLES: lists each set role but `placements` with the fields it holds
    SHOW_FIELDS: lists the show and cast fields the show item holds
    SET_IDENTITY_FIELDS: lists the set fields its role-less identity item holds
    TRANSITION_FIELDS: lists the path spec fields its role-less transition item holds
    set_keys_by_id(): maps each set ID of a show to the set's storage key
    transition_key(): returns a transition's item key, the stored folder of its set keys
    pin_path(): returns a pin's item path, naming a set or transition by set keys
"""

from collections.abc import Mapping

from e8s.core.ids import parse_tid
from e8s.core.readonly import ReadOnlyMap
from e8s.show.change_models import ItemKind
from e8s.show.document_paths import transition_folder
from e8s.show.models import Pin, Show
from e8s.show.set_keys import set_key

type ItemPath = tuple[str, ...]
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
SET_TARGET = "set"
TRANSITION_TARGET = "transition"


def _rows(model: str, kind: ItemKind, role: str | None, fields: str) -> dict[tuple[str, str], _Row]:
    return {(model, field): (kind, role) for field in fields.split()}


ITEM_TABLE: Mapping[tuple[str, str], tuple[ItemKind, str | None]] = ReadOnlyMap(
    {
        **_rows("Show", "show", None, "id title pack dependencies extensions"),
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


def _role_less(owners: tuple[str, ...], kind: ItemKind, path: str | None) -> tuple[str, ...]:
    """Lists the fields that land in an item with no role, leaving out the one its path holds."""
    return tuple(
        field
        for (owner, field), (item_kind, role) in ITEM_TABLE.items()
        if owner in owners and item_kind == kind and role is None and field != path
    )


SHOW_FIELDS = _role_less(("Show", "Cast"), "show", None)
SET_IDENTITY_FIELDS = _role_less(("Set",), "set", KEY_FIELD)
TRANSITION_FIELDS = _role_less(("TransitionSpec",), "transition", ID_FIELD)


def set_keys_by_id(show: Show) -> dict[str, str]:
    """Maps each set ID of a show to the set's storage key.

    >>> from e8s.examples import two_set_show
    >>> set_keys_by_id(two_set_show())
    {'1': '1', '2': '2'}
    """
    return {current.id: set_key(current) for current in show.sets}


def transition_key(keys: Mapping[str, str], tid: tuple[str, str]) -> str:
    """Returns a transition's item key: the stored folder of its set keys; an unknown ID stays.

    >>> transition_key({"1": "1", "2": "2A"}, ("1", "2"))
    '1-2A'
    """
    return transition_folder((keys.get(tid[0], tid[0]), keys.get(tid[1], tid[1])))


def pin_path(keys: Mapping[str, str], record: Pin) -> ItemPath:
    """Returns a pin's item path, naming a set or transition by set keys, as stored folders do.

    >>> from e8s.show.models import Pin
    >>> pin_path({"1": "1", "2": "2A"}, Pin(target="transition", id="1→2"))
    ('transition', '1-2A')
    """
    if record.target == SET_TARGET:
        return record.target, keys.get(record.id, record.id)
    if record.target == TRANSITION_TARGET:
        return record.target, transition_key(keys, parse_tid(record.id))
    return record.target, record.id
