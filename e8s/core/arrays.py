"""
arrays.py --- read-only numpy array fields for frozen models

Contains:
    FloatArray: a float64 numpy array
    BoolArray: a boolean numpy array
    ReadOnlyArray: annotates a field so it stores a read-only array copy and dumps nested lists
    ArrayModel: a frozen model whose equality and hash compare array fields by value
"""

import numpy as np
import numpy.typing as npt
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from e8s.core.types import FrozenModel

type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool_]
type ArrayDtype = type[np.float64] | type[np.bool_]


def _as_lists(array: npt.NDArray[np.generic]) -> object:
    return array.tolist()


class ReadOnlyArray:
    """Annotates a numpy array field: nested lists or arrays go in, a read-only copy is stored.

    The copy has the given dtype and number of dimensions, and a float array must be finite. JSON
    dumps and the JSON schema are nested lists. Use as `Annotated[FloatArray, ReadOnlyArray(1)]`.

    Attributes:
        ndim: Number of dimensions the array must have.
        dtype: Element type the array is stored as.
    """

    def __init__(self, ndim: int, dtype: ArrayDtype = np.float64) -> None:
        self.ndim = ndim
        self.dtype = dtype

    def _freeze(self, value: object) -> npt.NDArray[np.generic]:
        try:
            array: npt.NDArray[np.generic] = np.array(value, dtype=self.dtype)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cannot read an array of {self.dtype.__name__}: {exc}") from exc
        if array.ndim != self.ndim:
            raise ValueError(f"array needs {self.ndim} dimensions, got {array.ndim}")
        if self.dtype is np.float64 and not np.isfinite(array).all():
            raise ValueError("array holds NaN or infinite values")
        array.flags.writeable = False
        return array

    def __get_pydantic_core_schema__(
        self, _source: object, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Validates through a read-only copy and serializes JSON as nested lists."""
        return core_schema.no_info_plain_validator_function(
            self._freeze,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _as_lists, when_used="json"
            ),
        )

    def __get_pydantic_json_schema__(
        self, _schema: CoreSchema, _handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Describes the field as nested JSON arrays of numbers or booleans."""
        item: JsonSchemaValue = {"type": "boolean" if self.dtype is np.bool_ else "number"}
        for _ in range(self.ndim):
            item = {"type": "array", "items": item}
        return item


def _same(a: object, b: object) -> bool:
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and np.array_equal(a, b)
    return a == b


def _hashable(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.dtype.str, value.shape, value.tobytes()
    return value


class ArrayModel(FrozenModel):
    """Provides the frozen base for models with array fields: equal and hashed by value.

    Pydantic compares fields with `==`, which an array answers elementwise, so models that hold
    `ReadOnlyArray` fields inherit from this class instead of FrozenModel.
    """

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return all(
            _same(getattr(self, name), getattr(other, name)) for name in type(self).model_fields
        )

    def __hash__(self) -> int:
        return hash(tuple(_hashable(getattr(self, name)) for name in type(self).model_fields))
