#!/usr/bin/env python3

"""
test_readonly.py --- tests ReadOnlyMap: no writes, dict equality, pickling and copying

Contains:
    test_*(): one behavior of showpy.core.readonly each
"""

import copy
import pickle
from collections.abc import Mapping
from typing import Annotated

import pytest

from showpy.core.readonly import ReadOnly, ReadOnlyMap
from showpy.core.types import FrozenModel


def test_read_only_map_equals_a_dict_and_reads_like_one() -> None:
    table = ReadOnlyMap({"a": 1, "b": 2})
    assert table == {"a": 1, "b": 2}
    assert table["a"] == 1
    assert list(table) == ["a", "b"]
    assert len(table) == 2


def test_read_only_map_refuses_writes() -> None:
    table = ReadOnlyMap({"a": 1})
    with pytest.raises(TypeError):
        table["a"] = 2  # type: ignore[index]
    with pytest.raises(AttributeError):
        table.update({"b": 1})  # type: ignore[attr-defined]


def test_read_only_map_copies_its_source() -> None:
    source = {"a": 1}
    table = ReadOnlyMap(source)
    source["a"] = 2
    assert table == {"a": 1}


def test_read_only_map_survives_pickle_and_deepcopy() -> None:
    table = ReadOnlyMap({"a": [1]})
    assert pickle.loads(pickle.dumps(table)) == table
    assert copy.deepcopy(table) == table
    assert isinstance(copy.deepcopy(table), ReadOnlyMap)


def test_read_only_annotation_stores_a_read_only_map_and_dumps_a_dict() -> None:
    class Holder(FrozenModel):
        table: Annotated[Mapping[str, int], ReadOnly()]

    holder = Holder(table={"a": 1})
    assert isinstance(holder.table, ReadOnlyMap)
    assert holder.model_dump() == {"table": {"a": 1}}
    assert Holder.model_validate_json(holder.model_dump_json()) == holder


def test_read_only_map_hashes_equal_maps_alike_in_any_order() -> None:
    first, second = ReadOnlyMap({"a": 1, "b": (2, 3)}), ReadOnlyMap({"b": (2, 3), "a": 1})
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


def test_read_only_map_with_an_unhashable_value_is_unhashable() -> None:
    with pytest.raises(TypeError):
        hash(ReadOnlyMap({"a": ([1],)}))
