"""
item_reading.py --- reads item values back into model fields, refusing values that disagree

Item values come as `items` and `changes` give them, with None for an item left out. A value of
the wrong shape, or items of one object that disagree, raise SchemaError like a model refusal.

Contains:
    ItemValues: maps item keys to their new JSON values; None leaves an item out
    Entries: holds some item values as a dict
    Fields: holds an object item value's fields
    Refusal: holds the given items of the part of a show the models refused, and the refusal
    RefusedError: carries a part's refusal out of nested builds
    AssemblyContext: holds a base show, new item values and the set ID of each set key
    AssemblyContext.set_id(): returns the set ID of a set key in the result
    AssemblyContext.tid(): returns the transition ID a transition key names in the result
    AssemblyContext.is_renamed(): reports whether a pin path names a renamed set or transition
    AssemblyContext.pin_id(): returns the ID of the object a pin path names in the result
    assembly_context(): builds the context of applying item values to a base show
    incoherent(): builds the refusal of items that disagree
    guarded(): runs one part's build, turning a model refusal into a RefusedError
    sorted_keys(): sorts item keys by ItemKey.sort_key()
    as_object(), as_texts(), as_text(), as_paths(): read a value as an object, IDs or pin paths
    all_removed(): reports whether every given item is left out
    of_kinds(): picks the item values of some kinds
    by_part(): groups item values by one part of their path
    named_values(): keys the item values of one kind by the last part of their path
    with_members(): applies item values to a mapping of members
    in_order(): lists members in an order item's order, refusing an order that disagrees
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from e8s.core.errors import E8sError, SchemaError
from e8s.core.ids import SetId, TransitionId
from e8s.core.json_types import JsonValue
from e8s.show.change_models import ItemKey, ItemKind
from e8s.show.item_table import (
    ID_FIELD,
    SET_TARGET,
    TRANSITION_TARGET,
    ItemPath,
    set_keys_by_id,
)
from e8s.show.models import Show
from e8s.show.set_keys import PAIR_PARTS, PAIR_SEPARATOR

type ItemValues = Mapping[ItemKey, JsonValue]
type Entries = dict[ItemKey, JsonValue]
type Fields = dict[str, JsonValue]


@dataclass(frozen=True)
class Refusal:
    """Holds the given items of the part of a show the models refused, and the refusal.

    Attributes:
        keys: The given items of the refused part, in key order.
        error: The refusal the models raised.
    """

    keys: tuple[ItemKey, ...]
    error: E8sError


class RefusedError(Exception):
    """Carries a part's refusal out of nested builds to the caller that reports it.

    Attributes:
        refusal: The refused part's items and the refusal.
    """

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.error.message)
        self.refusal = refusal


@dataclass(frozen=True)
class AssemblyContext:
    """Holds a base show, the new item values and the set ID the result gives each set key.

    Attributes:
        base: The show the values apply to.
        values: The new item values; None leaves an item out.
        ids: Each set key of the result mapped to its set ID.
        renamed: Set keys whose set ID differs from the base's.
    """

    base: Show
    values: ItemValues
    ids: Mapping[str, str]
    renamed: frozenset[str]

    def set_id(self, key: str) -> str:
        """Returns the set ID of a set key in the result; a key of no set stays as it is."""
        return self.ids.get(key, key)

    def tid(self, folder: str) -> TransitionId:
        """Returns the transition ID that a transition key names in the result."""
        ends = folder.split(PAIR_SEPARATOR)
        if len(ends) != PAIR_PARTS:
            raise incoherent(f"transition {folder} does not name two set keys", {"key": folder})
        return TransitionId(SetId(self.set_id(ends[0])), SetId(self.set_id(ends[1])))

    def is_renamed(self, path: ItemPath) -> bool:
        """Reports whether a pin path names a set or transition whose set ID changed."""
        if path[0] not in {SET_TARGET, TRANSITION_TARGET}:
            return False
        return any(end in self.renamed for end in path[1].split(PAIR_SEPARATOR))

    def pin_id(self, path: ItemPath) -> str:
        """Returns the ID of the pinned object that a pin path names in the result."""
        if path[0] == SET_TARGET:
            return self.set_id(path[1])
        if path[0] == TRANSITION_TARGET:
            return str(self.tid(path[1]))
        return path[1]


def incoherent(what: str, context: dict[str, JsonValue]) -> SchemaError:
    """Builds the refusal of item values that disagree, naming what disagrees."""
    return SchemaError(
        f"the items disagree: {what}",
        hint="change an object's items together, as the show's own edits do",
        context=context,
    )


def sorted_keys(keys: Iterable[ItemKey]) -> tuple[ItemKey, ...]:
    """Sorts item keys by ItemKey.sort_key()."""
    return tuple(sorted(keys, key=ItemKey.sort_key))


def guarded[T](keys: Iterable[ItemKey], build: Callable[[], T]) -> T:
    """Runs one part's build, turning a model refusal into a refusal of the part's items."""
    try:
        return build()
    except E8sError as error:
        raise RefusedError(Refusal(keys=sorted_keys(keys), error=error)) from error


def as_object(value: JsonValue) -> Fields:
    """Reads an item value as an object of fields."""
    if isinstance(value, dict):
        return value
    raise incoherent("an item value is not an object", {"value": value})


def as_texts(value: JsonValue) -> list[str]:
    """Reads an item value as a list of IDs."""
    found = [part for part in value if isinstance(part, str)] if isinstance(value, list) else []
    if isinstance(value, list) and len(found) == len(value):
        return found
    raise incoherent("an item value is not a list of IDs", {"value": value})


def as_text(value: JsonValue) -> str:
    """Reads a field value as one ID."""
    return as_texts([value])[0]


def as_paths(value: JsonValue) -> list[ItemPath]:
    """Reads the pin order as a list of pin paths."""
    if isinstance(value, list):
        return [tuple(as_texts(part)) for part in value]
    raise incoherent("the pin order is not a list of pin paths", {"value": value})


def all_removed(entries: Entries) -> bool:
    """Reports whether every given item is left out."""
    return all(value is None for value in entries.values())


def of_kinds(values: ItemValues, kinds: Iterable[ItemKind]) -> Entries:
    """Picks the item values of some kinds."""
    wanted = frozenset(kinds)
    return {key: value for key, value in values.items() if key.kind in wanted}


def by_part(entries: Entries, part: int) -> dict[str, Entries]:
    """Groups item values by one part of their path, such as a set key or a spot ID."""
    grouped: dict[str, Entries] = {}
    for key, value in entries.items():
        grouped.setdefault(key.path[part], {})[key] = value
    return grouped


def with_members[K: str, T](
    current: Mapping[K, T], entries: Mapping[str, JsonValue], build: Callable[[str, JsonValue], T]
) -> dict[str, T]:
    """Applies item values to a mapping of members: None removes one, a value rebuilds it."""
    members: dict[str, T] = dict(current.items())
    for name, value in entries.items():
        if value is None:
            members.pop(name, None)
        else:
            members[name] = build(name, value)
    return members


def named_values(entries: Mapping[ItemKey, JsonValue], kind: ItemKind) -> dict[str, JsonValue]:
    """Keys the item values of one kind by the last part of their path, in key order."""
    return {key.path[-1]: entries[key] for key in sorted_keys(k for k in entries if k.kind == kind)}


def in_order[K, T](order: Sequence[K], members: Mapping[K, T], what: str) -> tuple[T, ...]:
    """Lists members in an order item's order, refusing an order that disagrees with them."""
    stray = sorted(map(str, members.keys() - set(order)))
    missing = sorted(map(str, set(order) - members.keys()))
    if stray or missing or len(set(order)) != len(order):
        context: dict[str, JsonValue] = {"unordered": list(stray), "unknown": list(missing)}
        raise incoherent(f"the {what} order disagrees with the {what} items", context)
    return tuple(members[name] for name in order)


def assembly_context(base: Show, values: ItemValues) -> AssemblyContext:
    """Builds the context of applying item values to a base show: the set ID of each set key."""
    old = {key: set_id for set_id, key in set_keys_by_id(base).items()}
    ids = dict(old)
    for key, value in values.items():
        if key.kind != "set" or key.role is not None:
            continue
        if value is None:
            ids.pop(key.path[0], None)
        else:
            ids[key.path[0]] = as_text(as_object(value)[ID_FIELD])
    renamed = frozenset(key for key, set_id in ids.items() if key in old and old[key] != set_id)
    return AssemblyContext(base=base, values=values, ids=ids, renamed=renamed)
