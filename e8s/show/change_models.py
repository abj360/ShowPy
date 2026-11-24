"""
change_models.py --- the records of what changed between two shows and how a merge settled it

Contains:
    ItemKind: names the kinds of show item a change or conflict points at
    ItemKey: identifies one show item by kind, path and role
    ItemChange: holds one item's value before and after
    ChangeSet: holds the changes between two shows in key order
    MergeConflict: holds one item that both sides changed differently
    MergeResult: holds a merged show or the conflicts that stop it
    IdChanges: holds the set and spot ID renames between two shows
"""

import itertools
from collections.abc import Callable, Sequence
from typing import Literal, Protocol, Self

from pydantic import model_validator

from e8s.core.errors import SchemaError
from e8s.core.ids import SetId, SpotId
from e8s.core.types import FrozenModel, JsonAny
from e8s.show.models import Show

type ItemKind = Literal[
    "placement",
    "set",
    "set_order",
    "form",
    "path_row",
    "transition",
    "route",
    "spot",
    "group",
    "selection",
    "pin",
    "facing_override",
    "sheet",
    "timeline",
    "show",
]


class _Ordered(Protocol):
    def __lt__(self, other: Self, /) -> bool: ...


def _require_strictly_increasing[T, K: _Ordered](
    items: Sequence[T], sort_key: Callable[[T], K], *, what: str, hint: str
) -> None:
    keys = [sort_key(item) for item in items]
    for earlier, later in itertools.pairwise(keys):
        if not earlier < later:
            raise SchemaError(
                f"{what} are not sorted and unique",
                hint=hint,
                context={"earlier": repr(earlier), "later": repr(later)},
            )


class ItemKey(FrozenModel):
    """Identifies one show item by kind, path and role.

    Attributes:
        kind: What sort of item it is.
        path: IDs that locate the item, outermost first, such as a set ID then a spot ID.
        role: Which part of the item, when one item has several; None for the whole item, never
            empty text.
    """

    kind: ItemKind
    path: tuple[str, ...]
    role: str | None = None

    @model_validator(mode="after")
    def _require_role_none_or_text(self) -> Self:
        if self.role == "":
            raise SchemaError(
                "an item key role is empty text",
                hint="use None for the whole item, or name the part",
                context={"kind": self.kind, "path": list(self.path)},
            )
        return self

    def sort_key(self) -> tuple[str, tuple[str, ...], str]:
        """Returns the key that orders items: kind, then path, then role with None first.

        >>> from e8s.show.change_models import ItemKey
        >>> ItemKey(kind="placement", path=("2", "T1"), role="drill").sort_key()
        ('placement', ('2', 'T1'), 'drill')

        Returns:
            sort_key: The kind, the path and the role, empty when there is none.
        """
        return (self.kind, self.path, self.role or "")


class ItemChange(FrozenModel):
    """Holds one item's value before and after.

    Attributes:
        key: The item.
        before: Its JSON value in the older show; None when it was added.
        after: Its JSON value in the newer show; None when it was removed.
    """

    key: ItemKey
    before: JsonAny = None
    after: JsonAny = None


class ChangeSet(FrozenModel):
    """Holds the changes between two shows, one per item, in key order.

    Attributes:
        changes: Item changes sorted by `ItemKey.sort_key()` with no item twice.
    """

    changes: tuple[ItemChange, ...] = ()

    @model_validator(mode="after")
    def _require_sorted_unique(self) -> Self:
        _require_strictly_increasing(
            self.changes,
            lambda change: change.key.sort_key(),
            what="changes",
            hint="sort the changes by ItemKey.sort_key() and give each item one change",
        )
        return self


class MergeConflict(FrozenModel):
    """Holds one item that both sides changed in different ways.

    Attributes:
        key: The item.
        base: Its JSON value in the common ancestor; None when absent.
        ours: Its JSON value on our side; None when absent.
        theirs: Its JSON value on their side; None when absent.
        reason: Why the two changes cannot combine.
    """

    key: ItemKey
    base: JsonAny = None
    ours: JsonAny = None
    theirs: JsonAny = None
    reason: str


class MergeResult(FrozenModel):
    """Holds the outcome of a merge: a merged show, or the conflicts that stop it.

    Attributes:
        show: The merged show; None when conflicts remain.
        conflicts: Items both sides changed differently; empty when the merge is clean.
    """

    show: Show | None = None
    conflicts: tuple[MergeConflict, ...] = ()

    @model_validator(mode="after")
    def _require_one_outcome(self) -> Self:
        if (self.show is None) == (not self.conflicts):
            raise SchemaError(
                "a merge result holds a show or conflicts, never both and never neither",
                hint="give `show` with no conflicts for a clean merge, or `conflicts` alone",
            )
        return self


class IdChanges(FrozenModel):
    """Holds the set and spot ID renames between two shows, as (old, new) pairs.

    Attributes:
        sets: Renamed sets, sorted and unique.
        spots: Renamed spots, sorted and unique.
    """

    sets: tuple[tuple[SetId, SetId], ...] = ()
    spots: tuple[tuple[SpotId, SpotId], ...] = ()

    @model_validator(mode="after")
    def _require_sorted_unique(self) -> Self:
        for what, pairs in (("set renames", self.sets), ("spot renames", self.spots)):
            _require_strictly_increasing(
                pairs, lambda pair: pair, what=what, hint="sort the (old, new) pairs, one each"
            )
        return self
