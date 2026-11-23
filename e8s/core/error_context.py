"""
error_context.py --- bounds the debugging context and echoed input that every error carries

Contains:
    bounded_context(): bounds a context mapping to JSON values of limited size and depth
    more_count(): renders the `(+n more)` count of items left out of a list
    bounded_repr(): renders a value in at most 80 characters, cutting its middle to `…`; catches
        only the ValueError that `repr` raises for an over-long int, so other repr errors escape
"""

import heapq
import json
import math
import re
from collections.abc import Iterable, Mapping
from itertools import islice
from operator import itemgetter
from typing import TYPE_CHECKING, TypeGuard

import numpy as np

from e8s.core.json_types import JsonValue

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet

    from _typeshed import SupportsRichComparison

MAX_ECHOED_INPUT_CHARS = 80
MAX_CONTEXT_ITEMS = 20
# Deeper values are rendered as text; a reference cycle stops here too.
MAX_CONTEXT_DEPTH = 8
CUT_MARK = "…"
# A cut keeps both ends: the start names what the value is, the end holds a path's file name.
ECHO_HEAD_CHARS = 40
ECHO_TAIL_CHARS = MAX_ECHOED_INPUT_CHARS - ECHO_HEAD_CHARS - len(CUT_MARK)
# Ranking a set that `<` does not order bounds every item; past this many it is summarized.
MAX_RANKED_SET_ITEMS = 1_000
NUMBER_TYPES = frozenset({bool, int, float})
MORE_PATTERN = re.compile(r"\(\+[0-9]+ more\)")
# CPython refuses to print ints longer than sys.int_max_str_digits, which the environment can
# lower to 640 and no further; ints at or past this bound echo by size so every environment
# prints the same text.
ECHO_INT_LIMIT = 10**640


def bounded_context(context: Mapping[str, object]) -> dict[str, JsonValue]:
    """Bounds a context mapping to JSON values of limited size and depth.

    Text and reprs are cut to 80 characters. Lists, tuples, arrays, sets and mappings keep 20
    items or entries plus a `"(+n more)"` count. A set keeps its 20 smallest items: by value
    when it holds only text, only bytes, or only numbers without NaN, and otherwise by type
    name and bounded JSON text, so no hash seed changes the result. A set of over 1,000 items
    that is not ordered by value is not ranked: it becomes its type name and length and a
    `"(not shown: n unorderable items)"` marker. Numpy values become plain numbers or lists,
    mapping keys become text, non-finite floats become text, and anything else, or anything
    nested deeper than 8 levels, becomes its bounded repr, or its type name when the repr
    raises. Bounding a bounded context changes nothing.

    Cost is linear in the kept items, except for sets, whose iteration order can change with
    the hash seed: a set's item types are scanned once in full, and a set of at most 1,000
    items that is not ordered by value has every item bounded once to rank it.

    Args:
        context: Debugging values chosen by a raise site.

    Returns:
        bounded: A new dict of JSON values.

    >>> bounded_context({"ids": tuple(range(25))})["ids"][-1]
    '(+5 more)'
    """
    return _bounded_mapping(context, 0)


def _bounded_value(value: object, depth: int) -> JsonValue:
    """Bounds one context value nested `depth` levels down."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, np.generic):
        return _bounded_value(value.item(), depth)
    if isinstance(value, int):
        return value if abs(value) < ECHO_INT_LIMIT else bounded_repr(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else bounded_repr(value)
    if isinstance(value, str) or depth >= MAX_CONTEXT_DEPTH:
        return _safe_repr(value)
    if isinstance(value, np.ndarray):
        return _bounded_array(value, depth)
    if isinstance(value, Mapping):
        return _bounded_mapping(value, depth + 1)
    if isinstance(value, list | tuple):
        return _bounded_items(value, depth)
    if isinstance(value, set | frozenset):
        return _bounded_set(value, depth)
    return _safe_repr(value)


def _safe_repr(value: object) -> str:
    """Returns the bounded repr of a value, or its type name when its repr raises."""
    try:
        return bounded_repr(value)
    except (MemoryError, RecursionError):
        raise
    # Every other exception: a context value's own __repr__ can raise anything, and a failing
    # repr must not stop the error that carries it from being built.
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def _bounded_mapping[K](mapping: Mapping[K, object], depth: int) -> dict[str, JsonValue]:
    """Keeps the first 20 entries of a mapping, counting the rest; a capped one stays whole."""
    keep = MAX_CONTEXT_ITEMS + 1 if _is_capped_mapping(mapping) else MAX_CONTEXT_ITEMS
    bounded = {
        _safe_repr(key): _bounded_value(item, depth) for key, item in islice(mapping.items(), keep)
    }
    if len(mapping) > keep:
        bounded[more_count(len(mapping) - keep)] = None
    return bounded


def _is_capped_mapping[K](mapping: Mapping[K, object]) -> bool:
    """Tells whether a mapping is a capped one: 20 entries and its `"(+n more)"` key."""
    if len(mapping) != MAX_CONTEXT_ITEMS + 1:
        return False
    last = next(islice(mapping, MAX_CONTEXT_ITEMS, None))
    return isinstance(last, str) and MORE_PATTERN.fullmatch(last) is not None


def _bounded_array(array: np.ndarray, depth: int) -> JsonValue:
    """Converts only the first 20 rows of an array, counting the rest."""
    if array.ndim == 0:
        return _bounded_value(array.item(), depth)
    kept: list[object] = array[:MAX_CONTEXT_ITEMS].tolist()
    return _listed(kept, len(array) - len(kept), depth)


def _bounded_set(items: set[object] | frozenset[object], depth: int) -> list[JsonValue]:
    """Keeps the 20 smallest items of a set, counting the rest, in an order no hash seed moves."""
    if _is_totally_ordered(items):
        kept = heapq.nsmallest(MAX_CONTEXT_ITEMS, items)
        return _listed(kept, len(items) - len(kept), depth)
    if len(items) > MAX_RANKED_SET_ITEMS:
        return [
            f"<{type(items).__name__} of {len(items)} items>",
            f"(not shown: {len(items)} unorderable items)",
        ]
    ranked = heapq.nsmallest(
        MAX_CONTEXT_ITEMS, (_ranked(item, depth + 1) for item in items), key=itemgetter(0)
    )
    bounded = [value for _, value in ranked]
    if len(items) > len(ranked):
        bounded.append(more_count(len(items) - len(ranked)))
    return bounded


def _is_totally_ordered(
    items: set[object] | frozenset[object],
) -> "TypeGuard[AbstractSet[SupportsRichComparison]]":
    """Tells whether `<` totally orders a set: all text, all bytes, or numbers with no NaN."""
    kinds = {type(item) for item in items}
    if kinds <= {str} or kinds <= {bytes}:
        return True
    if not kinds <= NUMBER_TYPES:
        return False
    return float not in kinds or not any(
        isinstance(item, float) and math.isnan(item) for item in items
    )


def _ranked(item: object, depth: int) -> tuple[tuple[str, str], JsonValue]:
    """Bounds one set item and keys it by its type name and its bounded JSON text.

    Two items with the same key bound to the same value, so ties cannot reorder the output.
    """
    value = _bounded_value(item, depth)
    return (type(item).__qualname__, json.dumps(value, sort_keys=True)), value


def _bounded_items(items: list[object] | tuple[object, ...], depth: int) -> list[JsonValue]:
    """Keeps the first 20 items, counting the rest; an already capped list is kept whole."""
    kept = items if _is_capped(items) else items[:MAX_CONTEXT_ITEMS]
    return _listed(kept, len(items) - len(kept), depth)


def _listed(kept: Iterable[object], hidden: int, depth: int) -> list[JsonValue]:
    """Bounds the kept items and appends the count of hidden ones, if any."""
    bounded = [_bounded_value(item, depth + 1) for item in kept]
    if hidden:
        bounded.append(more_count(hidden))
    return bounded


def _is_capped(items: list[object] | tuple[object, ...]) -> bool:
    """Tells whether a list is a capped list: 20 items and its `"(+n more)"` count."""
    last = items[-1] if len(items) == MAX_CONTEXT_ITEMS + 1 else None
    return isinstance(last, str) and MORE_PATTERN.fullmatch(last) is not None


def more_count(hidden: int) -> str:
    """Renders the count of items left out of a list.

    >>> more_count(980)
    '(+980 more)'
    """
    return f"(+{hidden} more)"


def bounded_repr(value: object) -> str:
    """Renders a value for an error context in at most 80 characters, never raising on size.

    Text echoes as itself, ints of 640 or more digits as their bit count, and anything else as
    its `repr`, or as its type name when the repr holds an int too long to print. Text too
    long to fit keeps its first 40 and last 39 characters around a `…`, so a cut number never
    reads as a whole one and a cut path keeps its file name.

    Args:
        value: Input to echo, as received from the caller.

    Returns:
        echo: Printable text of at most 80 characters.

    >>> bounded_repr(10**700)
    '<int of 2326 bits>'
    >>> bounded_repr([1.5, "a"])
    "[1.5, 'a']"
    >>> bounded_repr("/srv/" + "d/" * 50 + "show.json")
    '/srv/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d…d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/show.json'
    """
    if isinstance(value, int) and abs(value) >= ECHO_INT_LIMIT:
        return f"<int of {value.bit_length()} bits>"
    try:
        text = str.__str__(value) if isinstance(value, str) else repr(value)
    except ValueError:
        text = f"<unprintable {type(value).__name__}>"
    if len(text) <= MAX_ECHOED_INPUT_CHARS:
        return text
    return text[:ECHO_HEAD_CHARS] + CUT_MARK + text[-ECHO_TAIL_CHARS:]
