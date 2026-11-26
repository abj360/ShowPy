#!/usr/bin/env python3

"""
test_item_assembly.py --- tests rebuilding a show from a base show and changed item values

Contains:
    test_assemble_replays_the_changes_of_an_edit: rebuilds each edited show from its changes
    test_assemble_shares_every_untouched_object: keeps the base's untouched sets, forms and specs
    test_assemble_renames_what_names_a_renumbered_set: renames forms, specs and pins with a set
    test_assemble_renames_only_what_names_a_renumbered_set: keeps other pins and specs shared
    test_assemble_refuses_items_that_disagree: refuses each kind of disagreeing or malformed value
    test_assemble_refuses_an_order_that_disagrees_with_its_items: names the order and identities
    test_assemble_refuses_a_placement_with_roles_removed: names the set's given items
    test_assemble_names_the_part_a_model_refuses: names the cast items for a cast refusal
    test_assemble_refuses_a_show_the_layout_cannot_store: names the structural items
    test_regression_refusal_reason_does_not_follow_value_order: gives one refusal for any order
"""

from collections.abc import Callable
from typing import cast

import pytest

from e8s.cast.models import Group
from e8s.cast.spots import add_group, add_spot, define_selection, delete_spots
from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.choreo.path_models import PathRow, TransitionSpec
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.ids import FormId, SetId, SpotId, make_tid
from e8s.core.json_types import FrozenJsonValue, JsonValue
from e8s.core.readonly import thaw_json
from e8s.show.change_models import ItemKey
from e8s.show.changes import store_paths
from e8s.show.collab_models import PathsOp
from e8s.show.documents import encode_show
from e8s.show.item_assembly import assemble
from e8s.show.item_reading import Refusal
from e8s.show.items import changes, items
from e8s.show.manage import add_set, remove_set
from e8s.show.models import Show
from e8s.show.pinning import pin, unpin
from e8s.show.renumber import renumber
from tests.fixtures.cast_shows import rich_show
from tests.fixtures.recipes import LINE_RECIPE

SECOND = 1
THIRD_SET = "3"


def _base() -> Show:
    return pin(add_set(rich_show(), THIRD_SET, 4.0), "entity", "T1")


def _values(before: Show, after: Show) -> dict[ItemKey, JsonValue]:
    return {
        c.key: thaw_json(cast("FrozenJsonValue", c.after)) for c in changes(before, after).changes
    }


def _patched(show: Show, position: int, spot: str, patch: PlacementPatch) -> Show:
    edited = show.sets[position].with_placements({spot: patch}, force=True)
    return show.replace(sets=tuple(edited if i == position else s for i, s in enumerate(show.sets)))


def _reversed_spots(show: Show) -> Show:
    return show.replace(cast=show.cast.replace(spots=tuple(reversed(show.cast.spots))))


def _row_note(show: Show) -> Show:
    spec = show.transitions[0]
    row = spec.rows[SpotId("T1")].replace(note="lead")
    return show.replace(transitions=(spec.replace(rows={**spec.rows, row.entity: row}),))


def _new_form(show: Show) -> Show:
    form = Form(id=FormId("f3"), set=SetId(THIRD_SET), recipe=LINE_RECIPE, members=(SpotId("T2"),))
    return show.replace(forms={**show.forms, form.id: form})


def _new_paths(show: Show) -> Show:
    rows = {SpotId("T2"): PathRow(entity=SpotId("T2"), note="late")}
    return store_paths(
        show, PathsOp(spec=TransitionSpec(id=make_tid("2", "3"), counts=4.0, rows=rows))
    )


EDITS: dict[str, Callable[[Show], Show]] = {
    "move": lambda show: _patched(show, SECOND, "T2", PlacementPatch(x=3.0)),
    "turn and note": lambda show: _patched(
        show, SECOND, "T2", PlacementPatch(facing=90.0, note="solo")
    ),
    "add a set": lambda show: add_set(show, "1A", 4.0, after="1"),
    "remove a set": lambda show: remove_set(show, "2", force=True),
    "delete a spot": lambda show: delete_spots(show, "T4", force=True),
    "add a spot": lambda show: add_spot(show, (1.0, 2.0), "trumpets", "1"),
    "reverse spots": _reversed_spots,
    "add a group": lambda show: add_group(show, Group(key="drums", label_prefix="D")),
    "select": lambda show: define_selection(show, "solo", "T2"),
    "pin a set": lambda show: pin(show, "set", "2"),
    "pin a spot": lambda show: pin(show, "entity", "T2"),
    "unpin": lambda show: unpin(show, "entity", "T1"),
    "new form": _new_form,
    "drop forms": lambda show: show.replace(forms={}),
    "path row note": _row_note,
    "drop paths": lambda show: show.replace(transitions=()),
    "title": lambda show: show.replace(title="Renamed"),
    "drop overrides": lambda show: show.replace(facing_overrides=()),
    "set label": lambda show: show.replace(
        sets=(show.sets[0].replace(label="opener"), *show.sets[1:])
    ),
    "new paths": _new_paths,
    "sheet": lambda show: show.replace(sheet=show.sheet.replace(name="Gym")),
    "timeline": lambda show: show.replace(timeline=show.timeline.replace(total_counts=64.0)),
}


@pytest.mark.parametrize("edit", EDITS.values(), ids=EDITS.keys())
def test_assemble_replays_the_changes_of_an_edit(edit: Callable[[Show], Show]) -> None:
    base = _base()
    edited = edit(base)
    rebuilt = assemble(base, _values(base, edited))
    assert isinstance(rebuilt, Show)
    assert items(rebuilt) == items(edited)
    assert encode_show(rebuilt) == encode_show(edited)


def test_assemble_shares_every_untouched_object() -> None:
    base = _base()
    rebuilt = assemble(base, _values(base, EDITS["move"](base)))
    assert isinstance(rebuilt, Show)
    assert [rebuilt.sets[i] is base.sets[i] for i in range(len(base.sets))] == [True, False, True]
    moved, kept = rebuilt.sets[SECOND].placements, base.sets[SECOND].placements
    assert [a is b for a, b in zip(moved, kept, strict=True)] == [True, False, True, True]
    assert rebuilt.forms[FormId("line1")] is base.forms[FormId("line1")]
    assert rebuilt.transitions[0] is base.transitions[0]
    assert rebuilt.cast is base.cast


def _renumbered_base() -> Show:
    show = add_set(rich_show(), "9", 4.0)
    form = Form(id=FormId("f9"), set=SetId("9"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    show = show.replace(forms={**show.forms, form.id: form})
    spec = TransitionSpec(
        id=make_tid("2", "9"), counts=4.0, rows={SpotId("T2"): PathRow(entity=SpotId("T2"))}
    )
    show = show.replace(transitions=(*show.transitions, spec))
    return pin(pin(pin(show, "set", "9"), "transition", "2→9"), "form", "f9")


def test_assemble_renames_what_names_a_renumbered_set() -> None:
    base = _renumbered_base()
    renamed, _ = renumber(base)
    values = _values(base, renamed)
    assert [key.kind for key in values] == ["set"]
    rebuilt = assemble(base, values)
    assert isinstance(rebuilt, Show)
    assert rebuilt.forms[FormId("f9")].set == "3"
    assert [str(spec.id) for spec in rebuilt.transitions] == ["1→2", "2→3"]
    assert [(p.target, p.id) for p in rebuilt.pins] == [
        ("set", "3"),
        ("transition", "2→3"),
        ("form", "f9"),
    ]
    assert encode_show(rebuilt) == encode_show(renamed)


def test_assemble_renames_only_what_names_a_renumbered_set() -> None:
    base = pin(add_set(rich_show(), "9", 4.0), "entity", "T1")
    renamed, _ = renumber(base)
    rebuilt = assemble(base, _values(base, renamed))
    assert isinstance(rebuilt, Show)
    assert rebuilt.pins[0] is base.pins[0]
    assert rebuilt.transitions[0] is base.transitions[0]
    assert encode_show(rebuilt) == encode_show(renamed)


def _refusal(base: Show, *given: tuple[ItemKey, JsonValue]) -> Refusal:
    refused = assemble(base, dict(given))
    assert isinstance(refused, Refusal)
    return refused


def test_assemble_refuses_an_order_that_disagrees_with_its_items() -> None:
    base = _base()
    refused = _refusal(base, (SET_ORDER, ["1", "2", "3", "4"]))
    assert refused.keys == (SET_ORDER,)
    assert isinstance(refused.error, SchemaError)
    assert "set order" in refused.error.message


def test_assemble_refuses_a_placement_with_roles_removed() -> None:
    base = _base()
    drill = ItemKey(kind="placement", path=("2", "T2"), role="drill")
    facing = ItemKey(kind="placement", path=("2", "T2"), role="facing")
    refused = _refusal(base, (drill, None), (facing, {"facing": 90.0}))
    assert refused.keys == (drill, facing)
    assert "roles removed" in refused.error.message


DRILL_VALUE: JsonValue = {"x": 1.0, "y": 1.0, "on_field": True, "form": None, "order": None}
SET_ORDER = ItemKey(kind="set_order", path=())
DISAGREEING: dict[str, tuple[tuple[ItemKey, JsonValue], ...]] = {
    "new placement lacks roles": (
        (ItemKey(kind="placement", path=("2", "T9"), role="drill"), DRILL_VALUE),
        (ItemKey(kind="set", path=("2",), role="placements"), ["T1", "T2", "T3", "T4", "T9"]),
    ),
    "new set lacks items": (
        (ItemKey(kind="set", path=("9",)), {"id": "9"}),
        (SET_ORDER, ["1", "2", "3", "9"]),
    ),
    "removed set keeps items": (
        (ItemKey(kind="set", path=("3",)), None),
        (ItemKey(kind="set", path=("3",), role="timing"), {"counts": 5.0}),
    ),
    "removed transition keeps items": (
        (ItemKey(kind="transition", path=("1-2",)), None),
        (ItemKey(kind="path_row", path=("1-2", "T1")), {}),
    ),
    "new transition lacks its item": ((ItemKey(kind="path_row", path=("2-3", "T1")), {}),),
    "transition key names one set": ((ItemKey(kind="transition", path=("2",)), {"counts": 1.0}),),
    "show item removed": ((ItemKey(kind="show", path=()), None),),
    "sheet removed": ((ItemKey(kind="sheet", path=()), None),),
    "overrides not a list": ((ItemKey(kind="facing_override", path=()), {}),),
    "value not an object": ((ItemKey(kind="placement", path=("2", "T1"), role="drill"), 3),),
    "order not of IDs": ((SET_ORDER, [1, 2]),),
    "pin order not a list": ((ItemKey(kind="pin_order", path=()), "T1"),),
}


@pytest.mark.parametrize("given", DISAGREEING.values(), ids=DISAGREEING.keys())
def test_assemble_refuses_items_that_disagree(given: tuple[tuple[ItemKey, JsonValue], ...]) -> None:
    refused = _refusal(_base(), *given)
    assert isinstance(refused.error, SchemaError)
    assert "the items disagree" in refused.error.message
    given_keys = [key for key, _ in given]
    assert all(key in given_keys for key in refused.keys)


def test_assemble_names_the_part_a_model_refuses() -> None:
    base = _base()
    without = delete_spots(base, "T4", force=True)
    values = _values(base, without)
    values[ItemKey(kind="selection", path=("pair",))] = ["T1", "T4"]
    refused = _refusal(base, *values.items())
    assert isinstance(refused.error, UnknownRefError)
    assert {key.kind for key in refused.keys} == {"spot", "spot_order", "selection"}


def test_assemble_refuses_a_show_the_layout_cannot_store() -> None:
    base = _base()
    values = _values(base, remove_set(base, THIRD_SET, force=True))
    values |= _values(base, _new_form(base))
    refused = _refusal(base, *values.items())
    assert ItemKey(kind="form", path=("f3",)) in refused.keys
    assert ItemKey(kind="set", path=(THIRD_SET,)) in refused.keys
    assert all(key.kind != "placement" for key in refused.keys)


HALF_REMOVED: tuple[tuple[str, JsonValue], ...] = (("drill", None), ("facing", {"facing": 90.0}))


def test_regression_refusal_reason_does_not_follow_value_order() -> None:
    base = _base()
    given: list[tuple[ItemKey, JsonValue]] = [
        (ItemKey(kind="placement", path=("2", spot), role=role), value)
        for spot in ("T2", "T3")
        for role, value in HALF_REMOVED
    ]
    forward = _refusal(base, *given)
    backward = _refusal(base, *reversed(given))
    assert forward.error.message == backward.error.message
    assert forward.keys == backward.keys
