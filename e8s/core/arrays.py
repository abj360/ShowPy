"""
arrays.py --- read-only numpy array fields for frozen models

Contains:
    FloatArray: a float64 numpy array
    BoolArray: a boolean numpy array
    ReadOnlyArray: annotates a field so it stores an immutable array copy and dumps nested lists
    ArrayModel: a frozen model whose equality, hash and copies treat array fields by value
"""

from collections.abc import Mapping
from typing import Self, override

import numpy as np
import numpy.typing as npt
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler, ValidationInfo
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from e8s.core.types import FrozenModel

type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool_]
type ArrayDtype = type[np.float64] | type[np.bool_]

# Element kinds each stored dtype accepts: numbers for floats, only booleans for booleans.
ACCEPTED_KINDS: dict[str, str] = {"f": "iuf", "b": "b"}


def _as_lists(array: npt.NDArray[np.generic]) -> object:
    return array.tolist()


def _immutable(array: npt.NDArray[np.generic]) -> npt.NDArray[np.generic]:
    """Returns a copy backed by bytes, so no caller can turn its writeable flag back on."""
    frozen: npt.NDArray[np.generic] = np.frombuffer(array.tobytes(), dtype=array.dtype)
    return frozen.reshape(array.shape)


class ReadOnlyArray:
    """Annotates a numpy array field: nested lists or arrays go in, an immutable copy is stored.

    The copy has the given dtype and number of dimensions; a float array must be finite and
    built from numbers, a boolean array from booleans. JSON dumps and the JSON schema are nested
    lists. A JSON list cannot hold the column count of an array with no rows, so a 2-D field
    names the earlier field whose length gives it. Use as
    `Annotated[FloatArray, ReadOnlyArray(2, columns="spots")]`.

    Attributes:
        ndim: Number of dimensions the array must have.
        dtype: Element type the array is stored as.
        columns: Earlier field whose length is the column count of an array with no rows.
    """

    def __init__(
        self, ndim: int, dtype: ArrayDtype = np.float64, columns: str | None = None
    ) -> None:
        self.ndim = ndim
        self.dtype = dtype
        self.columns = columns

    def _read(self, value: object) -> npt.NDArray[np.generic]:
        if isinstance(value, str | bytes):
            raise ValueError("an array field takes numbers, not text")
        try:
            raw = np.asarray(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"cannot read an array: {exc}") from exc
        accepted = ACCEPTED_KINDS[np.dtype(self.dtype).kind]
        if raw.size and raw.dtype.kind not in accepted:
            raise ValueError(f"array holds {raw.dtype} values, not {self.dtype.__name__}")
        return raw.astype(self.dtype)

    def _shaped(
        self, array: npt.NDArray[np.generic], info: ValidationInfo
    ) -> npt.NDArray[np.generic]:
        if array.size or array.ndim != 1 or self.ndim == 1 or self.columns is None:
            return array
        width = info.data.get(self.columns)
        columns = len(width) if isinstance(width, tuple | list) else 0
        return array.reshape((0,) * (self.ndim - 1) + (columns,))

    def _freeze(self, value: object, info: ValidationInfo) -> npt.NDArray[np.generic]:
        array = self._shaped(self._read(value), info)
        if array.ndim != self.ndim:
            raise ValueError(f"array needs {self.ndim} dimensions, got {array.ndim}")
        if self.dtype is np.float64 and not np.isfinite(array).all():
            raise ValueError("array holds NaN or infinite values")
        return _immutable(array)

    def __get_pydantic_core_schema__(
        self, _source: object, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Validates through an immutable copy and serializes JSON as nested lists."""
        return core_schema.with_info_plain_validator_function(
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
    if not isinstance(value, np.ndarray):
        return value
    # Adding 0.0 turns -0.0 into 0.0, so values that compare equal hash alike.
    canonical = value + 0.0 if value.dtype.kind == "f" else value
    return value.dtype.str, value.shape, canonical.tobytes()


class ArrayModel(FrozenModel):
    """Provides the frozen base for models with array fields: equal, hashed and copied by value.

    Pydantic compares fields with `==`, which an array answers elementwise, and copies without
    validating, so models that hold `ReadOnlyArray` fields inherit from this class instead of
    FrozenModel. A deep copy is the model itself, since nothing in it can change. Building a
    model without validation skips the copy, so arrays given that way are not frozen.
    """

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return all(
            _same(getattr(self, name), getattr(other, name)) for name in type(self)._field_names()
        )

    def __hash__(self) -> int:
        return hash(tuple(_hashable(getattr(self, name)) for name in type(self)._field_names()))

    @override
    def _copied(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        """Returns a validated copy, so an update cannot store a writable or misshapen array."""
        return self._validated({**self._dumped(), **(update or {})})

    @override
    def __deepcopy__(self, memo: dict[int, object] | None = None) -> Self:
        """Returns the model itself: its arrays are immutable, so a copy could only loosen them."""
        return self
