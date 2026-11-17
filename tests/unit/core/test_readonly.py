#!/usr/bin/env python3

"""
test_readonly.py --- tests ReadOnlyMap: no writes, dict equality, pickling and copying

Contains:
    test_*(): one behavior of showpy.core.readonly each
"""

import copy
import pickle

import pytest

from showpy.core.readonly import ReadOnlyMap, read_only


def test_read_only_map_equals_a_dict_and_reads_like_one() -> None:
    table = read_only({"a": 1, "b": 2})
    assert table == {"a": 1, "b": 2}
    assert table["a"] == 1
    assert list(table) == ["a", "b"]
    assert len(table) == 2


def test_read_only_map_refuses_writes() -> None:
    table = read_only({"a": 1})
    with pytest.raises(TypeError):
        table["a"] = 2  # type: ignore[index]
    with pytest.raises(AttributeError):
        table.update({"b": 1})  # type: ignore[attr-defined]


def test_read_only_map_copies_its_source() -> None:
    source = {"a": 1}
    table = read_only(source)
    source["a"] = 2
    assert table == {"a": 1}


def test_read_only_map_survives_pickle_and_deepcopy() -> None:
    table = read_only({"a": [1]})
    assert pickle.loads(pickle.dumps(table)) == table
    assert copy.deepcopy(table) == table
    assert isinstance(copy.deepcopy(table), ReadOnlyMap)
