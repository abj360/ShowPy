"""
readonly.py --- read-only mappings and JSON values that hash, pickle and dump as plain JSON

Contains:
    ReadOnlyMap: wraps a copy of a mapping so no caller can change it; hashes by content
    ReadOnly: annotates a model field so it validates from any mapping and stores a ReadOnlyMap
    FrozenJson: annotates a JSON object field so every nested list and object is read-only too
    freeze_json(): returns a deep read-only copy of a JSON value
    thaw_json(): returns the plain JSON value of a frozen one
"""

from collections.abc import Callable, Iterator, Mapping
from typing import Any, get_args

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from e8s.core.json_types import FrozenJsonValue, JsonValue


class ReadOnlyMap[K, V](Mapping[K, V]):
    """Wraps a copy of a mapping so no caller can change it.

    Unlike `types.MappingProxyType`, it pickles, deep-copies and hashes, so models that hold one
    do too. Equal maps hash alike whatever their insertion order; a map holding an unhashable
    value raises TypeError on `hash`, as a tuple holding a list does.
    """

    def __init__(self, items: Mapping[K, V]) -> None:
        self._items = dict(items)

    def __getitem__(self, key: K) -> V:
        return self._items[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"ReadOnlyMap({self._items!r})"

    def __hash__(self) -> int:
        return hash(frozenset(self._items.items()))

    def __reduce__(self) -> tuple[type["ReadOnlyMap[K, V]"], tuple[dict[K, V]]]:
        return ReadOnlyMap, (self._items,)


class ReadOnly:
    """Annotates a mapping field: any mapping goes in, a ReadOnlyMap is stored, a dict dumps out.

    Use as `Annotated[Mapping[str, Font], ReadOnly()]` so callers still pass plain dicts.
    """

    # Any: pydantic passes the parametrized source type, which has no static type.
    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Validates as the plain mapping, wraps the result, and serializes it as a dict."""
        key_type, value_type = get_args(source) or (Any, Any)
        plain = handler.generate_schema(dict[key_type, value_type])  # type: ignore[valid-type]
        return core_schema.no_info_after_validator_function(
            ReadOnlyMap,
            plain,
            serialization=core_schema.wrap_serializer_function_ser_schema(
                lambda value, serialize: serialize(dict(value)), schema=plain
            ),
        )


def freeze_json(value: JsonValue) -> FrozenJsonValue:
    """Returns a deep read-only copy of a JSON value: lists become tuples, objects ReadOnlyMaps.

    Args:
        value: A plain JSON value.

    Returns:
        frozen: The same value with every container read-only and hashable.

    >>> freeze_json({"k": [1, {"n": None}]})
    ReadOnlyMap({'k': (1, ReadOnlyMap({'n': None}))})
    """
    if isinstance(value, list):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, dict):
        return ReadOnlyMap({key: freeze_json(item) for key, item in value.items()})
    return value


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    """Returns the plain JSON value of a frozen one: tuples become lists, mappings dicts.

    Args:
        value: A value built by `freeze_json`, or any plain JSON value.

    Returns:
        plain: The same value with lists and dicts for containers.

    >>> thaw_json(freeze_json({"k": [1, {"n": None}]}))
    {'k': [1, {'n': None}]}
    """
    if isinstance(value, tuple | list):
        return [thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    return value


def _freeze_object(value: dict[str, JsonValue]) -> ReadOnlyMap[str, FrozenJsonValue]:
    return ReadOnlyMap({key: freeze_json(item) for key, item in value.items()})


def _thaw_object(
    value: Mapping[str, FrozenJsonValue], serialize: Callable[[object], object]
) -> object:
    return serialize({key: thaw_json(item) for key, item in value.items()})


class FrozenJson:
    """Annotates a JSON object field: plain JSON goes in, a deep read-only copy is stored.

    Nested lists are stored as tuples and nested objects as ReadOnlyMaps, so the model stays
    immutable and hashable; dumps and the JSON schema stay plain JSON arrays and objects. Use as
    `Annotated[Mapping[str, FrozenJsonValue], FrozenJson()]`.
    """

    def __get_pydantic_core_schema__(
        self, _source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Validates as a plain JSON object, freezes the result, and serializes it as JSON."""
        plain = handler.generate_schema(dict[str, JsonValue])
        return core_schema.no_info_after_validator_function(
            _freeze_object,
            plain,
            serialization=core_schema.wrap_serializer_function_ser_schema(
                _thaw_object, schema=plain
            ),
        )
