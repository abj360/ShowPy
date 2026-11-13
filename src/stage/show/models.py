#!/usr/bin/env python3

"""
models.py --- the Show aggregate, its index, and the storage, history and collaboration records

Contains:
    Pin: records one designer lock on a spot, form, set or transition
    Show: holds a whole show: sheet, cast, timeline, sets, forms, paths, notes and zones
    Show.set(): returns a set by ID
    Show.set_index(): returns a set's position in the show
    Show.set_ids(): returns set IDs in show order
    Show.transition_ids(): returns consecutive set pairs
    Show.transition(): returns a transition's paths, defaulting to straight moves
    ShowIndexEntry: holds one set's place on the clock: counts, start, measure, seconds, cue
    ShowIndex: looks up sets and transitions by global count
    SetDiff: lists what differs between two sets
    PathDiff: lists what differs between two transitions
    Revision: records one saved state of a show in storage
    Comment: holds one review comment anchored on the canvas
    PlacementOp: replaces one spot's placement in one set
    FormOp: replaces or adds one form
    PathsOp: replaces one transition's stored paths
    CountsOp: retimes one set
    CanvasEvent: records one edit or pointer from either side of the canvas
    Proposal: holds an agent suggestion the designer accepts or rejects
    ProposalPreview: holds the ghost placements and paths a proposal would produce
"""

import bisect
import itertools
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from stage.cast.models import Cast
from stage.choreo.models import FacingOverride, Form, Placement, Set, TransitionSpec
from stage.core.errors import SchemaError, TimeRangeError, UnknownRefError
from stage.core.ids import FormId, RouteId, SetId, SpotId, TransitionId
from stage.core.ids import format_tid as _format_tid
from stage.core.types import Count, FrozenModel, Seconds
from stage.core.util import close_matches
from stage.notes.models import Anchor, Note
from stage.sheet.models import CoordinateSystem, Sheet, Zone
from stage.time.models import MeasureLabel, Timeline

INDEX_TOL = 1e-9

type PinTarget = Literal["entity", "form", "set", "transition"]
type Principal = Literal["agent", "designer", "system"]
type RevisionKind = Literal["edit", "snapshot", "revert", "undo", "redo"]
type EventKind = Literal[
    "move", "edit_form", "pin", "unpin", "edit_paths", "comment", "set_counts", "highlight"
]
type ProposalState = Literal["open", "accepted", "rejected"]


class Pin(FrozenModel):
    """Records one designer lock; the flags it sets live on placements, forms and path rows.

    Attributes:
        target: What is locked.
        id: Spot, form or set ID, or a transition written as `a→b`.
        by: Who pinned it.
        reason: Why, shown to the agent when an edit is refused.
    """

    target: PinTarget
    id: str
    by: str = "designer"
    reason: str = ""


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
        pins: Designer locks with their reasons.
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
    pins: tuple[Pin, ...] = ()
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


class ShowIndexEntry(FrozenModel):
    """Holds one set's place on the show clock.

    Attributes:
        set: Set ID.
        counts: Counts of the move into the set.
        start_count: Global count where the set is reached.
        measure: Measure label of the start count; None when measures are off.
        seconds: Show seconds of the start count, holds included.
        cue: The set's cue or rehearsal mark.
    """

    set: SetId
    counts: float
    start_count: Count
    measure: MeasureLabel | None = None
    seconds: Seconds
    cue: str | None = None


class ShowIndex(FrozenModel):
    """Looks up sets and transitions by global count.

    Count `c` is AT set `s` when `c` equals the start count of `s`; it lies IN transition
    `(a, b)` when `start(a) < c < start(b)`. Exactly one of the two holds on `[0, total]`.

    Attributes:
        entries: One entry per set, in show order.
    """

    entries: tuple[ShowIndexEntry, ...]

    @property
    def total_counts(self) -> Count:
        """Returns the global count of the last set; 0 for an empty show."""
        return self.entries[-1].start_count if self.entries else 0.0

    def _starts(self) -> list[Count]:
        return [entry.start_count for entry in self.entries]

    def _require_in_show(self, count: Count) -> None:
        if not self.entries or count < -INDEX_TOL or count > self.total_counts + INDEX_TOL:
            raise TimeRangeError(
                f"count {count} is outside the show",
                hint=f"use a count from 0 to {self.total_counts}",
                context={"count": count, "total": self.total_counts},
            )

    def set_at(self, count: Count) -> SetId | None:
        """Returns the set reached exactly at `count`, or None while moving between sets.

        >>> rows = (("1", 0.0), ("2", 8.0))
        >>> idx = ShowIndex(entries=tuple(
        ...     ShowIndexEntry(set=i, counts=s, start_count=s, seconds=0) for i, s in rows))
        >>> idx.set_at(8.0), idx.set_at(3.0)
        ('2', None)
        """
        self._require_in_show(count)
        starts = self._starts()
        position = bisect.bisect_left(starts, count - INDEX_TOL)
        if position < len(starts) and abs(starts[position] - count) <= INDEX_TOL:
            return self.entries[position].set
        return None

    def transition_at(self, count: Count) -> tuple[TransitionId, Count] | None:
        """Returns the transition `count` lies in plus its local count tau, or None at a set.

        >>> rows = (("1", 0.0), ("2", 8.0))
        >>> idx = ShowIndex(entries=tuple(
        ...     ShowIndexEntry(set=i, counts=s, start_count=s, seconds=0) for i, s in rows))
        >>> idx.transition_at(3.0)
        (('1', '2'), 3.0)
        """
        if self.set_at(count) is not None:
            return None
        position = bisect.bisect_right(self._starts(), count) - 1
        before, after = self.entries[position], self.entries[position + 1]
        return (before.set, after.set), count - before.start_count

    def _position(self, set_id: SetId) -> int:
        ids = [entry.set for entry in self.entries]
        if set_id not in ids:
            close = close_matches(set_id, ids)
            raise UnknownRefError(
                f"unknown set {set_id!r}",
                hint=f"did you mean {', '.join(close)}?" if close else "check the set id",
                context={"set": set_id},
            )
        return ids.index(set_id)

    def next(self, set_id: SetId) -> SetId | None:
        """Returns the set after `set_id`, or None for the last set."""
        position = self._position(set_id) + 1
        return self.entries[position].set if position < len(self.entries) else None

    def prev(self, set_id: SetId) -> SetId | None:
        """Returns the set before `set_id`, or None for the first set."""
        position = self._position(set_id) - 1
        return self.entries[position].set if position >= 0 else None


class SetDiff(FrozenModel):
    """Lists what differs between two sets; every field is sorted naturally.

    Attributes:
        moved: Spots whose position or on-field state changed.
        facing_changed: Spots whose facing changed.
        added: Spots placed only in the second set.
        removed: Spots placed only in the first set.
        forms_changed: Forms added, removed or re-membered between the sets.
    """

    moved: tuple[SpotId, ...] = ()
    facing_changed: tuple[SpotId, ...] = ()
    added: tuple[SpotId, ...] = ()
    removed: tuple[SpotId, ...] = ()
    forms_changed: tuple[FormId, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Reports whether the sets match."""
        return not any(self.model_dump().values())


class PathDiff(FrozenModel):
    """Lists what differs between two transitions; a missing row equals a default row.

    Attributes:
        changed_rows: Spots whose effective path row changed.
        routes_changed: Routes added, removed or edited.
    """

    changed_rows: tuple[SpotId, ...] = ()
    routes_changed: tuple[RouteId, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Reports whether the transitions match."""
        return not (self.changed_rows or self.routes_changed)


class Revision(FrozenModel):
    """Records one saved state of a show; the ID hashes the parent plus the change.

    Attributes:
        id: Content hash of parent, change and metadata.
        parent: Previous revision; empty for the first.
        at: When it was saved; passed in, never read from the clock.
        actor: Who saved it.
        message: What the change was for.
        changed: Workspace paths written or deleted, sorted.
        kind: Plain edit, empty snapshot, revert, undo or redo.
        target: Revision an undo, redo or revert acts on.
    """

    id: str
    parent: str
    at: datetime
    actor: Principal
    message: str
    changed: tuple[str, ...] = ()
    kind: RevisionKind = "edit"
    target: str | None = None


class Comment(FrozenModel):
    """Holds one review comment on the canvas.

    Attributes:
        id: Comment ID.
        anchor: What the comment points at.
        text: Comment body.
        resolved: Whether the discussion is closed.
        author: Who wrote it.
    """

    id: str
    anchor: Anchor
    text: str
    resolved: bool = False
    author: str = "designer"


class PlacementOp(FrozenModel):
    """Replaces one spot's placement in one set.

    Attributes:
        op: Tag.
        set_id: Set edited.
        placement: New placement; its entity names the spot.
    """

    op: Literal["placement"] = "placement"
    set_id: SetId
    placement: Placement


class FormOp(FrozenModel):
    """Replaces or adds one form.

    Attributes:
        op: Tag.
        form: New form; its set must exist.
    """

    op: Literal["form"] = "form"
    form: Form


class PathsOp(FrozenModel):
    """Replaces one transition's stored paths.

    Attributes:
        op: Tag.
        spec: New paths; counts are taken from the destination set.
    """

    op: Literal["paths"] = "paths"
    spec: TransitionSpec


class CountsOp(FrozenModel):
    """Retimes one set.

    Attributes:
        op: Tag.
        set_id: Set retimed.
        counts: New counts.
        rescale: Whether stored path windows scale with the counts.
    """

    op: Literal["counts"] = "counts"
    set_id: SetId
    counts: float
    rescale: bool = False


type ChangeOp = Annotated[PlacementOp | FormOp | PathsOp | CountsOp, Field(discriminator="op")]


class CanvasEvent(FrozenModel):
    """Records one edit or pointer from either side of the canvas.

    Edit payloads are dumped ops (`PlacementOp`, `FormOp`, `PathsOp`, `CountsOp`); pin and
    unpin payloads are dumped `Pin`s; comment payloads are dumped `Comment`s.

    Attributes:
        id: Event ID.
        at: When it happened.
        actor: Who made it.
        kind: What happened.
        payload: JSON body for the kind.
        revision: Revision that holds the edit; empty for pointers such as highlights.
    """

    id: str
    at: datetime
    actor: Principal
    kind: EventKind
    # Any: payloads are JSON objects whose fields depend on the kind.
    payload: dict[str, Any]
    revision: str = ""


class Proposal(FrozenModel):
    """Holds an agent suggestion; the designer accepts or rejects it.

    Attributes:
        id: Proposal ID.
        base_revision: Revision the change was made against.
        change: Ops applied in order when accepted.
        note: Why the agent suggests it.
        state: open, accepted or rejected.
        reason: Why it was rejected.
    """

    id: str
    base_revision: str
    change: tuple[ChangeOp, ...]
    note: str = ""
    state: ProposalState = "open"
    reason: str = ""


class ProposalPreview(FrozenModel):
    """Holds what a proposal would produce, for drawing ghosts on the canvas.

    Attributes:
        proposal_id: Proposal previewed.
        placements: Set ID to the placements that would change in that set.
        paths: Transitions whose effective paths would change, as they would be.
        counts: Set ID to new counts.
        forms: Forms that would be added or changed.
        blocked: Pinned or locked items the proposal touches; accepting it would fail.
    """

    proposal_id: str
    placements: dict[SetId, tuple[Placement, ...]] = {}
    paths: tuple[TransitionSpec, ...] = ()
    counts: dict[SetId, float] = {}
    forms: tuple[Form, ...] = ()
    blocked: tuple[str, ...] = ()
