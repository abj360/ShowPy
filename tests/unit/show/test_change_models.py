#!/usr/bin/env python3

"""
test_change_models.py --- tests the change and merge models

Contains:
    test_every_change_model_round_trips_through_json: each model survives to_json and from_json
    test_item_kind_*: lists the kinds a change can point at
    test_item_key_*: orders keys and refuses an empty role
    test_change_set_refuses_*: refuses changes that are unsorted or hold one item twice
"""

import pytest

from e8s.core.errors import SchemaError
from e8s.core.types import FrozenModel
from e8s.show.change_models import (
    ChangeSet,
    ItemChange,
    ItemKey,
    ItemKind,
)

PLACEMENT_KEY = ItemKey(kind="placement", path=("2", "T1"), role="drill")
SET_KEY = ItemKey(kind="set", path=("2",))
CHANGE = ItemChange(key=PLACEMENT_KEY, before=None, after={"x": 1.5, "tags": ["a", [True]]})
MODELS: tuple[FrozenModel, ...] = (
    PLACEMENT_KEY,
    CHANGE,
    ChangeSet(changes=(CHANGE, ItemChange(key=SET_KEY, before=3, after=None))),
)


@pytest.mark.parametrize("model", MODELS, ids=lambda model: type(model).__name__)
def test_every_change_model_round_trips_through_json(model: FrozenModel) -> None:
    assert type(model).from_json(model.to_json()) == model


def test_item_kind_lists_the_fifteen_kinds_in_order() -> None:
    assert ItemKind.__value__.__args__ == (
        "placement",
        "set",
        "set_order",
        "form",
        "path_row",
        "transition",
        "route",
        "spot",
        "group",
        "selection",
        "pin",
        "facing_override",
        "sheet",
        "timeline",
        "show",
    )


def test_item_key_sort_key_puts_a_missing_role_first() -> None:
    assert ItemKey(kind="spot", path=("T1",)).sort_key() == ("spot", ("T1",), "")


def test_item_key_refuses_an_empty_role() -> None:
    with pytest.raises(SchemaError) as info:
        ItemKey(kind="spot", path=("T1",), role="")
    assert "None" in info.value.hint


def test_change_set_refuses_unsorted_changes() -> None:
    later = ItemChange(key=SET_KEY, before=1, after=2)
    with pytest.raises(SchemaError) as info:
        ChangeSet(changes=(later, CHANGE))
    assert "sort_key" in info.value.hint


def test_change_set_refuses_one_item_twice() -> None:
    with pytest.raises(SchemaError):
        ChangeSet(changes=(CHANGE, CHANGE))
