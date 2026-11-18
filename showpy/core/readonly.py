"""
readonly.py --- a read-only mapping that compares equal to a dict and survives pickling

Contains:
    ReadOnlyMap: wraps a copy of a mapping so no caller can change it
    ReadOnly: annotates a model field so it validates from any mapping and stores a ReadOnlyMap
"""

from collections.abc import Iterator, Mapping
from typing import Any, get_args

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema


class ReadOnlyMap[K, V](Mapping[K, V]):
    """Wraps a copy of a mapping so no caller can change it.

    Unlike `types.MappingProxyType`, it pickles and deep-copies, so models that hold one do too.
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
