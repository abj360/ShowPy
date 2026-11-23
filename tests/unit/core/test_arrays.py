#!/usr/bin/env python3

"""
test_arrays.py --- tests read-only array fields and value equality of array models

Contains:
    test_array_field_stores_a_read_only_copy: copies the input and refuses writes
    test_array_model_round_trips_through_json: dumps nested lists and reads them back equal
    test_array_model_equality_and_hash_compare_values: equal arrays make equal, hash-equal models
    test_regression_array_field_rejects_bad_values: raises SchemaError for NaN, the wrong
        number of dimensions and values that are not numbers
    test_array_field_describes_nested_arrays_in_json_schema: names nested number arrays
"""

from typing import Annotated

import numpy as np
import pytest

from e8s.core.arrays import ArrayModel, BoolArray, FloatArray, ReadOnlyArray
from e8s.core.errors import SchemaError


class _Grid(ArrayModel):
    name: str
    values: Annotated[FloatArray, ReadOnlyArray(2)]
    flags: Annotated[BoolArray, ReadOnlyArray(1, np.bool_)]


def _grid(scale: float = 1.0) -> _Grid:
    return _Grid.model_validate(
        {"name": "g", "values": [[1.0 * scale, 2.0], [3.0, 4.0]], "flags": [True, False]}
    )


def test_array_field_stores_a_read_only_copy() -> None:
    source = np.array([[1.0, 2.0], [3.0, 4.0]])
    grid = _Grid(name="g", values=source, flags=np.array([True]))
    source[0, 0] = 9.0
    assert grid.values[0, 0] == 1.0
    with pytest.raises(ValueError, match="read-only"):
        grid.values[0, 0] = 5.0


def test_array_model_round_trips_through_json() -> None:
    grid = _grid()
    text = grid.model_dump_json()
    assert '"values":[[1.0,2.0],[3.0,4.0]]' in text
    assert _Grid.model_validate_json(text) == grid


def test_array_model_equality_and_hash_compare_values() -> None:
    assert _grid() == _grid()
    assert hash(_grid()) == hash(_grid())
    assert _grid() != _grid(scale=2.0)
    assert _grid() != "g"


@pytest.mark.parametrize(
    "values",
    [[[float("nan"), 1.0]], [1.0, 2.0], [["a", "b"]], [[object()]]],
)
def test_regression_array_field_rejects_bad_values(values: object) -> None:
    with pytest.raises(SchemaError):
        _Grid.model_validate({"name": "g", "values": values, "flags": [True]})


def test_array_field_describes_nested_arrays_in_json_schema() -> None:
    schema = _Grid.model_json_schema()["properties"]
    assert schema["values"]["items"] == {"type": "array", "items": {"type": "number"}}
    assert schema["flags"]["items"] == {"type": "boolean"}
