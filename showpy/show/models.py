"""
models.py --- the Show aggregate, its index, and the storage, history and collaboration records

Contains:
    Pin: records one designer lock on a spot, form, set or transition
    Show: holds a whole show: sheet, cast, timeline, sets, forms, paths, notes and zones
    ShowLookup: looks up a show's sets, transitions and forms by ID without scanning
    Show.lookup: returns the show's cached ShowLookup
    Show.set(): returns a set by ID
    Show.set_index(): returns a set's position in the show
    Show.set_ids(): returns set IDs in show order
    Show.transition_ids(): returns consecutive set pairs
    Show.transition_id(): resolves a pair of set IDs to the show's transition
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
    Highlight: points the designer at spots, an area, a point, a set, a transition or a zone
    CanvasEvent: records one edit or pointer from either side of the canvas
    Proposal: holds an agent suggestion the designer accepts or rejects
    ProposalPreview: holds the ghost placements and paths a proposal would produce
"""

import bisect
import itertools
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from functools import cached_property
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from showpy.cast.models import Cast
from showpy.choreo.form_models import Form
from showpy.choreo.models import Placement, Set
from showpy.choreo.path_models import FacingOverride, TransitionSpec
from showpy.core.deprecation import leave_field
from showpy.core.errors import SchemaError, TimeRangeError, UnknownRefError
from showpy.core.ids import FormId, RouteId, SetId, SpotId, TransitionId
from showpy.core.ids import format_tid as _format_tid
from showpy.core.readonly import ReadOnly, ReadOnlyMap
from showpy.core.types import Count, FrozenModel, Seconds
from showpy.core.util import close_matches
from showpy.notes.models import Anchor, Note
from showpy.sheet.models import CoordinateSystem, Sheet, Zone
from showpy.time.models import MeasureLabel, Timeline

INDEX_TOL = 1e-9

type PinTarget = Literal["entity", "form", "set", "transition"]
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


class ShowLookup(FrozenModel):
    """Looks up a show's sets, transitions and forms by ID; built once per Show.

    Attributes:
        positions: Set ID to its position in show order.
        consecutive: Each consecutive set pair to the show's own TransitionId for it.
        stored: TransitionId to its stored spec; the first spec wins when IDs repeat.
        forms: Form ID to form.
    """

    positions: Annotated[Mapping[SetId, int], ReadOnly()]
    consecutive: Annotated[Mapping[TransitionId, TransitionId], ReadOnly()]
    stored: Annotated[Mapping[TransitionId, TransitionSpec], ReadOnly()]
    forms: Annotated[Mapping[FormId, Form], ReadOnly()]


def _no_forms() -> ReadOnlyMap[FormId, Form]:
    return ReadOnlyMap({})


def _no_dependencies() -> ReadOnlyMap[str, str]:
    return ReadOnlyMap({})


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
    forms: Annotated[Mapping[FormId, Form], ReadOnly()] = Field(default_factory=_no_forms)
    transitions: tuple[TransitionSpec, ...] = ()
    facing_overrides: tuple[FacingOverride, ...] = ()
    zones: tuple[Zone, ...] = ()
    dependencies: Annotated[Mapping[str, str], ReadOnly()] = Field(default_factory=_no_dependencies)
    pins: tuple[Pin, ...] = ()
    revision: str = ""

    @model_validator(mode="after")
    def _form_keys_match_ids(self) -> "Show":
        wrong = {key: form.id for key, form in self.forms.items() if key != form.id}
        if wrong:
            raise SchemaError(
                f"form keys differ from form ids: {', '.join(sorted(wrong))}",
                hint="store every form under its own id",
                context={"forms": wrong},
            )
        return self

    @model_validator(mode="after")
    def _unique_set_ids(self) -> "Show":
        ids = self.set_ids()
        if len(set(ids)) != len(ids):
            raise SchemaError("set ids repeat", hint="give every set a unique id")
        keys = [s.key or s.id for s in self.sets]
        folded = Counter(k.casefold() for k in keys)
        # v0.2 stored IDs differing only by case; a key equal to its ID is such a stored ID, so
        # only a group holding a key that differs from its ID is refused. Commits refuse new
        # folders that repeat ignoring case, and add_set() picks keys free ignoring case.
        explicit = {s.key.casefold() for s in self.sets if s.key not in (None, s.id)}
        repeated = sorted(
            {k for k in keys if folded[k.casefold()] > 1 and k.casefold() in explicit}
        )
        if repeated:
            raise SchemaError(
                f"set keys repeat ignoring case: {', '.join(repeated)}",
                hint="give every set a key unique ignoring case; add_set() picks a free one",
                context={"keys": repeated},
            )
        return self

    def set_ids(self) -> list[SetId]:
        """Returns set IDs in show order."""
        return [s.id for s in self.sets]

    @cached_property
    def lookup(self) -> ShowLookup:
        """Returns the show's lookup tables, built on first use and dropped by every copy."""
        ids = self.set_ids()
        positions: dict[SetId, int] = {}
        for position, set_id in enumerate(ids):
            positions.setdefault(set_id, position)
        stored: dict[TransitionId, TransitionSpec] = {}
        for spec in self.transitions:
            stored.setdefault(spec.id, spec)
        pairs = [TransitionId(a, b) for a, b in itertools.pairwise(ids)]
        return ShowLookup.model_construct(
            positions=ReadOnlyMap(positions),
            consecutive=ReadOnlyMap({pair: pair for pair in pairs}),
            stored=ReadOnlyMap(stored),
            forms=ReadOnlyMap(self.forms),
        )

    def set_index(self, set_id: str) -> int:
        """Returns the position of a set, raising UnknownRefError with suggestions."""
        position = self.lookup.positions.get(SetId(set_id))
        if position is not None:
            return position
        close = close_matches(set_id, self.set_ids())
        raise UnknownRefError(
            f"unknown set {set_id!r}",
            hint=f"did you mean {', '.join(close)}?"
            if close
            else f"known sets: {', '.join(self.set_ids())}",
            context={"set": set_id},
        )

    def set(self, set_id: str) -> Set:
        """Returns a set by ID."""
        return self.sets[self.set_index(set_id)]

    def transition_ids(self) -> list[TransitionId]:
        """Returns consecutive set pairs in show order."""
        return list(self.lookup.consecutive.values())

    def transition_id(self, tid: tuple[str, str]) -> TransitionId:
        """Resolves a pair of set IDs to the show's transition between them.

        Args:
            tid: Pair of set IDs, such as `("1", "2")` or a TransitionId.

        Returns:
            transition_id: The show's own TransitionId for that pair.

        >>> from showpy.examples import two_set_show
        >>> str(two_set_show().transition_id(("1", "2")))
        '1→2'
        """
        known = self.lookup.consecutive.get(TransitionId(SetId(tid[0]), SetId(tid[1])))
        if known is not None:
            return known
        raise UnknownRefError(
            f"{_format_tid(tid)} is not a transition between consecutive sets",
            hint="use a pair from show.transition_ids()",
            context={"tid": tid},
        )

    def transition(self, tid: tuple[str, str]) -> TransitionSpec:
        """Returns stored paths for a consecutive pair, or straight moves when none are stored."""
        known = self.transition_id(tid)
        spec = self.lookup.stored.get(known)
        if spec is not None:
            return spec
        return TransitionSpec(id=known, counts=self.set(known.to_set).counts)


leave_field(Show, "coordinates", since="0.4.0", remove="0.7.0", replacement="Location")
leave_field(Show, "zones", since="0.4.0", remove="0.7.0", replacement="Marking")


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
        (TransitionId(from_set='1', to_set='2'), 3.0)
        """
        if self.set_at(count) is not None:
            return None
        position = bisect.bisect_right(self._starts(), count) - 1
        before, after = self.entries[position], self.entries[position + 1]
        return TransitionId(before.set, after.set), count - before.start_count

    def _position(self, set_id: str) -> int:
        ids: list[str] = [entry.set for entry in self.entries]
        if set_id not in ids:
            close = close_matches(set_id, ids)
            raise UnknownRefError(
                f"unknown set {set_id!r}",
                hint=f"did you mean {', '.join(close)}?" if close else "check the set id",
                context={"set": set_id},
            )
        return ids.index(set_id)

    def next(self, set_id: str) -> SetId | None:
        """Returns the set after `set_id`, or None for the last set."""
        position = self._position(set_id) + 1
        return self.entries[position].set if position < len(self.entries) else None

    def prev(self, set_id: str) -> SetId | None:
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
    """Records one saved state of a show: what changed and when, never who or why.

    Attributes:
        id: Content hash of parent, time and change.
        parent: Previous revision; empty for the first.
        at: When it was saved; passed in, never read from the clock.
        changed: Stored paths written or deleted, sorted; set and transition folders are named
            by set key, so they can differ from workspace paths after a renumber.
    """

    id: str
    parent: str
    at: datetime
    changed: tuple[str, ...] = ()


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


class Highlight(FrozenModel):
    """Points the designer at part of the canvas for a while; it changes nothing.

    Attributes:
        anchor: What is pointed at, when it is spots, an area, a point, a set or a transition.
        zone: Zone name, when the pointer is a zone.
        ttl_s: Seconds the pointer stays visible.
    """

    anchor: Anchor | None = None
    zone: str | None = None
    ttl_s: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def _require_one_target(self) -> "Highlight":
        if (self.anchor is None) == (self.zone is None):
            raise SchemaError(
                "highlight needs exactly one of anchor and zone",
                hint="set `anchor` for spots, an area, a point, a set or a transition, "
                "or `zone` for a zone",
            )
        return self


type EventPayload = PlacementOp | FormOp | PathsOp | CountsOp | Pin | Comment | Highlight

EVENT_PAYLOADS: Final[Mapping[str, type[EventPayload]]] = ReadOnlyMap(
    {
        "move": PlacementOp,
        "edit_form": FormOp,
        "edit_paths": PathsOp,
        "set_counts": CountsOp,
        "pin": Pin,
        "unpin": Pin,
        "comment": Comment,
        "highlight": Highlight,
    }
)


class CanvasEvent(FrozenModel):
    """Records one edit or pointer from either side of the canvas.

    The payload type follows the kind: `move` carries a `PlacementOp`, `edit_form` a `FormOp`,
    `edit_paths` a `PathsOp`, `set_counts` a `CountsOp`, `pin` and `unpin` a `Pin`, `comment` a
    `Comment` and `highlight` a `Highlight`. A payload given as a dict is validated as the
    model its kind names; a payload of another kind's model is refused.

    Attributes:
        id: Event ID.
        at: When it happened.
        kind: What happened.
        payload: Typed body for the kind.
        revision: Revision that holds the edit; empty for pointers such as highlights.
    """

    id: str
    at: datetime
    kind: EventKind
    payload: EventPayload
    revision: str = ""

    @model_validator(mode="before")
    @classmethod
    def _type_payload(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        kind, payload = data.get("kind"), data.get("payload")
        model = EVENT_PAYLOADS.get(kind) if isinstance(kind, str) else None
        if model is None or not isinstance(payload, Mapping):
            return data
        return {**data, "payload": model.model_validate(payload)}

    @model_validator(mode="after")
    def _require_payload_of_kind(self) -> "CanvasEvent":
        expected = EVENT_PAYLOADS[self.kind]
        if type(self.payload) is not expected:
            raise SchemaError(
                f"event {self.id} of kind {self.kind} carries a {type(self.payload).__name__}",
                hint=f"give a {expected.__name__} payload for kind {self.kind}",
                context={"event": self.id},
            )
        return self


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
