#!/usr/bin/env python3

"""
selection.py --- Selection, the ordered duplicate-free set of spot IDs, and the Selector alias

Contains:
    Selection: holds ordered unique IDs and supports |, &, -, len, iteration and `in`
    Selector: names anything that picks spots: an expression, a list of IDs or a Selection
    SelectorField: names the JSON-safe form of Selector stored on models
"""

from collections.abc import Iterator, Sequence
from typing import Self

from pydantic import model_validator

from stage.core.types import FrozenModel


class Selection(FrozenModel):
    """Holds an ordered, duplicate-free set of spot IDs.

    Attributes:
        ids: Spot IDs in selection order.
        description: How the selection was made, for display.
    """

    ids: tuple[str, ...]
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _dedupe(cls, values: object) -> object:
        if isinstance(values, dict) and "ids" in values:
            values = {**values, "ids": tuple(dict.fromkeys(values["ids"]))}
        return values

    def __or__(self, other: Self) -> "Selection":
        return Selection(
            ids=self.ids + other.ids, description=f"{self.description}+{other.description}"
        )

    def __and__(self, other: Self) -> "Selection":
        keep = set(other.ids)
        return Selection(
            ids=tuple(i for i in self.ids if i in keep),
            description=f"{self.description}&{other.description}",
        )

    def __sub__(self, other: Self) -> "Selection":
        drop = set(other.ids)
        return Selection(
            ids=tuple(i for i in self.ids if i not in drop),
            description=f"{self.description}-{other.description}",
        )

    def __len__(self) -> int:
        return len(self.ids)

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self.ids)

    def __contains__(self, item: object) -> bool:
        return item in self.ids


type Selector = str | Sequence[str] | Selection
type SelectorField = str | tuple[str, ...] | Selection
