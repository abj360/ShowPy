#!/usr/bin/env python3

"""
test_arrays.py --- tests read-only array fields and value equality of array models

Contains:
    test_array_field_stores_a_read_only_copy: copies the input and refuses writes
    test_regression_array_field_cannot_be_made_writeable: refuses to turn the writeable flag on
    test_array_model_round_trips_through_json: dumps nested lists and reads them back equal
    test_regression_zero_row_arrays_keep_their_columns_through_json: restores (0, n) shapes
    test_array_model_equality_and_hash_compare_values: equal arrays make equal, hash-equal models
    test_regression_signed_zeros_hash_alike: hashes -0.0 and 0.0 alike, as they compare equal
    test_regression_array_model_copies_validate: validates and freezes a copy's updated arrays
    test_regression_deepcopy_keeps_arrays_read_only: deep-copies to the same immutable model
    test_regression_array_field_rejects_bad_values: raises SchemaError for NaN, the wrong
        number of dimensions (an empty 3-D input included), text, objects and numbers in a
        boolean field
    test_array_field_describes_nested_arrays_in_json_schema: names nested number arrays
"""

import copy
from typing import Annotated

import numpy as np
import pytest

from e8s.core.arrays import ArrayModel, BoolArray, FloatArray, ReadOnlyArray
from e8s.core.errors import SchemaError


class _Grid(ArrayModel):
    names: tuple[str, ...]
    values: Annotated[FloatArray, ReadOnlyArray(2, columns="names")]
    flags: Annotated[BoolArray, ReadOnlyArray(1, np.bool_)]


def _grid(scale: float = 1.0) -> _Grid:
    return _Grid.model_validate(
        {"names": ["a", "b"], "values": [[1.0 * scale, 2.0], [3.0, 4.0]], "flags": [True, False]}
    )


def test_array_field_stores_a_read_only_copy() -> None:
    source = np.array([[1.0, 2.0], [3.0, 4.0]])
    grid = _Grid(names=("a", "b"), values=source, flags=np.array([True]))
    source[0, 0] = 9.0
    assert grid.values[0, 0] == 1.0
    with pytest.raises(ValueError, match="read-only"):
        grid.values[0, 0] = 5.0


def test_regression_array_field_cannot_be_made_writeable() -> None:
    grid = _grid()
    with pytest.raises(ValueError, match="WRITEABLE"):
        grid.values.flags.writeable = True
    assert grid.values[0, 0] == 1.0


def test_array_model_round_trips_through_json() -> None:
    grid = _grid()
    text = grid.model_dump_json()
    assert '"values":[[1.0,2.0],[3.0,4.0]]' in text
    assert _Grid.model_validate_json(text) == grid


def test_regression_zero_row_arrays_keep_their_columns_through_json() -> None:
    empty = _Grid(names=("a", "b", "c"), values=np.empty((0, 3)), flags=np.array([], dtype=bool))
    read = _Grid.model_validate_json(empty.model_dump_json())
    assert read.values.shape == (0, 3)
    assert read == empty


def test_array_model_equality_and_hash_compare_values() -> None:
    assert _grid() == _grid()
    assert hash(_grid()) == hash(_grid())
    assert _grid() != _grid(scale=2.0)
    assert _grid() != "g"


def test_regression_signed_zeros_hash_alike() -> None:
    positive, negative = _grid(scale=0.0), _grid(scale=-0.0)
    assert positive == negative
    assert hash(positive) == hash(negative)


def test_regression_array_model_copies_validate() -> None:
    copy = _grid().model_copy(update={"values": np.ones((2, 2))})
    assert copy.values.tolist() == [[1.0, 1.0], [1.0, 1.0]]
    with pytest.raises(ValueError, match="WRITEABLE"):
        copy.values.flags.writeable = True
    with pytest.raises(SchemaError):
        _grid().model_copy(update={"values": [1.0]})


def test_regression_deepcopy_keeps_arrays_read_only() -> None:
    grid = _grid()
    copied = copy.deepcopy(grid)
    assert copied == grid
    assert not copied.values.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        copied.values[0, 0] = 5.0
    nested = copy.deepcopy({"grid": grid})["grid"]
    assert not nested.values.flags.writeable


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("values", [[float("nan"), 1.0]]),
        ("values", [1.0, 2.0]),
        ("values", [["a", "b"]]),
        ("values", [["1.5", "2"]]),
        ("values", "12"),
        ("values", [[object()]]),
        ("values", [[1.0], [1.0, 2.0]]),
        ("flags", [1, 0]),
        ("values", [[[]]]),
    ],
)
def test_regression_array_field_rejects_bad_values(field: str, value: object) -> None:
    fields = {"names": ["a"], "values": [[1.0]], "flags": [True], field: value}
    with pytest.raises(SchemaError):
        _Grid.model_validate(fields)


def test_array_field_describes_nested_arrays_in_json_schema() -> None:
    schema = _Grid.model_json_schema()["properties"]
    assert schema["values"]["items"] == {"type": "array", "items": {"type": "number"}}
    assert schema["flags"]["items"] == {"type": "boolean"}
