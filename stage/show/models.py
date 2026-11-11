#!/usr/bin/env python3

"""
models.py --- the Show aggregate and its derived lookups

Contains:
    Show: holds a whole show: sheet, cast, timeline, sets, forms, paths, notes and zones
    Show.set(): returns a set by ID
    Show.set_index(): returns a set's position in the show
    Show.set_ids(): returns set IDs in show order
    Show.transition_ids(): returns consecutive set pairs
    Show.transition(): returns a transition's paths, defaulting to straight moves
"""

import itertools

from pydantic import model_validator

from stage.cast.models import Cast
from stage.choreo.models import FacingOverride, Form, Set, TransitionSpec
from stage.core.errors import SchemaError, UnknownRefError
from stage.core.ids import FormId, SetId, TransitionId
from stage.core.ids import format_tid as _format_tid
from stage.core.types import FrozenModel
from stage.core.util import close_matches
from stage.notes.models import Note
from stage.sheet.models import CoordinateSystem, Sheet, Zone
from stage.time.models import Timeline


class Show(FrozenModel):
    """Holds a whole show.

    Transitions are stored sparsely: a consecutive pair without a stored spec moves every
    spot straight over the destination set's counts.

    Attributes:
        id: Show ID.
        title: Display title.
        pack: Domain pack key; empty when no pack applies.
        sheet: The 2D design surface.
        coordinates: How positions are labeled; display only.
        notes: Design notes.
        cast: Groups and spots.
        timeline: Show clock.
        sets: Sets in show order.
        forms: Form ID to form, across every set.
        transitions: Stored path instructions for consecutive set pairs.
        facing_overrides: Facings forced over global count ranges.
        zones: Show-specific zones, on top of the sheet's.
        dependencies: Sheet reference (without version) to pinned version.
        revision: Storage revision this show was loaded at.
    """

    id: str
    title: str
    pack: str = ""
    sheet: Sheet
    coordinates: CoordinateSystem = CoordinateSystem()
    notes: tuple[Note, ...] = ()
    cast: Cast = Cast()
    timeline: Timeline
    sets: tuple[Set, ...] = ()
    forms: dict[FormId, Form] = {}
    transitions: tuple[TransitionSpec, ...] = ()
    facing_overrides: tuple[FacingOverride, ...] = ()
    zones: tuple[Zone, ...] = ()
    dependencies: dict[str, str] = {}
    revision: str = ""

    @model_validator(mode="after")
    def _unique_set_ids(self) -> "Show":
        ids = self.set_ids()
        if len(set(ids)) != len(ids):
            raise SchemaError("set ids repeat", hint="give every set a unique id")
        return self

    def set_ids(self) -> list[SetId]:
        """Returns set IDs in show order."""
        return [s.id for s in self.sets]

    def set_index(self, set_id: SetId) -> int:
        """Returns the position of a set, raising UnknownRefError with suggestions."""
        for index, candidate in enumerate(self.sets):
            if candidate.id == set_id:
                return index
        close = close_matches(set_id, self.set_ids())
        raise UnknownRefError(
            f"unknown set {set_id!r}",
            hint=f"did you mean {', '.join(close)}?"
            if close
            else f"known sets: {', '.join(self.set_ids())}",
            context={"set": set_id},
        )

    def set(self, set_id: SetId) -> Set:
        """Returns a set by ID."""
        return self.sets[self.set_index(set_id)]

    def transition_ids(self) -> list[TransitionId]:
        """Returns consecutive set pairs in show order."""
        ids = self.set_ids()
        return list(itertools.pairwise(ids))

    def transition(self, tid: TransitionId) -> TransitionSpec:
        """Returns stored paths for a consecutive pair, or straight moves when none are stored."""
        if tid not in self.transition_ids():
            raise UnknownRefError(
                f"{_format_tid(tid)} is not a transition between consecutive sets",
                hint="use a pair from show.transition_ids()",
                context={"tid": tid},
            )
        for spec in self.transitions:
            if spec.id == tid:
                return spec
        return TransitionSpec(id=tid, counts=self.set(tid[1]).counts)
