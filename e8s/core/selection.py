"""
selection.py --- Selection, the ordered duplicate-free set of spot IDs, and the Selector alias

Contains:
    Selection: holds ordered unique IDs and supports |, &, -, len and `in`; equal by IDs
    operand_ids(): lists the IDs of a Selection or any iterable of IDs, refusing a bare string
    Selector: names anything that picks spots: an expression, a list of IDs or a Selection
    SelectorField: names the JSON-safe form of Selector stored on models
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Self

from pydantic import model_validator

from e8s.core.errors import SchemaError
from e8s.core.ids import SpotId
from e8s.core.types import FrozenModel
from e8s.core.util import natural_sort

type Operand = Iterable[str] | Selection


class Selection(FrozenModel):
    """Holds an ordered, duplicate-free set of spot IDs.

    Attributes:
        ids: Spot IDs in selection order.
        description: How the selection was made, for display.
    """

    ids: tuple[SpotId, ...]
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _dedupe(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        ids = values.get("ids")
        if isinstance(ids, list | tuple) and all(isinstance(i, str) for i in ids):
            return {**values, "ids": tuple(dict.fromkeys(ids))}
        return values

    def __or__(self, other: Operand) -> "Selection":
        theirs = operand_ids(other)
        return Selection(
            ids=self.ids + theirs,
            description=f"{self.description}+{_describe(other, theirs)}",
        )

    def __and__(self, other: Operand) -> "Selection":
        theirs = operand_ids(other)
        keep = set(theirs)
        return Selection(
            ids=tuple(i for i in self.ids if i in keep),
            description=f"{self.description}&{_describe(other, theirs)}",
        )

    def __sub__(self, other: Operand) -> "Selection":
        theirs = operand_ids(other)
        drop = set(theirs)
        return Selection(
            ids=tuple(i for i in self.ids if i not in drop),
            description=f"{self.description}-{_describe(other, theirs)}",
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Selection) and self.ids == other.ids

    def __hash__(self) -> int:
        return hash(self.ids)

    def __len__(self) -> int:
        return len(self.ids)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        """Returns a copy whose updated fields run through validation, so `ids` stay unique.

        Args:
            update: Field values to overwrite; validated like a constructor call.
            deep: Whether to copy nested values.

        Returns:
            selection_copy: A new Selection holding the updated fields.
        """
        duplicate = super().model_copy(deep=deep)
        return duplicate if update is None else duplicate.replace(**update)

    def __contains__(self, item: object) -> bool:
        return item in self.ids


def operand_ids(other: Operand) -> tuple[SpotId, ...]:
    """Lists the IDs of an operand, in order.

    Args:
        other: A Selection or any iterable of IDs.

    Returns:
        ids: The IDs as a tuple, a set in natural order; a bare string raises SchemaError.

    >>> operand_ids(["T1", "T2"])
    ('T1', 'T2')
    """
    if isinstance(other, str):
        raise SchemaError(
            "A selection operand must be a collection of IDs, not one string",
            hint="Pass ('T1', 'T2'), a list, or a Selection; use select() for an expression",
            context={"operand": other},
        )
    if isinstance(other, Selection):
        return other.ids
    if isinstance(other, set | frozenset):
        return tuple(SpotId(i) for i in natural_sort(other))
    return tuple(SpotId(i) for i in other)


def _describe(other: Operand, ids: tuple[SpotId, ...]) -> str:
    return other.description if isinstance(other, Selection) else ",".join(ids)


type Selector = str | Sequence[str] | Selection
type SelectorField = str | tuple[SpotId, ...] | Selection
