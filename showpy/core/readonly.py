"""
readonly.py --- a read-only mapping that compares equal to a dict and survives pickling

Contains:
    ReadOnlyMap: wraps a copy of a mapping so no caller can change it
    read_only(): returns a ReadOnlyMap over a copy of a mapping
"""

from collections.abc import Iterator, Mapping


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


def read_only[K, V](items: Mapping[K, V]) -> Mapping[K, V]:
    """Returns a read-only view over a copy of a mapping.

    Args:
        items: Mapping to copy.

    Returns:
        frozen_map: A mapping that equals `items` and raises TypeError on item assignment.

    Examples:
        >>> read_only({"a": 1}) == {"a": 1}
        True
    """
    return ReadOnlyMap(items)
