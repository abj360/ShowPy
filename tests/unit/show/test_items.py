#!/usr/bin/env python3

"""
test_items.py --- tests the item table, the items of a show and the changes between two shows

Contains:
    test_every_stored_field_has_an_item_row: walks the v2 schema against ITEM_TABLE
    test_whole_item_models_map_every_field_to_their_kind: keeps whole items undivided
    test_roles_are_the_design_roles: pins the placement and set roles and their fields
    test_changes_of_a_show_with_itself_is_empty: finds nothing between a show and itself
    test_one_spot_move_is_one_drill_change: reports a move as one placement drill item
    test_turn_and_note_on_one_spot_are_two_roles: reports a turn and a note as two items
    test_items_cover_every_kind_and_no_notes_or_proposals: lists the kinds a rich show holds
    test_items_key_transitions_by_set_keys: keys paths, rows and routes by the stored folder
    test_renumber_changes_set_identities_only: keeps placement keys through a renumber
    test_added_and_removed_items_have_no_other_side: leaves the missing side None
    test_changes_skip_sets_shared_by_identity: dumps only the sets that differ
    test_items_refuse_*: refuses two stored values that claim one item
"""

import json
from collections.abc import Iterator
from typing import get_args

import pytest

from e8s.choreo.models import PlacementPatch, Set
from e8s.core.errors import SchemaError
from e8s.core.ids import SetId
from e8s.core.readonly import freeze_json
from e8s.core.resources import schema_path
from e8s.examples import two_set_show
from e8s.show.change_models import ItemKey, ItemKind
from e8s.show.documents import decode_show, encode_show
from e8s.show.items import ITEM_TABLE, PLACEMENT_ROLES, SET_ROLES, changes, items
from e8s.show.manage import add_set
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin
from e8s.show.renumber import renumber
from e8s.show.set_keys import set_key
from tests.fixtures.cast_shows import rich_show

SCHEMA = "e8.show.v2.json"
DEFS = "$defs"
SHOW_DOCUMENT = "ShowDocument"
ENVELOPE_FIELDS = frozenset({"format", "version"})
CONTAINER_MODELS = frozenset({"Show", "Cast", "Set", "Placement", "TransitionSpec"})
WHOLE_ITEM_MODELS = {
    "Form": "form",
    "PathRow": "path_row",
    "Route": "route",
    "Spot": "spot",
    "Group": "group",
    "Pin": "pin",
    "FacingOverride": "facing_override",
    "Sheet": "sheet",
    "Timeline": "timeline",
}
TABLE_MODELS = CONTAINER_MODELS | WHOLE_ITEM_MODELS.keys()
VALUE_MODELS = frozenset(
    {
        "ApprovalState",
        "Heading",
        "JsonObject",
        "LabelScheme",
        "Meters",
        "NonNegCount",
        "SelectionKey",
        "SelectionSpots",
        "TransitionId",
    }
)
SECOND_SET = 1


def _schema() -> dict[str, object]:
    loaded: dict[str, object] = json.loads(schema_path(SCHEMA).read_text())
    return loaded


def _model_name(definition: str) -> str:
    return "Show" if definition == SHOW_DOCUMENT else definition


def _refs(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for name, value in node.items():
            if name == "$ref" and isinstance(value, str):
                yield value.rsplit("/", 1)[-1]
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def _properties(defs: dict[str, dict[str, dict[str, object]]], model: str) -> dict[str, object]:
    definition = SHOW_DOCUMENT if model == "Show" else model
    found = defs[definition]["properties"]
    return {k: v for k, v in found.items() if not (model == "Show" and k in ENVELOPE_FIELDS)}


def test_every_stored_field_has_an_item_row() -> None:
    schema = _schema()
    defs = schema[DEFS]
    assert isinstance(defs, dict)
    documents = [*_refs(schema["properties"]), *_refs(schema["patternProperties"])]
    stray_documents = {_model_name(d) for d in documents} - TABLE_MODELS
    assert not stray_documents, f"stored documents of models with no item rows: {stray_documents}"
    for model in sorted(TABLE_MODELS):
        fields = _properties(defs, model)
        missing = [f for f in fields if (model, f) not in ITEM_TABLE]
        assert not missing, f"{model} stores fields with no ITEM_TABLE row: {missing}"
        if model in CONTAINER_MODELS:
            reached = set(_refs(fields)) - TABLE_MODELS - VALUE_MODELS
            assert not reached, f"{model} reaches models neither items nor values: {reached}"
    stale = [row for row in ITEM_TABLE if row[0] not in TABLE_MODELS]
    stale += [row for row in ITEM_TABLE if row[1] not in _properties(defs, row[0])]
    assert not stale, f"ITEM_TABLE rows that name no stored field: {stale}"


def test_whole_item_models_map_every_field_to_their_kind() -> None:
    for (model, field), (kind, role) in ITEM_TABLE.items():
        if model in WHOLE_ITEM_MODELS:
            assert (kind, role) == (WHOLE_ITEM_MODELS[model], None), (model, field)


def test_roles_are_the_design_roles() -> None:
    assert dict(PLACEMENT_ROLES) == {
        "drill": ("x", "y", "on_field", "form", "order"),
        "facing": ("facing",),
        "labels": ("note",),
        "pin": ("pinned",),
    }
    assert dict(SET_ROLES) == {
        "timing": ("counts",),
        "labels": ("label", "cue"),
        "build": ("source_hash", "state"),
        "forms": ("forms",),
    }


@pytest.mark.parametrize("show", [two_set_show(), rich_show()])
def test_changes_of_a_show_with_itself_is_empty(show: Show) -> None:
    assert changes(show, show).changes == ()
    assert changes(show, decode_show(encode_show(show))).changes == ()


def _move(show: Show, position: int, spot: str, patch: PlacementPatch) -> Show:
    edited = show.sets[position].with_placements({spot: patch})
    return show.replace(sets=tuple(edited if i == position else s for i, s in enumerate(show.sets)))


def test_one_spot_move_is_one_drill_change() -> None:
    show = two_set_show()
    second = show.sets[SECOND_SET]
    assert second.id == "2"
    assert set_key(second) == "2"
    moved = _move(show, SECOND_SET, "T1", PlacementPatch(x=3.0))
    (change,) = changes(show, moved).changes
    assert change.key == ItemKey(kind="placement", path=(set_key(second), "T1"), role="drill")
    before = items(show)[change.key]
    assert isinstance(before, dict)
    assert change.before == freeze_json(before)
    assert change.after == freeze_json({**before, "x": 3.0})
    assert set(before) == {"x", "y", "on_field", "form", "order"}


def test_turn_and_note_on_one_spot_are_two_roles() -> None:
    show = two_set_show()
    edited = _move(show, SECOND_SET, "T2", PlacementPatch(facing=90.0, note="hit"))
    found = changes(show, edited).changes
    key = set_key(show.sets[SECOND_SET])
    assert [c.key for c in found] == [
        ItemKey(kind="placement", path=(key, "T2"), role="facing"),
        ItemKey(kind="placement", path=(key, "T2"), role="labels"),
    ]
    assert [c.after for c in found] == [freeze_json({"facing": 90.0}), freeze_json({"note": "hit"})]


def _pinned_rich_show() -> Show:
    return pin(rich_show(), "entity", "T1", reason="soloist")


def test_items_cover_every_kind_and_no_notes_or_proposals() -> None:
    found = items(_pinned_rich_show())
    assert {key.kind for key in found} == set(get_args(ItemKind.__value__))
    assert all("notes" not in key.path and "proposals" not in key.path for key in found)
    assert list(found) == sorted(found, key=ItemKey.sort_key)


def test_items_key_transitions_by_set_keys() -> None:
    show = rich_show()
    found = items(show)
    assert found[ItemKey(kind="transition", path=("1-2",))] == {
        "counts": 8.0,
        "notes": "",
        "source_hash": "",
    }
    row = found[ItemKey(kind="path_row", path=("1-2", "T3"))]
    assert isinstance(row, dict)
    assert row["route"] == "r1"
    assert "entity" not in row
    route = found[ItemKey(kind="route", path=("1-2", "r1"))]
    assert isinstance(route, dict)
    assert "id" not in route
    assert found[ItemKey(kind="selection", path=("pair",))] == ["T1", "T4"]
    assert found[ItemKey(kind="set_order", path=())] == ["1", "2"]
    assert found[ItemKey(kind="set", path=("1",))] == {"id": "1"}
    assert found[ItemKey(kind="set", path=("1",), role="forms")] == {"forms": []}
    form = found[ItemKey(kind="form", path=("line1",))]
    assert isinstance(form, dict)
    assert form["set"] == "1"


def test_renumber_changes_set_identities_only() -> None:
    show = add_set(two_set_show(), "9", 4.0)
    renamed, mapping = renumber(show)
    assert mapping[SetId("9")] == "3"
    found = changes(show, renamed).changes
    assert [c.key for c in found] == [ItemKey(kind="set", path=("9",))]
    assert [found[0].before, found[0].after] == [freeze_json({"id": "9"}), freeze_json({"id": "3"})]


def test_added_and_removed_items_have_no_other_side() -> None:
    show = two_set_show()
    longer = add_set(show, "3", 4.0)
    added = changes(show, longer).changes
    assert all(c.before is None for c in added if c.key.path[:1] == ("3",))
    assert ItemKey(kind="set", path=("3",), role="timing") in [c.key for c in added]
    removed = changes(longer, show).changes
    assert all(c.after is None for c in removed if c.key.path[:1] == ("3",))
    (order,) = [c for c in removed if c.key.kind == "set_order"]
    assert [order.before, order.after] == [freeze_json(["1", "2", "3"]), freeze_json(["1", "2"])]


def test_changes_skip_sets_shared_by_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    show = add_set(two_set_show(), "3", 4.0)
    moved = _move(show, SECOND_SET, "T1", PlacementPatch(y=7.0))
    dumped: list[str] = []
    original = Set.to_json

    def counting(self: Set) -> dict[str, object]:
        dumped.append(self.id)
        return dict(original(self))

    monkeypatch.setattr(Set, "to_json", counting)
    assert len(changes(show, moved).changes) == 1
    assert sorted(dumped) == ["2", "2"]


def test_items_refuse_two_pins_of_one_target() -> None:
    show = _pinned_rich_show()
    twice = show.replace(pins=(*show.pins, Pin(target="entity", id="T1", reason="again")))
    with pytest.raises(SchemaError, match="repeats a pin"):
        items(twice)


def test_items_refuse_two_paths_for_one_transition() -> None:
    show = rich_show()
    twice = show.replace(transitions=(*show.transitions, *show.transitions))
    with pytest.raises(SchemaError, match="path spec"):
        changes(two_set_show(), twice)
