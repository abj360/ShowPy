#!/usr/bin/env python3

"""
test_items.py --- tests the item table, the items of a show and the changes between two shows

Contains:
    test_every_stored_field_has_an_item_row: walks the v2 schema against ITEM_TABLE; the envelope
        and Show.cast, which holds only the Cast and its own rows, have no row
    test_every_value_row_lands_in_its_item: finds each container row's field in its item's value
    test_whole_item_models_map_every_field_to_their_kind: keeps whole items undivided
    test_roles_are_the_design_roles: pins the roles and the role-less fields the table derives
    test_changes_of_a_show_with_itself_is_empty: finds nothing between a show and itself
    test_one_spot_move_is_one_drill_change: reports a move as one placement drill item
    test_turn_and_note_on_one_spot_are_two_roles: reports a turn and a note as two items
    test_items_cover_every_kind_and_no_notes_or_proposals: lists the kinds a rich show holds
    test_items_key_transitions_by_set_keys: keys paths, rows and routes by the stored folder
    test_reorders_are_one_order_change_each: reports a spot, pin or placement reorder as one item
    test_renumber_changes_set_identities_only: changes only set identities when forms, paths and
        pins follow a renumber
    test_added_and_removed_items_have_no_other_side: leaves the missing side None
    test_changes_skip_sets_shared_by_identity: dumps only the changed set's placements, no shared
        set, form or path row
    test_set_and_spec_values_match_their_json: reads set and path spec fields as to_json writes them
    test_changes_refuse_*: refuses two stored values that claim one item
"""

import json
from collections.abc import Iterator, Mapping
from typing import get_args

import pytest

from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, PlacementPatch, Set
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.core.errors import SchemaError
from e8s.core.ids import FormId, SetId, SpotId, make_tid
from e8s.core.readonly import freeze_json
from e8s.core.resources import schema_path
from e8s.examples import two_set_show
from e8s.show import items as items_module
from e8s.show.change_models import ItemKey, ItemKind
from e8s.show.documents import decode_show, encode_show
from e8s.show.items import (
    ITEM_TABLE,
    PLACEMENT_ROLES,
    SET_IDENTITY_FIELDS,
    SET_ROLES,
    SHOW_FIELDS,
    TRANSITION_FIELDS,
    changes,
    items,
)
from e8s.show.manage import add_set
from e8s.show.models import Pin, Show
from e8s.show.pinning import pin
from e8s.show.renumber import renumber
from e8s.show.set_keys import set_key
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.recipes import LINE_RECIPE

SCHEMA = "e8.show.v2.json"
DEFS = "$defs"
SHOW_DOCUMENT = "ShowDocument"
ENVELOPE_FIELDS = frozenset({"format", "version"})
CONTAINER_FIELDS = {("Show", "cast"): "Cast"}
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
VALUE_ITEMS = {
    "Show": ItemKey(kind="show", path=()),
    "Cast": ItemKey(kind="show", path=()),
    "Set": ItemKey(kind="set", path=("1",)),
    "Placement": ItemKey(kind="placement", path=("1", "T1")),
    "TransitionSpec": ItemKey(kind="transition", path=("1-2",)),
}
PATH_FIELDS = frozenset({("Set", "key"), ("Placement", "entity"), ("TransitionSpec", "id")})


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
    kept = {k: v for k, v in found.items() if (model, k) not in CONTAINER_FIELDS}
    return {k: v for k, v in kept.items() if not (model == "Show" and k in ENVELOPE_FIELDS)}


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
    for (model, field), held in CONTAINER_FIELDS.items():
        definition = SHOW_DOCUMENT if model == "Show" else model
        assert list(_refs(defs[definition]["properties"][field])) == [held]
        assert (model, field) not in ITEM_TABLE
    stale = [row for row in ITEM_TABLE if row[0] not in TABLE_MODELS]
    stale += [row for row in ITEM_TABLE if row[1] not in _properties(defs, row[0])]
    assert not stale, f"ITEM_TABLE rows that name no stored field: {stale}"


def test_every_value_row_lands_in_its_item() -> None:
    found = items(_pinned_rich_show())
    checked = []
    for (model, field), (kind, role) in ITEM_TABLE.items():
        target = VALUE_ITEMS.get(model)
        if target is None or target.kind != kind or (model, field) in PATH_FIELDS:
            continue
        value = found[ItemKey(kind=kind, path=target.path, role=role)]
        if role == "placements":
            assert isinstance(value, list)
            continue
        assert isinstance(value, dict)
        assert field in value, (model, field)
        checked.append((model, field))
    assert {model for model, _ in checked} == set(VALUE_ITEMS)


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
    assert SHOW_FIELDS == ("id", "title", "pack", "dependencies", "extensions", "label_scheme")
    assert SET_IDENTITY_FIELDS == ("id",)
    assert TRANSITION_FIELDS == ("counts", "notes", "source_hash")
    assert ITEM_TABLE["Set", "placements"] == ("set", "placements")
    assert ITEM_TABLE["Cast", "spots"] == ("spot_order", None)
    assert ITEM_TABLE["Show", "pins"] == ("pin_order", None)


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


def test_reorders_are_one_order_change_each() -> None:
    show = pin(_pinned_rich_show(), "form", "line1")
    first = show.sets[0]
    reorders = [
        (
            ItemKey(kind="spot_order", path=()),
            show.replace(cast=show.cast.replace(spots=tuple(reversed(show.cast.spots)))),
        ),
        (ItemKey(kind="pin_order", path=()), show.replace(pins=tuple(reversed(show.pins)))),
        (
            ItemKey(kind="set", path=(set_key(first),), role="placements"),
            show.replace(
                sets=(first.replace(placements=tuple(reversed(first.placements))), *show.sets[1:])
            ),
        ),
    ]
    for key, reordered in reorders:
        assert encode_show(reordered) != encode_show(show)
        (change,) = changes(show, reordered).changes
        assert change.key == key
    assert items(show)[ItemKey(kind="pin_order", path=())] == [["entity", "T1"], ["form", "line1"]]
    assert items(show)[ItemKey(kind="spot_order", path=())] == ["T1", "T2", "T3", "T4"]


def _renumbered_show() -> Show:
    show = add_set(rich_show(), "9", 4.0)
    form = Form(id=FormId("f9"), set=SetId("9"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    spec = TransitionSpec(
        id=make_tid("2", "9"), counts=4.0, rows={SpotId("T2"): PathRow(entity=SpotId("T2"))}
    )
    show = show.replace(forms={**show.forms, form.id: form}, transitions=(*show.transitions, spec))
    return pin(pin(pin(show, "set", "9"), "transition", "2→9"), "form", "f9")


def test_renumber_changes_set_identities_only() -> None:
    show = _renumbered_show()
    renamed, mapping = renumber(show)
    assert mapping[SetId("9")] == "3"
    assert renamed.forms[FormId("f9")].set == "3"
    assert [str(spec.id) for spec in renamed.transitions] == ["1→2", "2→3"]
    assert [(p.target, p.id) for p in renamed.pins] == [
        ("set", "3"),
        ("transition", "2→3"),
        ("form", "f9"),
    ]
    found = items(renamed)
    assert found[ItemKey(kind="pin_order", path=())] == [
        ["set", "9"],
        ["transition", "2-9"],
        ["form", "f9"],
    ]
    form = found[ItemKey(kind="form", path=("f9",))]
    assert isinstance(form, dict)
    assert form["set"] == "9"
    (change,) = changes(show, renamed).changes
    assert change.key == ItemKey(kind="set", path=("9",))
    assert [change.before, change.after] == [freeze_json({"id": "9"}), freeze_json({"id": "3"})]


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
    show = add_set(rich_show(), "3", 4.0)
    moved = _move(show, SECOND_SET, "T1", PlacementPatch(y=7.0))
    dumped: list[str] = []
    original = Placement.to_json

    def counting(self: Placement) -> dict[str, object]:
        dumped.append(self.entity)
        return dict(original(self))

    def refuse(_: object) -> None:
        raise AssertionError

    monkeypatch.setattr(Placement, "to_json", counting)
    for model in (Set, Form, PathRow):
        monkeypatch.setattr(model, "to_json", refuse)
    monkeypatch.setattr(items_module, "require_storable", refuse)
    assert len(changes(show, moved).changes) == 1
    assert sorted(dumped) == sorted(2 * [p.entity for p in show.sets[SECOND_SET].placements])


@pytest.mark.parametrize("show", [two_set_show(), rich_show()])
def test_set_and_spec_values_match_their_json(show: Show) -> None:
    found = items(show)
    for current in show.sets:
        fields = [v for k, v in found.items() if k.kind == "set" and k.path == (current.key,)]
        merged = {
            name: value for part in fields if isinstance(part, dict) for name, value in part.items()
        }
        assert merged == _without(current.to_json(), "key", "placements")
    for spec in show.transitions:
        (value,) = [v for k, v in found.items() if k.kind == "transition"]
        assert value == _without(spec.to_json(), "id", "rows", "routes")


def _without(dumped: Mapping[str, object], *names: str) -> dict[str, object]:
    return {name: value for name, value in dumped.items() if name not in names}


def test_changes_refuse_two_pins_of_one_target() -> None:
    show = _pinned_rich_show()
    twice = show.replace(pins=(*show.pins, Pin(target="entity", id="T1", reason="again")))
    with pytest.raises(SchemaError, match="repeats a pin"):
        items(twice)
    with pytest.raises(SchemaError, match="entity/T1") as caught:
        changes(show, twice)
    assert caught.value.hint == "keep one pin per target and ID; pin() replaces the old one"


def test_changes_refuse_two_paths_for_one_transition() -> None:
    show = rich_show()
    twice = show.replace(transitions=(*show.transitions, *show.transitions))
    with pytest.raises(SchemaError, match="path spec"):
        items(twice)
    with pytest.raises(SchemaError, match="1-2") as caught:
        changes(two_set_show(), twice)
    assert caught.value.hint == "store one path spec per set pair"
